"""Context Reconstruction Pipeline (CRP) — the deterministic core (P2).

Given a delegation (the in-progress TODO item, the router's briefing, the
support_files), assemble — in pure Python, with ZERO LLM call — a compact
**Context Packet** that is injected into the worker's first message. The worker
then *executes* a pre-built context instead of *reconstructing* it (the whole
point: small models stop guessing).

Best-effort by construction: every slice is wrapped so a failure degrades to an
empty slice and NEVER breaks delegation. Active only when a code-mode git
worktree exists for the conversation (returns "" otherwise) → zero effect on
research/chat/analyse delegations.

Slices (each deterministic, each capped):
  1. Task anchor      — the living TODO goal + in-progress item.
  2. Recent diff      — `git diff` of the worktree (what prior steps changed).
  3. Source           — the support_files, read in cat -n form (read-before-edit anchors).
  4. Lexical (grep)   — ripgrep hits for identifiers named in the briefing.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .todo import load_todo
from .tools import _repo
from .tools._workspace import workspace_root_for

_log = logging.getLogger(__name__)

_TOTAL_CAP = 8000          # hard cap on the whole packet (chars)
_SRC_LINES_CAP = 160       # max lines read per support file
_DIFF_LINES_CAP = 120      # max lines of recent diff
_GREP_HITS_CAP = 12        # max grep hit lines total
_MAX_IDENTS = 8            # identifiers mined from the briefing
_GIT_TIMEOUT_S = 10
_RG_TIMEOUT_S = 10

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Common English / prose tokens that look like identifiers but aren't symbols.
_STOP = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "your", "you",
    "should", "must", "when", "then", "code", "file", "files", "test", "tests",
    "function", "class", "method", "return", "add", "use", "make", "fix", "via",
    "support", "briefing", "expected", "goal", "step", "task", "repo", "write",
    "read", "edit", "run", "value", "data", "list", "dict", "true", "false", "none",
})


def _has_internal_upper(tok: str) -> bool:
    return any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok)


def _identifiers(briefing: str, support_files: list[str]) -> list[str]:
    """Mine likely code identifiers from the briefing + support-file stems."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in _IDENT_RE.findall(briefing or ""):
        low = tok.lower()
        if low in _STOP or low in seen:
            continue
        if "_" in tok or _has_internal_upper(tok):  # snake_case or CamelCase
            out.append(tok)
            seen.add(low)
    for f in support_files or []:
        stem = Path(f).stem
        if stem and stem.lower() not in seen and len(stem) > 2:
            out.append(stem)
            seen.add(stem.lower())
    return out[:_MAX_IDENTS]


def _task_anchor(conv_folder: Path) -> str:
    todo = load_todo(conv_folder)
    if not todo:
        return ""
    goal = (todo.get("goal") or "").strip()
    items = todo.get("items") or []
    cur = next((it for it in items if it.get("status") == "in_progress"), None)
    lines = []
    if goal:
        lines.append(f"Goal: {goal}")
    if cur:
        lines.append(f"Current step: {cur.get('text', '').strip()}")
    return "\n".join(lines)


def _recent_diff(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "--no-pager", "diff"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    if proc.returncode != 0 or not proc.stdout.strip():
        return ""
    lines = proc.stdout.splitlines()
    shown = lines[:_DIFF_LINES_CAP]
    suffix = "\n… (diff truncated)" if len(lines) > _DIFF_LINES_CAP else ""
    return "\n".join(shown) + suffix


def _source_slice(
    conv_folder: Path, root: Path, support_files: list[str], ws_root: Path | None = None
) -> str:
    blocks: list[str] = []
    for rel in (support_files or [])[:3]:
        # A support_file is either a WORKSPACE handoff artifact (a previous
        # specialist's findings) — checked first — or a repo SOURCE file. Read it
        # from wherever it lives ; only repo files get a read-before-edit anchor.
        target: Path | None = None
        in_repo = False
        if ws_root is not None:
            with contextlib.suppress(ValueError):
                cand = _repo.safe_resolve(ws_root, rel)
                if cand.exists() and cand.is_file():
                    target = cand
        if target is None:
            with contextlib.suppress(ValueError):
                cand = _repo.safe_resolve(root, rel)
                if cand.exists() and cand.is_file():
                    target, in_repo = cand, True
        if target is None:
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        base = root if in_repo else ws_root
        canonical = target.relative_to(base.resolve()).as_posix()
        lines = content.splitlines()
        head = "\n".join(lines[:_SRC_LINES_CAP])
        if in_repo:
            # Record the read so the worker can repo_edit this file without an extra round.
            with contextlib.suppress(OSError):
                _repo.mark_read(conv_folder, canonical, target.stat().st_mtime_ns)
        more = f"\n… ({len(lines) - _SRC_LINES_CAP} more lines)" if len(lines) > _SRC_LINES_CAP else ""
        label = canonical if in_repo else f"workspace:{canonical}"
        blocks.append(f"### {label}\n{_repo.cat_n(head)}{more}")
    return "\n\n".join(blocks)


def _grep_slice(root: Path, identifiers: list[str]) -> str:
    if not identifiers or shutil.which("rg") is None:
        return ""
    pattern = "|".join(re.escape(i) for i in identifiers)
    try:
        proc = subprocess.run(
            ["rg", "--color=never", "--line-number", "--no-heading", "-e", pattern],
            cwd=str(root), capture_output=True, text=True, timeout=_RG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        return ""
    return "\n".join(proc.stdout.splitlines()[:_GREP_HITS_CAP])


def build_context_packet(
    conv_folder: Path,
    *,
    briefing: str = "",
    support_files: list[str] | None = None,
) -> str:
    """Assemble the deterministic Context Packet, or "" when not applicable.

    Returns "" when there is no code worktree for the conversation (i.e. not
    code mode) so the caller can append unconditionally.
    """
    root = _repo.worktree_root(conv_folder)
    if root is None:
        return ""
    support_files = support_files or []
    idents = _identifiers(briefing, support_files)

    sections: list[tuple[str, str]] = []
    try:
        sections.append(("Task", _task_anchor(conv_folder)))
        sections.append(("Recent changes (git diff)", _recent_diff(root)))
        ws_root = workspace_root_for(conv_folder)
        sections.append(
            ("Source (support files)", _source_slice(conv_folder, root, support_files, ws_root))
        )
        sections.append(("Lexical hits (grep)", _grep_slice(root, idents)))
    except Exception as exc:  # noqa: BLE001 — CRP must never break a delegation
        _log.warning("context_packet assembly error: %s", exc)

    body_parts = [f"## {title}\n{text.strip()}" for title, text in sections if text and text.strip()]
    if not body_parts:
        return ""
    packet = (
        "# Reconstructed context (assembled deterministically — read before acting)\n\n"
        + "\n\n".join(body_parts)
    )
    if len(packet) > _TOTAL_CAP:
        packet = packet[:_TOTAL_CAP] + "\n… (context truncated)"
    return packet

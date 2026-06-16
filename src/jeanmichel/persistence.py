"""Disk persistence: writes artifacts (prompts, thoughts, briefings, …) with frontmatter.

Also exposes the v2 persistence layer for the new orchestrator
(cf. DevNotes/REVOLUCION/06_proposition_v2.md §6 et §6 bis) :

- `messages.json` — Ollama-shape array, source of the main agent's runtime state.
- `state.json` — scalar counters snapshot (budget, depth, search calls).
- `events.jsonl` — append-only event log.
- `subagent_<request_id>.json` — per-subagent messages array.

All writes are atomic (write-to-temp + rename) so a crash mid-write leaves
the previous valid version on disk.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _frontmatter(conversation_id: str, request_id: str, agent: str, kind: str) -> str:
    utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "---\n"
        f"conversation_id: {conversation_id}\n"
        f"request_id: {request_id}\n"
        f"agent: {agent}\n"
        f"kind: {kind}\n"
        f"utc: {utc}\n"
        "---\n\n"
    )


def _hhmmssmmm() -> str:
    now = datetime.now(UTC)
    return now.strftime("%H%M%S") + f"{now.microsecond // 1000:03d}"


def write_artifact(conv_folder: Path, *, conversation_id: str, request_id: str,
                   agent: str, kind: str, body: str) -> str:
    """Write an artifact file. Returns its relative path inside conv_folder."""
    filename = f"{_hhmmssmmm()}_{agent}_{kind}.md"
    path = conv_folder / filename
    path.write_text(_frontmatter(conversation_id, request_id, agent, kind) + body, encoding="utf-8")
    return filename


def append_to_journal(conv_folder: Path, line: str) -> None:
    """Append a human-readable line to conversation.md."""
    journal = conv_folder / "conversation.md"
    if not journal.exists():
        journal.write_text("# Conversation journal\n\n", encoding="utf-8")
    with open(journal, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def conversation_folder_name(conv_id: str, started_at_utc: datetime) -> str:
    return started_at_utc.strftime("%Y-%m-%d_%H-%M") + f"_{conv_id}"


# =============================================================================
# v2 — persistence layer
# =============================================================================
#
# Three files per conversation folder + per-subagent files :
#   messages.json                — main agent's full messages[] array
#   state.json                   — ConversationState scalars
#   events.jsonl                 — append-only journal of typed events
#   subagent_<request_id>.json   — one per subagent execution
#
# All writes are atomic. `events.jsonl` uses fcntl.flock for append safety.


_MESSAGES_FILE = "messages.json"
_STATE_FILE = "state.json"
_EVENTS_FILE = "events.jsonl"


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomic file write : tempfile in same dir + os.replace.

    POSIX `rename` is atomic — either the new content is fully on disk under
    the target path, or the previous content (if any) is still there. No
    half-written state. Tempfile lives in the same directory to keep the
    rename within a single filesystem (cross-fs rename is not atomic).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in the same dir as the target.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best effort cleanup if rename failed.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# Transient `role:user` prompt-assembly injections — re-added fresh each turn by
# the hooks/orchestrator. They are NOT conversation history: never persist them
# (they'd bloat messages.json + render as fake user bubbles in the web UI on
# reload). The `]` on `[ORCHESTRATOR]` is deliberate — it matches the one-shot
# nudges but NOT the compaction summaries `[ORCHESTRATOR CONTEXT COLLAPSE]` /
# `[ORCHESTRATOR AUTOCOMPACT]`, which ARE real (compacted) history and must stay.
_TRANSIENT_USER_PREFIXES = ("[TODO-RECAP]", "[CODE-REPO]", "[ORCHESTRATOR]", "[PLAN]")


def _is_transient_injection(m: dict[str, Any]) -> bool:
    if m.get("role") != "user":
        return False
    content = m.get("content")
    return isinstance(content, str) and content.lstrip().startswith(_TRANSIENT_USER_PREFIXES)


def save_messages(conv_folder: Path, messages: list[dict[str, Any]]) -> None:
    """Atomic write of `messages.json` (main agent messages[]).

    Drops transient prompt-assembly injections (TODO/repo recaps, orchestrator
    nudges) — they are re-injected fresh each turn and must never become history.
    Strips any transient ``images`` (base64 vision input) so the conversation file
    stays text-only — images live in the workspace, never in messages.json (cf.
    DevNotes/WEBUI/03). The in-memory list is untouched (the current turn still
    sees the injections); only the persisted copy is sanitized.
    """
    path = conv_folder / _MESSAGES_FILE
    sanitized = [
        {k: v for k, v in m.items() if k != "images"} if "images" in m else m
        for m in messages
        if not _is_transient_injection(m)
    ]
    _atomic_write_text(path, json.dumps(sanitized, ensure_ascii=False, indent=2))


def load_messages(conv_folder: Path) -> list[dict[str, Any]]:
    """Load `messages.json`. Returns [] if absent."""
    path = conv_folder / _MESSAGES_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_sub_messages(
    conv_folder: Path, request_id: str, sub_messages: list[dict[str, Any]]
) -> None:
    """Atomic write of `subagent_<request_id>.json`."""
    path = conv_folder / f"subagent_{request_id}.json"
    _atomic_write_text(path, json.dumps(sub_messages, ensure_ascii=False, indent=2))


def load_sub_messages(conv_folder: Path, request_id: str) -> list[dict[str, Any]]:
    """Load a subagent's messages[]. Returns [] if absent."""
    path = conv_folder / f"subagent_{request_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(conv_folder: Path, state: Any) -> None:
    """Atomic write of `state.json`. `state` is a ConversationState dataclass."""
    path = conv_folder / _STATE_FILE
    data = dataclasses.asdict(state) if dataclasses.is_dataclass(state) else dict(state)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_state(conv_folder: Path) -> dict[str, Any]:
    """Load `state.json`. Returns {} if absent (caller reconstructs default state)."""
    path = conv_folder / _STATE_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def append_event(conv_folder: Path, event: Any) -> None:
    """Append a single event to `events.jsonl`.

    `event` is either a dataclass with `to_dict()` (the canonical case for the
    11 event types defined in `events.py`) or a raw dict. Uses fcntl.flock
    to serialise concurrent appends from multiple processes — overkill for
    the mono-thread orchestrator but a cheap safety net.
    """
    conv_folder.mkdir(parents=True, exist_ok=True)
    path = conv_folder / _EVENTS_FILE

    if hasattr(event, "to_dict") and callable(event.to_dict):
        payload = event.to_dict()
    elif dataclasses.is_dataclass(event):
        payload = dataclasses.asdict(event)
    elif isinstance(event, dict):
        payload = event
    else:
        raise TypeError(f"Unsupported event type for append_event: {type(event)!r}")

    line = json.dumps(payload, ensure_ascii=False) + "\n"

    # Open in append-binary mode for atomic write of the encoded line.
    # fcntl.LOCK_EX serialises across processes (no-op within one process,
    # but matters if a debugger or sister script reads/writes the file).
    encoded = line.encode("utf-8")
    with open(path, "ab") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(encoded)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def append_sandbox_audit(
    request_id: str,
    command: str,
    exit_code: int | None,
    duration_ms: int,
) -> None:
    """Append one sandbox execution to the cross-conversation audit log.

    Path : ``config.SANDBOX_AUDIT_LOG`` (default ``~/.jean-michel/sandbox_audit.jsonl``).
    Replaces the deprecated ``sandbox_executions`` SQL table (dropped in
    migration 102).

    ``exit_code=None`` means the command was refused before execution
    (allow-list violation, sandbox start failure, timeout).
    """
    from .config import SANDBOX_AUDIT_LOG

    path = SANDBOX_AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "request_id": request_id,
        "command": command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "ab") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_sandbox_audit(limit: int | None = None) -> list[dict[str, Any]]:
    """Read entries from the sandbox audit JSONL. Returns [] if absent.

    With ``limit`` set, returns the LAST `limit` entries (chronological order
    preserved). Used by self_inspect to surface recent activity.
    """
    from .config import SANDBOX_AUDIT_LOG

    path = SANDBOX_AUDIT_LOG
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None and len(out) > limit:
        out = out[-limit:]
    return out


def load_events(conv_folder: Path) -> list[dict[str, Any]]:
    """Load and parse all events from `events.jsonl`. Returns [] if absent.

    Each line is a JSON object including a `type` field. The caller can
    reconstruct dataclasses via `events.event_from_dict(d)` if needed.
    """
    path = conv_folder / _EVENTS_FILE
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


# ---- Anti-drift safety net : rebuild the referent from the event journal ----
# cf. docs/20260616_meaningful_state (Phase 1.6). The organizational state.json is
# MAINTAINED live by the orchestrator ; this reconstructs the SAME organizational
# fields by folding the referent domain events (the journal). The test
# "maintained == reconstructed" proves no inscription site was forgotten. Pure +
# idempotent. The per-turn EPHEMERAL fields (budget/counters/round-trip) are NOT
# journaled → left at defaults (compare only the organizational subset).


def _apply_file(state: Any, path: str, layer: str, produced_by: str | None, plan_id: str | None) -> None:
    """Mirror of orchestrator `_add_file`'s dedup-by-path (last write wins)."""
    if not path:
        return
    for f in state.files:
        if f.get("path") == path:
            f.update({"layer": layer, "produced_by": produced_by, "plan_id": plan_id})
            return
    state.files.append({"path": path, "layer": layer, "produced_by": produced_by, "plan_id": plan_id})


def _derive_phase(state: Any) -> str:
    """Same rule as the live loop : start phase from the last turn's mode, terminal
    phase from its outcome (answered→answered, halted→awaiting_approval, aborted/open
    → the start phase). No requests yet → idle."""
    if not state.requests:
        return "idle"
    last = state.requests[-1]
    start_phase = "planning" if last.get("mode") == "plan" else "executing"
    outcome = last.get("outcome")
    if outcome == "answered":
        return "answered"
    if outcome == "halted":
        return "awaiting_approval"
    return start_phase  # aborted, or still open (crash mid-turn)


def rebuild_from_events(events: list[dict[str, Any]]) -> Any:
    """Reconstruct the organizational referent (a ConversationState with only the
    organizational fields populated) by folding the referent domain events."""
    from .models import ConversationState

    state = ConversationState()
    for e in events:
        t = e.get("type")
        if t == "RequestOpened":
            state.requests.append({
                "id": e["request_id"], "mode": e["mode"], "plan_id": e["plan_id"],
                "started": e["started"], "ended": None, "outcome": None, "summary": e["summary"],
            })
        elif t == "RequestClosed":
            for r in reversed(state.requests):
                if r["id"] == e["request_id"]:
                    r["ended"] = e["ended"]
                    r["outcome"] = e["outcome"]
                    r["summary"] = e["summary"]
                    r["last_iteration_utc"] = e["last_iteration_utc"]
                    break
        elif t == "PlanInscribed":
            pid = e["plan_id"]
            state.active_plan_id = pid
            entry = state.plans.setdefault(pid, {"plan_file": e["plan_file"], "status": e["status"], "approved": False})
            entry["plan_file"] = e["plan_file"]
            entry["status"] = e["status"]
        elif t == "PlanApprovalChanged":
            if e["plan_id"] in state.plans:
                state.plans[e["plan_id"]]["approved"] = e["approved"]
        elif t == "TodoInscribed":
            tid = e["todo_id"]
            state.active_todo_id = tid
            state.todos[tid] = {
                "plan_id": e["plan_id"], "owner": e["owner"], "file": e["file"],
                "done": e["done"], "total": e["total"], "current_step": e["current_step"],
            }
        elif t == "TodoCleared":
            state.todos.pop(e["todo_id"], None)
            if state.active_todo_id == e["todo_id"]:
                state.active_todo_id = None
        elif t == "FileProduced":
            _apply_file(state, e["path"], e["layer"], e["produced_by"], e["plan_id"])
        elif t == "SubagentInscribed":
            state.subagents.append({
                "request_id": e["request_id"], "agent": e["agent"],
                "parent_request": e["parent_request"], "plan_id": e["plan_id"],
                "confidence": e["confidence"], "files_produced": list(e["files_produced"]),
            })
    state.phase = _derive_phase(state)
    return state

"""Deliberation engine (P5) — DOWNSTREAM validation, grounded in real sources.

critical-coder and sergent-kiss are **validators / controllers, NOT creatives**.
They never propose or design an approach (that drifts/hallucinates on a small model
with nothing concrete to ground on — cf. conv 9f428b47). They VALIDATE a CONCRETE
deliverable already produced — a code diff, or an analysis/audit report — against the
REAL repository (repo_read / repo_grep / repo_glob), flagging any claim not supported
by the code, then gate PASS / REWORK.

Single firing point, gated by a deterministic complexity probe (important phases :
proposed code, analysis reports, audit conclusions) :
  - `validate_deliverable` : 3 angles (grounding / correctness / simplicity) by
    `critical-coder` + a PASS/REWORK gate by `sergent-kiss`. The verdict is attached
    to the result the router sees (`kiss_review`) → handled by its normal PDCA ACT.

Spawning is injected (`spawn`) so the engine is unit-testable and does not import the
orchestrator. Best-effort : if a deliberation agent is unavailable (`spawn` returns
None) the outcome is 'skipped' and the caller proceeds normally.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tools import _repo

_log = logging.getLogger(__name__)

CODE_WORKERS = frozenset({"code-runner", "code-runner-node"})
_DELIVERABLE_CAP_LINES = 150

# A step is "important" enough to validate when it spans files or names structural work.
_HARD_KEYWORDS = (
    "refactor", "redesign", "re-architect", "architecture", "migrate", "migration",
    "rename", "breaking", "across", "multiple files", "rework", "overhaul",
    "restructure", "interface", "protocol", "schema", "concurren", "race condition",
)

# spawn(agent_code, briefing, support_files) -> SubResult-like | None
SpawnFn = Callable[..., Any]


@dataclass
class DeliberationOutcome:
    verdict: str = "skipped"          # "pass" | "rework" | "skipped"
    synthesis: str = ""               # unused (kept for transcript-shape compat)
    critique: str = ""                # concrete fixes when verdict == "rework"
    transcript: list[dict] = field(default_factory=list)


# ---- trigger ---------------------------------------------------------------


def complexity_probe(briefing: str, support_files: list[str]) -> bool:
    """Deterministic 'is this an important deliverable to validate?' heuristic."""
    if len(support_files or []) >= 2:
        return True
    text = (briefing or "").lower()
    return any(k in text for k in _HARD_KEYWORDS)


def current_diff(conv_folder: Path) -> str:
    """The worktree's uncommitted diff (what edits have accumulated), or ""."""
    import subprocess
    root = _repo.worktree_root(conv_folder)
    if root is None:
        return ""
    try:
        r = subprocess.run(["git", "-C", str(root), "--no-pager", "diff"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


# ---- validation briefings (grounded, validator framing) --------------------


def _validate_brief(angle: str, task: str, kind: str, content: str) -> str:
    intro = {
        "grounding": (
            f"ANGLE: GROUNDING.\nVerify EVERY factual claim in the {kind} below against the REAL "
            "repository (repo_read / repo_grep / repo_glob). Flag any file, symbol, or conclusion "
            "that is NOT supported by the actual code. Cite path:line."
        ),
        "correctness": (
            f"ANGLE: CORRECTNESS.\nChecking against the repo, does the {kind} correctly and completely "
            "address the task? Surface gaps, errors, wrong assumptions, missed cases."
        ),
        "simplicity": (
            f"ANGLE: SIMPLICITY.\nIs the {kind} the SIMPLEST thing that solves EXACTLY the task? Flag "
            "over-engineering, speculative generality, anything that was not requested."
        ),
    }[angle]
    return (
        f"{intro}\n\nYou are a VALIDATOR, not a creative: CHECK the deliverable against the sources, "
        f"do NOT redesign or propose your own approach.\n\n## Task\n{task}\n\n## {kind}\n{content}"
    )


def _gate_brief_validate(task: str, kind: str, content: str, reviews: list[tuple[str, str]]) -> str:
    blocks = "\n\n".join(f"### {a}\n{s}" for a, s in reviews)
    return (
        f"Given the reviews, decide whether this {kind} is correct, GROUNDED in the real repo, and the "
        "simplest solution to EXACTLY the task. PASS via report_back(confidence='high' or 'medium'); "
        "REWORK via confidence='low' with precise, concrete fixes in low_confidence_reason.\n\n"
        f"## Task\n{task}\n\n## {kind}\n{content}\n\n## Reviews\n{blocks}"
    )


# ---- helpers ---------------------------------------------------------------


def _t(stage: str, r: Any) -> dict:
    if r is None:
        return {"stage": stage, "agent": "(unavailable)", "confidence": "", "summary": ""}
    return {
        "stage": stage,
        "agent": getattr(r, "agent", "?"),
        "confidence": getattr(r, "confidence", ""),
        "summary": (getattr(r, "summary", "") or "")[:200],
    }


def _verdict_of(gate: Any) -> tuple[str, str]:
    """Map a sergent-kiss report_back to (verdict, critique)."""
    conf = getattr(gate, "confidence", "") or ""
    if conf == "low":
        cuts = getattr(gate, "low_confidence_reason", "") or getattr(gate, "summary", "") or ""
        return "rework", cuts
    return "pass", ""


# ---- engine ----------------------------------------------------------------


def validate_deliverable(
    *, spawn: SpawnFn, task: str, kind: str, content: str, support_files: list[str] | None = None,
) -> DeliberationOutcome:
    """Validate a CONCRETE deliverable (``kind`` = e.g. "diff" or "analysis report") against
    the real repo : 3 angles (grounding / correctness / simplicity) by critical-coder, then a
    PASS/REWORK gate by sergent-kiss. Validators, not creatives. Best-effort (spawn None → skipped)."""
    sf = list(support_files or [])
    capped = "\n".join((content or "").splitlines()[:_DELIVERABLE_CAP_LINES])
    transcript: list[dict] = []
    reviews: list[tuple[str, str]] = []
    for angle in ("grounding", "correctness", "simplicity"):
        r = spawn("critical-coder", _validate_brief(angle, task, kind, capped), sf)
        transcript.append(_t(f"review:{angle}", r))
        if r is None:
            return DeliberationOutcome("skipped", "", "", transcript)
        reviews.append((angle, r.summary or ""))
    gate = spawn("sergent-kiss", _gate_brief_validate(task, kind, capped, reviews), sf)
    transcript.append(_t("sergent-kiss", gate))
    if gate is None:
        return DeliberationOutcome("skipped", "", "", transcript)
    verdict, critique = _verdict_of(gate)
    return DeliberationOutcome(verdict, "", critique, transcript)

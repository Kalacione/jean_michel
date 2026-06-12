"""Dialectic deliberation engine (P5) — the deterministic 'method of reasoning'.

Orchestrated by CODE (not by a dumb LLM deciding to think): on a hard step the
router's delegation is wrapped with a thesis → antithesis → synthesis pass run
by `critical-coder` (one angle per fresh-context spawn), then gated by
`sergent-kiss` (PASS / REWORK via report_back confidence — no new control verb).
A bounded REWORK loop (<=2) lets synthesis incorporate the KISS cuts.

Two firing points (both gated by a deterministic complexity probe):
  - UPSTREAM  `deliberate_approach`: before writing — produce a vetted approach
    that is prepended to the worker's briefing (anti-impasse).
  - DOWNSTREAM `review_diff`: after a code worker edits — review the diff from 3
    angles + KISS gate; the critique is attached to the result the router sees,
    which handles rework through its normal PDCA ACT (no new orchestrator loop).

Spawning is injected (`spawn`) so the engine is unit-testable and does not import
the orchestrator. Best-effort: if the deliberation agents are unavailable
(`spawn` returns None) the outcome is 'skipped' and the caller proceeds normally.
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
MAX_REWORK = 2
_DIFF_CAP_LINES = 150

# A step is "hard" enough to deliberate when it spans files or names structural work.
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
    synthesis: str = ""               # vetted approach (upstream) — "" otherwise
    critique: str = ""                # KISS cuts / review notes when rework
    transcript: list[dict] = field(default_factory=list)


# ---- trigger ---------------------------------------------------------------


def complexity_probe(briefing: str, support_files: list[str]) -> bool:
    """Deterministic 'is this hard enough to deliberate?' heuristic."""
    if len(support_files or []) >= 2:
        return True
    text = (briefing or "").lower()
    return any(k in text for k in _HARD_KEYWORDS)


def should_deliberate(conv_folder: Path, target_code: str, briefing: str, support_files: list[str]) -> bool:
    """Gate: a code worker, in code mode (worktree exists), on a hard step."""
    if target_code not in CODE_WORKERS:
        return False
    if _repo.worktree_root(conv_folder) is None:
        return False
    return complexity_probe(briefing, support_files)


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


# ---- briefings (deterministic angle templates) -----------------------------


def _angle_brief(angle: str, task: str, *, thesis: str = "", antithesis: str = "", rework: str = "") -> str:
    if angle == "thesis":
        return ("ANGLE: THESIS.\nPropose the most direct, concrete approach to this task. "
                "Name the files/functions to touch and the ordered steps.\n\n## Task\n" + task)
    if angle == "antithesis":
        return ("ANGLE: ANTITHESIS.\nAttack the proposed approach below. Where does it break? "
                "Hidden assumptions? Failure modes? Side effects on callers? Is there a simpler "
                "path? Steelman the best alternative.\n\n## Task\n" + task +
                "\n\n## Proposed approach (thesis)\n" + thesis)
    # synthesis
    out = ("ANGLE: SYNTHESIS.\nReconcile the thesis and antithesis into the single best approach — "
           "the SIMPLEST design that survives the critique. Output a concrete, ordered plan "
           "(files, functions, steps).\n\n## Task\n" + task +
           "\n\n## Thesis\n" + thesis + "\n\n## Antithesis\n" + antithesis)
    if rework:
        out += "\n\n## A KISS reviewer asked for these cuts — apply them\n" + rework
    return out


def _review_brief(angle: str, task: str, diff: str) -> str:
    return (f"ANGLE: REVIEW / {angle.upper()}.\nReview the diff below for {angle.replace('_', ' ')}. "
            "Be concrete and cite path:line.\n\n## Task\n" + task + "\n\n## Diff\n" + diff)


def _gate_brief(approach: str, task: str) -> str:
    return ("Decide whether this approach is the SIMPLEST design that solves exactly the task. "
            "PASS via report_back(confidence='high' or 'medium'); REWORK via confidence='low' with "
            "the precise cuts in low_confidence_reason.\n\n## Task\n" + task +
            "\n\n## Proposed approach\n" + approach)


def _gate_brief_review(task: str, diff: str, reviews: list[tuple[str, str]]) -> str:
    blocks = "\n\n".join(f"### {a}\n{s}" for a, s in reviews)
    return ("Decide whether this diff is the SIMPLEST change that solves exactly the task, given the "
            "reviews. PASS via report_back(confidence='high' or 'medium'); REWORK via confidence='low' "
            "with the precise cuts in low_confidence_reason.\n\n## Task\n" + task +
            "\n\n## Diff\n" + diff + "\n\n## Reviews\n" + blocks)


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


def deliberate_approach(
    *, spawn: SpawnFn, task: str, support_files: list[str] | None = None, max_rework: int = MAX_REWORK,
) -> DeliberationOutcome:
    """Thesis → antithesis → synthesis + KISS gate (bounded REWORK). UPSTREAM."""
    sf = list(support_files or [])
    transcript: list[dict] = []

    thesis = spawn("critical-coder", _angle_brief("thesis", task), sf)
    transcript.append(_t("thesis", thesis))
    if thesis is None:
        return DeliberationOutcome("skipped", "", "", transcript)
    antithesis = spawn("critical-coder", _angle_brief("antithesis", task, thesis=thesis.summary), sf)
    transcript.append(_t("antithesis", antithesis))
    synthesis = spawn(
        "critical-coder",
        _angle_brief("synthesis", task, thesis=thesis.summary,
                     antithesis=(antithesis.summary if antithesis else "")), sf,
    )
    transcript.append(_t("synthesis", synthesis))
    approach = synthesis.summary if synthesis else ""

    verdict, critique = "pass", ""
    for attempt in range(max_rework + 1):
        gate = spawn("sergent-kiss", _gate_brief(approach, task), sf)
        transcript.append(_t("sergent-kiss", gate))
        if gate is None:
            break
        verdict, critique = _verdict_of(gate)
        if verdict == "pass" or attempt == max_rework:
            break
        # REWORK: revise the synthesis with the cuts and re-gate.
        synthesis = spawn(
            "critical-coder",
            _angle_brief("synthesis", task, thesis=thesis.summary,
                         antithesis=(antithesis.summary if antithesis else ""), rework=critique), sf,
        )
        transcript.append(_t("synthesis", synthesis))
        if synthesis:
            approach = synthesis.summary

    return DeliberationOutcome(verdict, approach, critique, transcript)


def review_diff(
    *, spawn: SpawnFn, task: str, diff: str, support_files: list[str] | None = None,
) -> DeliberationOutcome:
    """3-angle diff review (correctness / simplicity / side_effects) + KISS gate. DOWNSTREAM."""
    sf = list(support_files or [])
    capped = "\n".join((diff or "").splitlines()[:_DIFF_CAP_LINES])
    transcript: list[dict] = []
    reviews: list[tuple[str, str]] = []
    for angle in ("correctness", "simplicity", "side_effects"):
        r = spawn("critical-coder", _review_brief(angle, task, capped), sf)
        transcript.append(_t(f"review:{angle}", r))
        if r is None:
            return DeliberationOutcome("skipped", "", "", transcript)
        reviews.append((angle, r.summary or ""))
    gate = spawn("sergent-kiss", _gate_brief_review(task, capped, reviews), sf)
    transcript.append(_t("sergent-kiss", gate))
    if gate is None:
        return DeliberationOutcome("skipped", "", "", transcript)
    verdict, critique = _verdict_of(gate)
    return DeliberationOutcome(verdict, "", critique, transcript)

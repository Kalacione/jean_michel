"""Typed events emitted by the v2 orchestrator.

Each event class is an immutable dataclass with:
- A `utc` field (auto-populated at instantiation).
- Domain-specific fields per event type.
- A `to_dict()` method that produces a JSON-serializable dict including the
  event `type` (class name) — ready for `events.jsonl` persistence.

The catalogue is the single source of truth for the orchestrator → CLI
contract (cf. DevNotes/REVOLUCION/06_proposition_v2.md §6 bis and §11 ter C).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert an event dataclass to a JSON-serializable dict with `type` injected."""
    d = dataclasses.asdict(event)
    return {"type": type(event).__name__, **d}


def event_to_jsonl_line(event: Any) -> str:
    """Serialize an event as a single JSONL line (trailing newline included)."""
    return json.dumps(_event_to_dict(event), ensure_ascii=False) + "\n"


# ---- Event dataclasses (13 total, cf. §6 bis doc 06) ---------------------


@dataclass(frozen=True)
class RequestStarted:
    """Emitted at the start of a human turn or of a delegation."""
    agent: str
    depth: int
    briefing_summary: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class LLMCallStarted:
    """Emitted before each LLM call."""
    agent: str
    model: str
    messages_count: int
    working_tokens_used: int
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class LLMCallCompleted:
    """Emitted after each LLM call."""
    tokens_used: int
    tool_call_count: int
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class ToolCallStarted:
    """Emitted just before executing a tool call."""
    agent: str
    tool_name: str
    args_summary: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class ToolCallCompleted:
    """Emitted after a tool call returns."""
    tool_name: str
    result_summary: str
    duration_ms: int
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class DelegationStarted:
    """Emitted when a delegate_to is validated and the subagent spawns."""
    parent_agent: str
    child_agent: str
    depth: int
    child_working_budget: int
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class DelegationCompleted:
    """Emitted when the subagent calls report_back."""
    child_agent: str
    summary: str
    confidence: str  # "low" | "medium" | "high"
    files_produced: list[str] = field(default_factory=list)
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class HookFired:
    """Emitted when a hook takes a visible action (deny, compaction, force-persist)."""
    hook_name: str       # "PreLLMCall" | "PreToolUse" | "PostToolUse" | "OnDelegateReturn"
    action: str          # short action label
    reason: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class WorkingBudgetUpdate:
    """Emitted when working_tokens_used / working_budget crosses a compaction threshold."""
    ratio: float
    compaction_level_triggered: int  # 0 = none, 1 = snip, 2 = microcompact, 3 = collapse, 4 = autocompact
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class MemoryNearCapacity:
    """Emitted when user_memory entry count reaches the warn threshold (90 by default)."""
    current_count: int
    limit: int
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class RequestCompleted:
    """Emitted when an agent produces its final response (assistant turn without tool_calls)."""
    agent: str
    final_content_summary: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class MemoryConsolidationProposed:
    """Emitted by the shadow pass after a turn : grounded memory candidates the
    human can review (accept / edit / extend / drop). Never auto-written."""
    count: int
    candidates: list[dict[str, Any]]
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class AgentThinking:
    """Emitted after an LLM call when the model produced thinking-channel text.

    Powers the web frontend's "thoughts" display. Only emitted when the
    thinking channel is non-empty.
    """
    agent: str
    text: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class AgentTokenStreamed:
    """A streamed token delta forwarded LIVE to the UI as it is generated. `channel`
    distinguishes the model's two streams so the GUI renders them in DIFFERENT places :
    - "thinking" → the dedicated collapsible thinking block (small, auto-fold) ;
    - "content"  → the answer bubble (only the main agent's final answer is streamed).
    Emitted with conv_folder=None so it is NEVER persisted to events.jsonl (it would
    bloat it) — the full text still lands via AgentThinking / the final answer."""
    agent: str
    delta: str
    channel: str = "content"  # "content" | "thinking"
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


# ---- Referent domain events (Phase 1.6) ----------------------------------
#
# A persist-only journal (append_event, NOT _emit) of every mutation to the
# organizational referent (state.json). They are 1:1 with the inscription sites
# and feed `persistence.rebuild_from_events` — the anti-drift SAFETY NET (if a
# site mutates the referent without emitting here, the "maintained == reconstructed"
# test fails). The UI reads the referent via /state, so these are HIDDEN from the
# live trace. All domain fields are REQUIRED (callers pass explicit values incl.
# None) so the event mirrors exactly what was written to the state.


@dataclass(frozen=True)
class RequestOpened:
    """A human turn's entry opened in the referent's turn log (state.requests)."""
    request_id: str
    mode: str            # "plan" | "edit"
    plan_id: str | None
    started: str
    summary: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class RequestClosed:
    """A turn's entry closed with its outcome (post-loop)."""
    request_id: str
    outcome: str | None  # "answered" | "halted" | "aborted"
    summary: str
    ended: str
    last_iteration_utc: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class PlanInscribed:
    """A plan's metadata mirrored into the referent (state.plans[id])."""
    plan_id: str
    plan_file: str
    status: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class PlanApprovalChanged:
    """The human-acceptance flag of a plan flipped (state.plans[id].approved)."""
    plan_id: str
    approved: bool
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class TodoInscribed:
    """A todo tracker's progression mirrored into the referent (state.todos[id])."""
    todo_id: str
    plan_id: str | None
    owner: str
    done: int
    total: int
    current_step: str | None
    file: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class TodoCleared:
    """A todo tracker dropped from the referent (solved / cleared)."""
    todo_id: str
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class FileProduced:
    """A produced file recorded in the referent (state.files), dedup by path."""
    path: str
    layer: str           # "workspace" | "worktree"
    produced_by: str | None
    plan_id: str | None
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class SubagentInscribed:
    """A returned subagent recorded in the referent (state.subagents)."""
    request_id: str
    agent: str
    parent_request: str | None
    plan_id: str | None
    confidence: str
    files_produced: list[str]
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


@dataclass(frozen=True)
class PlanSuperseded:
    """An approved plan was replaced by a re-plan (Phase 2) : the old plan is archived
    (state.plans[old].status='superseded', superseded_by=<new>, plan_file='plan_<old>.md')."""
    plan_id: str          # the OLD plan being superseded
    superseded_by: str    # the NEW plan id
    utc: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _event_to_dict(self)


# ---- Registry for deserialization ----------------------------------------

EVENT_CLASSES: dict[str, type] = {
    "RequestStarted": RequestStarted,
    "LLMCallStarted": LLMCallStarted,
    "LLMCallCompleted": LLMCallCompleted,
    "ToolCallStarted": ToolCallStarted,
    "ToolCallCompleted": ToolCallCompleted,
    "DelegationStarted": DelegationStarted,
    "DelegationCompleted": DelegationCompleted,
    "HookFired": HookFired,
    "WorkingBudgetUpdate": WorkingBudgetUpdate,
    "MemoryNearCapacity": MemoryNearCapacity,
    "RequestCompleted": RequestCompleted,
    "AgentThinking": AgentThinking,
    "AgentTokenStreamed": AgentTokenStreamed,
    "MemoryConsolidationProposed": MemoryConsolidationProposed,
    # Referent domain events (Phase 1.6)
    "RequestOpened": RequestOpened,
    "RequestClosed": RequestClosed,
    "PlanInscribed": PlanInscribed,
    "PlanApprovalChanged": PlanApprovalChanged,
    "TodoInscribed": TodoInscribed,
    "TodoCleared": TodoCleared,
    "FileProduced": FileProduced,
    "SubagentInscribed": SubagentInscribed,
    # Phase 2 : plan multiplicity
    "PlanSuperseded": PlanSuperseded,
}


def event_from_dict(d: dict[str, Any]) -> Any:
    """Reconstruct an event dataclass from its serialized dict form."""
    payload = dict(d)
    event_type = payload.pop("type")
    cls = EVENT_CLASSES.get(event_type)
    if cls is None:
        raise ValueError(f"Unknown event type: {event_type!r}")
    return cls(**payload)


def event_from_jsonl_line(line: str) -> Any:
    """Parse a single JSONL line into an event dataclass."""
    return event_from_dict(json.loads(line))

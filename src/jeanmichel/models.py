"""Runtime dataclasses (DB rows + transient state)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class Agent:
    id: int
    code: str
    name: str
    role: str            # 'router' | 'specialist' | 'finalizer'
    mission: str
    thinking_mode: bool
    temperature: float
    sandbox_image: str | None = None   # override Docker image for bash_sandbox
    model_override: str | None = None  # v2 : per-agent Ollama model (cf. migrate_102)


@dataclass
class Paradigm:
    section_code: str
    category_code: str
    category_title: str
    code: str
    title: str
    content: str
    requires_tool: str | None = None  # only inject when a granted tool starts with this prefix


@dataclass
class Conversation:
    id: str
    folder_path: str
    user_language: str | None
    title: str | None = None
    mode: str = "analyse"
    project_id: int | None = None  # migrate_124, nullable (0 or 1 project)


@dataclass
class ToolCall:
    """Parsed tool call emitted by the model."""
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    thinking: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    corrupted: bool = False
    # v2 additions: token usage reported by Ollama. Zero when unknown
    # (e.g. MockClient or older Ollama versions). Used by hooks to refine
    # token estimates after each call.
    prompt_eval_count: int = 0
    eval_count: int = 0


# ---- v2 conversation runtime state ---------------------------------------
#
# Snapshot of scalar counters tracked by the orchestrator (cf. §6 doc 06).
# Persisted to `state.json` after each iteration of the main loop.
# Not a state machine — just a structured bag of counters that hooks read
# and mutate. `working_tokens_used` is recomputed from `estimate_tokens(messages)
# − system_reserve_tokens` rather than accumulated, so it stays accurate even
# when the compaction shrinks `messages[]`.


@dataclass
class ConversationState:
    system_reserve_tokens: int = 0
    output_reserve_tokens: int = 0
    working_budget: int = 0
    working_tokens_used: int = 0
    depth_current: int = 0
    search_calls_total: int = 0
    search_calls_since_last_persist: int = 0
    stocktake_due: bool = False  # a specialist returned ; the router owes a todo re-eval (code mode)
    active_subagent: str | None = None
    last_iteration_at_utc: str = ""
    plan_mode: bool = False  # PLAN turn : produce a plan, no mutation (gate in PreToolUse) ; propagated to subagents
    # Resumable-subagent round-trip (ask_human) : a specialist that blocked on a human
    # question returned low + "HUMAN INPUT NEEDED:". We remember WHICH subagent (code +
    # its saved trace id) so that, once the router relays the human's answer, we RESUME
    # that exact trace instead of re-spawning a fresh context (no lost work).
    blocked_subagent_code: str | None = None
    blocked_subagent_request_id: str | None = None
    pending_human_answer: str | None = None  # set by ask_human, consumed by the matching re-delegation

    # ---- Organizational referent (cf. docs/20260616_meaningful_state) ----------------
    # These fields PERSIST across turns (reloaded at turn start) — the state IS the ledger :
    # index + statuses + progression + links + pointers, maintained by the orchestrator
    # (deterministic), read directly (no derivation). Everything ABOVE is per-turn ephemeral
    # (budget recomputed ; counters/stocktake/round-trip reset each turn).
    phase: str = "idle"                  # idle | planning | awaiting_approval | executing | answered
    active_plan_id: str | None = None    # which plan is current ("id1"…) ; ≠ plan_mode
    active_todo_id: str | None = None    # current tracker ("t1"…) ; MAY be plan-less
    plans: dict[str, Any] = field(default_factory=dict)    # id → {status, approved, plan_file, todo_id, files[], subagents[], …}
    todos: dict[str, Any] = field(default_factory=dict)    # id → {plan_id|null, owner, status, done, total, current_step, file}
    requests: list[dict[str, Any]] = field(default_factory=list)  # turn log : {id, mode, plan_id, outcome, …}
    lineage: dict[str, Any] = field(default_factory=lambda: {"parent_conv_id": None, "parent_commit": None})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationState:
        """Rebuild from a persisted ``state.json`` dict — tolerant of MISSING keys (legacy /
        partial → defaults) and IGNORES unknown keys (forward-compat). Used to RELOAD the
        referent at turn start ; the caller then recomputes the per-turn ephemeral fields."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def reset_ephemeral(self, *, plan_mode: bool) -> None:
        """Reset the PER-TURN ephemeral fields at the start of a turn (the organizational
        fields above PERSIST). The budget (system/output/working_budget) is recomputed
        separately by the loop's `_initialize_state` ; here we only zero the per-turn
        counters/flags + take the turn's plan_mode. cf. docs/20260616_meaningful_state (le split)."""
        self.depth_current = 0
        self.plan_mode = plan_mode
        self.working_tokens_used = 0
        self.search_calls_total = 0
        self.search_calls_since_last_persist = 0
        self.stocktake_due = False
        self.active_subagent = None
        self.blocked_subagent_code = None
        self.blocked_subagent_request_id = None
        self.pending_human_answer = None

"""Runtime dataclasses (DB rows + transient state)."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    reeval_pending: bool = False  # a specialist returned ; the router owes a todo re-eval (code mode)
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

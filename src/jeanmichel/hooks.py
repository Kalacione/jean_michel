"""Orchestrator hooks (cf. §7 doc 06).

Four callable classes, instantiated once per agent execution scope :

- `PreLLMCall(messages, state)` — runs compaction escalation before each LLM call.
- `PreToolUse(ctx, state, dedup_cache)` — validates a tool_call before exec
  (grant check, depth/whitelist for delegate_to, search budget, contextual
  dedup). Returns a `Decision(deny: bool, reason: str | None)`.
- `PostToolUse(call, result, messages, state, dedup_cache, agent_code)` —
  side effects after exec : counter updates, force-persist nudge, dedup
  cache population.
- `OnDelegateReturn(parent_messages, sub_result, state)` — push the
  structured subagent return as a `role=tool` message in the caller's
  `messages[]`. Validates `low_confidence_reason` when confidence is "low".

The `dedup_cache` is a plain dict owned by the calling main loop — fresh
per agent execution (per `run_main_loop` or per `spawn_subagent` call). The
hooks read and write it; they don't own it.

Tools are referenced by name (string). The orchestrator (Phase 4) is
responsible for resolving tool names to executable callables and for
passing the right context dict to each hook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import worktree
from .compaction import escalate_compaction
from .config import MAX_DEPTH, MAX_SEARCH_CALLS_PER_TURN
from .models import ConversationState, ToolCall
from .todo import RECAP_MARKER, load_todo, render_recap

_log = logging.getLogger(__name__)


# ---- Tool sets relevant to the hooks --------------------------------------

_SEARCH_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "wikipedia_search",
    "wikipedia_get_page",
    "wikipedia_fetch",
})

_WORKSPACE_WRITE_TOOLS: frozenset[str] = frozenset({
    "workspace_create_file",
    "workspace_append",
    "workspace_str_replace",
})

# Threshold above which PostToolUse injects a force-persist nudge.
_FORCE_PERSIST_AFTER_N_RESEARCH_CALLS = 3


# ---- Result types ---------------------------------------------------------


@dataclass
class Decision:
    """Outcome of a PreToolUse check."""
    deny: bool = False
    reason: str | None = None


@dataclass
class ToolCallContext:
    """Context bundled with a tool_call for hook checks.

    - `agent_code` : the agent currently emitting the call.
    - `call` : the parsed ToolCall (name + arguments).
    - `agent_grants` : tool names the agent is authorised to call (from `agent_tools`).
    - `delegation_targets` : agent codes the agent may delegate to (from
      `agent_delegation_targets`). Empty set means "no restriction" (legacy
      v1 behaviour, will be tightened in Phase 6).
    """
    agent_code: str
    call: ToolCall
    agent_grants: frozenset[str] = frozenset()
    delegation_targets: frozenset[str] = frozenset()


# ---- Fingerprint helper ---------------------------------------------------


def _normalize_args_for_fingerprint(args: dict[str, Any]) -> str:
    """Stable string repr of tool arguments for dedup.

    - Sorts keys.
    - Lowercases and strips string values.
    - Preserves type for scalars (int/float/bool/None).
    """
    if not args:
        return ""
    parts: list[str] = []
    for key in sorted(args.keys()):
        val = args[key]
        if isinstance(val, str):
            val = val.strip().lower()
        parts.append(f"{key}={val!r}")
    return "|".join(parts)


def _fingerprint(call_name: str, args: dict[str, Any]) -> str:
    """Fingerprint of a tool call for dedup.

    The scope is the calling LLM context (the `messages[]` of one agent run) —
    naturally isolated because each execution has its own `dedup_cache`.
    Sibling and subagent calls don't poison each other.
    """
    return f"{call_name}({_normalize_args_for_fingerprint(args)})"


# ---- Hooks ----------------------------------------------------------------


class PreLLMCall:
    """Run before each LLM call. May mutate `messages` via compaction escalation."""

    name = "PreLLMCall"

    def __init__(
        self,
        llm_client: Any | None = None,
        conv_folder: Path | None = None,
        is_main_agent: bool = False,
    ) -> None:
        # `llm_client` is required to escalate to levels 3 and 4 (compactor LLM calls).
        # When None, the hook stops at level 2 (deterministic Snip + Microcompact).
        self.llm_client = llm_client
        # `conv_folder` + `is_main_agent` let the orchestrator (and ONLY the
        # orchestrator) re-surface its living TODO each turn. Subagents keep a
        # focused, recap-free context.
        self.conv_folder = conv_folder
        self.is_main_agent = is_main_agent

    def __call__(
        self,
        messages: list[dict[str, Any]],
        state: ConversationState,
    ) -> int:
        """Return the compaction level triggered (0..4)."""
        level = escalate_compaction(messages, state, self.llm_client)
        # Re-inject the living TODO recap + the attached-repo notice — main agent
        # only, no-op without todo.json / without a code-mode worktree.
        if self.is_main_agent and self.conv_folder is not None:
            _refresh_todo_recap(messages, self.conv_folder)
            _refresh_repo_recap(messages, self.conv_folder)
            _refresh_plan_nudge(messages, self.conv_folder, state)
        return level


class PreToolUse:
    """Validate a tool_call before execution."""

    name = "PreToolUse"

    def __call__(
        self,
        ctx: ToolCallContext,
        state: ConversationState,
        dedup_cache: dict[str, dict[str, Any]],
    ) -> Decision:
        # 1. Grant check
        if ctx.call.name not in ctx.agent_grants:
            return Decision(
                deny=True,
                reason=(
                    f"Tool '{ctx.call.name}' not granted to agent "
                    f"'{ctx.agent_code}'. Available: "
                    f"{sorted(ctx.agent_grants)}"
                ),
            )

        # 2. Delegation depth + whitelist
        if ctx.call.name == "delegate_to":
            target = ctx.call.arguments.get("agent_code", "")
            if not target:
                return Decision(
                    deny=True,
                    reason="delegate_to called without agent_code argument",
                )
            if ctx.delegation_targets and target not in ctx.delegation_targets:
                return Decision(
                    deny=True,
                    reason=(
                        f"Delegation target '{target}' not in whitelist for "
                        f"agent '{ctx.agent_code}'. Allowed: "
                        f"{sorted(ctx.delegation_targets)}"
                    ),
                )
            if state.depth_current + 1 > MAX_DEPTH:
                return Decision(
                    deny=True,
                    reason=(
                        f"MAX_DEPTH ({MAX_DEPTH}) would be exceeded by "
                        f"spawning at depth {state.depth_current + 1}. "
                        "Conclude with what you have."
                    ),
                )

        # 3. Search budget (turn-wide, counted across all depths)
        if (
            ctx.call.name in _SEARCH_TOOLS
            and state.search_calls_total >= MAX_SEARCH_CALLS_PER_TURN
        ):
            return Decision(
                    deny=True,
                    reason=(
                        f"MAX_SEARCH_CALLS_PER_TURN "
                        f"({MAX_SEARCH_CALLS_PER_TURN}) reached. Synthesize "
                        "from what you've already gathered."
                    ),
                )

        # 4. Contextualised dedup
        fp = _fingerprint(ctx.call.name, ctx.call.arguments)
        if fp in dedup_cache:
            cached = dedup_cache[fp]
            return Decision(
                deny=True,
                reason=(
                    f"Duplicate call. Previously returned: "
                    f"{cached.get('summary', '(no summary)')}"
                ),
            )

        return Decision(deny=False)


class PostToolUse:
    """Side effects after a successful tool execution."""

    name = "PostToolUse"

    def __call__(
        self,
        call: ToolCall,
        result: Any,
        messages: list[dict[str, Any]],
        state: ConversationState,
        dedup_cache: dict[str, dict[str, Any]],
        agent_code: str = "",
    ) -> None:
        # 1. Counter updates
        if call.name in _SEARCH_TOOLS:
            state.search_calls_total += 1
            state.search_calls_since_last_persist += 1
        elif call.name in _WORKSPACE_WRITE_TOOLS:
            state.search_calls_since_last_persist = 0
        if call.name == "todo_write":
            state.reeval_pending = False  # plan (re-)evaluated → clear the ACT nudge

        # 2. Cache the result for future dedup
        fp = _fingerprint(call.name, call.arguments)
        summary = _summarize_for_cache(result)
        dedup_cache[fp] = {"summary": summary, "agent": agent_code}

        # 3. Force-persist nudge — if research has been running without a write,
        #    inject a synthetic role=user message before the next LLM call.
        if state.search_calls_since_last_persist > _FORCE_PERSIST_AFTER_N_RESEARCH_CALLS:
            nudge = {
                "role": "user",
                "content": (
                    f"[ORCHESTRATOR] You have made "
                    f"{state.search_calls_since_last_persist} research calls "
                    "without writing to the workspace. Persist your findings "
                    "via workspace_create_file or workspace_append before "
                    "continuing."
                ),
            }
            messages.append(nudge)
            # Reset so we don't nudge again immediately on the next call.
            state.search_calls_since_last_persist = 0


class OnDelegateReturn:
    """Push the structured subagent return into the caller's messages[].

    Not a guard — mechanical bridge between subagent completion and the
    parent's messages[]. Listed as a hook for symmetry with the others.
    """

    name = "OnDelegateReturn"

    def __call__(
        self,
        parent_messages: list[dict[str, Any]],
        sub_result: dict[str, Any],
        state: ConversationState,
    ) -> None:
        # Validate the structured return.
        confidence = sub_result.get("confidence", "")
        low_reason = sub_result.get("low_confidence_reason") or ""
        if confidence == "low" and not low_reason.strip():
            raise ValueError(
                "report_back with confidence='low' must include a "
                "non-empty low_confidence_reason. The subagent should "
                "re-emit report_back with the reason."
            )

        import json
        parent_messages.append({
            "role": "tool",
            "tool_name": "delegate_to",
            "content": json.dumps(sub_result, ensure_ascii=False),
        })
        state.active_subagent = None
        # A specialist just returned → the router owes a TODO re-evaluation (ACT)
        # before its next delegation (enforced by the plan nudge in PreLLMCall).
        state.reeval_pending = True


# ---- Utilities ------------------------------------------------------------


def _summarize_for_cache(result: Any) -> str:
    """Compact summary of a tool result for the dedup cache.

    Tool results are typically dicts with a `summary` field (cf. the
    `tool_ok` / `tool_error` contract). Falls back to a truncated repr.
    """
    if isinstance(result, dict):
        s = result.get("summary")
        if isinstance(s, str) and s.strip():
            return s[:200]
        err = result.get("error")
        if isinstance(err, str) and err.strip():
            return f"error: {err[:160]}"
    s = str(result)
    return s[:200] if len(s) > 200 else s


def _refresh_todo_recap(messages: list[dict[str, Any]], conv_folder: Path) -> None:
    """Re-surface the orchestrator's living TODO as the latest `[TODO-RECAP]` msg.

    Idempotent per turn: any previous recap is stripped first (so it never
    accumulates), then a fresh one rendered from todo.json is appended at the
    end. No-op when there is no todo.json — a trivial turn carries no recap.
    """
    messages[:] = [
        m for m in messages
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(RECAP_MARKER)
        )
    ]
    todo = load_todo(conv_folder)
    if todo is None:
        return
    messages.append({"role": "user", "content": render_recap(todo)})


_REPO_RECAP_MARKER = "[CODE-REPO]"


def _refresh_repo_recap(messages: list[dict[str, Any]], conv_folder: Path) -> None:
    """Tell the ROUTER, each turn, that a code repo is attached to this
    conversation — so it DELEGATES to code-runner instead of claiming it has no
    access (or hallucinating a GitHub remote). Idempotent per turn ; no-op when
    there is no worktree (non-code conversations carry no notice)."""
    messages[:] = [
        m for m in messages
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_REPO_RECAP_MARKER)
        )
    ]
    if not worktree.worktree_path_for(conv_folder).exists():
        return
    messages.append({"role": "user", "content": (
        f"{_REPO_RECAP_MARKER} A code repository is attached to this conversation, checked out "
        "in an isolated git worktree. You are the router and do NOT hold repo tools yourself. "
        "Delegate code work to `code-runner`: it operates ON the repo through dedicated tools — "
        "repo_read/grep/glob to inspect, repo_edit/write to change, repo_git (log/show/diff/"
        "status/blame) for git history, repo_test to run tests — and receives an auto-assembled "
        "context of the repo. Brief it by WHAT to achieve in the repo; never tell it to run a "
        "shell command at a filesystem path (it has no shell over the repo, and bash_sandbox "
        "cannot see the repo). Use `code-fetcher` for external lookups. Answer questions about "
        "this code by delegating — never claim you cannot see it, and never assume a remote GitHub repo."
    )})


_PLAN_NUDGE_MARKER = "[ORCHESTRATOR] PLAN:"


def _count_delegations(messages: list[dict[str, Any]]) -> int:
    """Completed delegations so far = number of delegate_to return messages
    (pushed by OnDelegateReturn as role=tool, tool_name=delegate_to)."""
    return sum(
        1 for m in messages
        if m.get("role") == "tool" and m.get("tool_name") == "delegate_to"
    )


def _refresh_plan_nudge(
    messages: list[dict[str, Any]], conv_folder: Path, state: ConversationState
) -> None:
    """Deterministic plan discipline for the code-router (code mode only):

    - PLAN: delegated a multi-step task (≥2 delegations) without a TODO ⇒ tell it
      to decompose with todo_write first.
    - ACT: a specialist just returned and a plan exists (``reeval_pending``) ⇒ tell
      it to re-evaluate/update the TODO before the next delegation.

    Idempotent (strips the prior nudge first). `[ORCHESTRATOR]`-prefixed, so it is
    stripped from messages.json and never surfaces as a user bubble in the UI.
    """
    messages[:] = [
        m for m in messages
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_PLAN_NUDGE_MARKER)
        )
    ]
    if not worktree.worktree_path_for(conv_folder).exists():
        return  # code mode only
    todo = load_todo(conv_folder)
    if todo is None:
        if _count_delegations(messages) >= 2:
            messages.append({"role": "user", "content": (
                f"{_PLAN_NUDGE_MARKER} You are delegating a multi-step task without a plan. Decompose "
                "it now with todo_write (3-7 scoped steps, exactly one in_progress) BEFORE delegating "
                "further, then keep it current after every worker returns."
            )})
    elif state.reeval_pending:
        messages.append({"role": "user", "content": (
            f"{_PLAN_NUDGE_MARKER} A specialist just returned. Re-evaluate your plan with todo_write "
            "(mark the finished step done, add or re-scope any steps the report surfaced, set the next "
            "step in_progress) BEFORE your next delegation."
        )})


# ---- Registry helper -----------------------------------------------------


@dataclass
class HookRegistry:
    """Bundle the 4 hooks for one agent execution scope.

    The orchestrator (Phase 4) creates one registry per `run_main_loop` and
    per `spawn_subagent` invocation. Hooks share no state across registries —
    the dedup cache is passed explicitly per call.
    """
    pre_llm_call: PreLLMCall = field(default_factory=PreLLMCall)
    pre_tool_use: PreToolUse = field(default_factory=PreToolUse)
    post_tool_use: PostToolUse = field(default_factory=PostToolUse)
    on_delegate_return: OnDelegateReturn = field(default_factory=OnDelegateReturn)


def build_hook_registry(
    llm_client: Any | None = None,
    conv_folder: Path | None = None,
    is_main_agent: bool = False,
) -> HookRegistry:
    """Build a fresh hook registry for one execution scope.

    `llm_client` is required for the PreLLMCall hook to escalate beyond
    level 2 (Snip + Microcompact). Pass the v2-capable LLM client used by
    the orchestrator.

    `conv_folder` + `is_main_agent` are forwarded to PreLLMCall so the main
    loop re-injects its TODO recap each turn (subagents get neither).
    """
    return HookRegistry(
        pre_llm_call=PreLLMCall(
            llm_client=llm_client,
            conv_folder=conv_folder,
            is_main_agent=is_main_agent,
        ),
        pre_tool_use=PreToolUse(),
        post_tool_use=PostToolUse(),
        on_delegate_return=OnDelegateReturn(),
    )

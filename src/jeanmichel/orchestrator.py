"""Orchestrator: pure Python state machine, drives agent turns and persists everything.

Implemented as a generator that `yield`s events. The CLI consumes those events
to render the conversation in real time without coupling itself to the
orchestrator internals.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config, db
from .config import MAX_RECURSION_DEPTH, UserProfile, ensure_dirs
from .llm import LLMClient
from .models import LLMResponse
from .persistence import (
    append_to_journal,
    conversation_folder_name,
    write_artifact,
)
from .prompts import (
    PromptContext,
    render_system_prompt,
    tools_payload_for_agent,
)
from .tools import build_registry

# ---- Events emitted to the CLI -------------------------------------------

@dataclass
class ConversationStarted:
    conversation_id: str
    folder_path: str
    user_language: str


@dataclass
class AgentStarted:
    agent_code: str
    request_id: str
    depth: int


@dataclass
class ThoughtCaptured:
    agent_code: str
    text: str


@dataclass
class ToolCallEmitted:
    agent_code: str
    tool_name: str
    arguments: dict


@dataclass
class ToolResponseRecorded:
    agent_code: str
    tool_name: str
    response: str


@dataclass
class DelegationStarted:
    parent_agent: str
    child_agent: str
    briefing: str


@dataclass
class HumanQuestionAsked:
    agent_code: str
    question: str
    why: str


@dataclass
class HumanAnswerReceived:
    answer: str


@dataclass
class RecursionLimitReached:
    agent_code: str
    depth: int


@dataclass
class FinalAnswer:
    text: str


@dataclass
class OrchestrationFailed:
    reason: str


@dataclass
class TurnStarted:
    turn_index: int


@dataclass
class SummaryUpdated:
    path: str


# ---- Helpers --------------------------------------------------------------

def _detect_language(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "und"


def _new_uuid() -> str:
    return uuid.uuid4().hex[:12]


# ---- Orchestrator ---------------------------------------------------------

class Orchestrator:
    """Owns one conversation. Use `run(user_input)` once; subsequent messages
    create new requests inside the same conversation via `continue_with(...)`.
    """

    def __init__(self, llm: LLMClient, profile: UserProfile,
                 mode: str = "analyse",
                 conv_id: str | None = None,
                 ask_human_callback=None) -> None:
        assert mode in config.MODES, f"Unknown mode: {mode!r}"
        ensure_dirs()
        self.llm = llm
        self.profile = profile
        self.mode = mode
        self.conv_id = conv_id or _new_uuid()
        self.ask_human_callback = ask_human_callback
        self.conv_folder: Path | None = None
        self.user_language: str = "und"
        self.turn_index: int = -1

    # ---- Public API ------------------------------------------------------

    def run(self, user_input: str) -> Generator[object]:
        """Process one user input. Yields events; the CLI consumes them."""
        self.user_language = _detect_language(user_input)

        if self.conv_folder is None:
            # First turn — create the conversation.
            started = datetime.now(UTC)
            folder_name = conversation_folder_name(self.conv_id, started)
            self.conv_folder = config.CONVERSATIONS_DIR / folder_name
            self.conv_folder.mkdir(parents=True, exist_ok=True)
            with db.connect() as conn:
                db.create_conversation(
                    conn, self.conv_id, str(self.conv_folder),
                    self.user_language, mode=self.mode,
                )
            self.turn_index = 0
            yield ConversationStarted(
                conversation_id=self.conv_id,
                folder_path=str(self.conv_folder),
                user_language=self.user_language,
            )
        else:
            # Subsequent turns.
            self.turn_index += 1
            yield TurnStarted(turn_index=self.turn_index)

        enriched_input = self._prefix_summary(user_input)
        append_to_journal(self.conv_folder, f"## User (turn {self.turn_index})\n{user_input}\n")

        answer = yield from self._run_request(
                agent_code="jean-michel",
                inbound_text=enriched_input,
                expected_outcome="Address the human request fully.",
                support_files=[],
                parent_request_id=None,
                depth=0,
                sender="human",
            )

        append_to_journal(self.conv_folder, f"## Jean-Michel\n{answer}\n")
        yield FinalAnswer(text=answer)

        if self.mode in {"chat", "vocal"}:
            yield from self._run_archivist(user_input, answer)

    # ---- Internal --------------------------------------------------------

    def _run_request(self, *, agent_code: str, inbound_text: str,
                     expected_outcome: str, support_files: list[str],
                     parent_request_id: str | None, depth: int,
                     sender: str) -> Generator[object, None, str]:
        """Run a single agent request, recursively if it delegates.

        Returns the agent's final string output (the value passed to
        return_to_user).
        """
        assert self.conv_folder is not None

        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, agent_code)
            paradigms = db.load_paradigms_for_agent(conn, agent.id, self.mode)
            available_agents = db.list_active_agents(conn)
            tool_grants = db.load_tool_grants(conn, agent.id)
            req_id = _new_uuid()
            db.create_request(
                conn,
                req_id=req_id,
                conv_id=self.conv_id,
                parent_id=parent_request_id,
                depth=depth,
                agent_id=agent.id,
                inbound_briefing=inbound_text,
                expected_outcome=expected_outcome,
                turn_index=self.turn_index,
            )
            db.update_request_status(conn, req_id, "running")

        yield AgentStarted(agent_code=agent_code, request_id=req_id, depth=depth)

        registry = build_registry(self.conv_folder)

        # Multi-step loop: tool_call -> tool_response -> ... until return_to_user.
        # We rebuild the user message each turn (KISS, matches the Gemma 4
        # multi-turn rule of stripping previous thoughts between turns).
        running_user_text = inbound_text
        max_steps = 8  # safety net against tool-loops within a single request

        # Build the system prompt once — the mission is immutable for the
        # lifetime of this request. Only the LLM user message changes between
        # tool-call iterations.
        ctx = PromptContext(
            agent=agent,
            paradigms=paradigms,
            user_profile=self.profile,
            detected_language=self.user_language,
            conversation_id=self.conv_id,
            conversation_folder=str(self.conv_folder),
            request_id=req_id,
            parent_request_id=parent_request_id,
            depth=depth,
            mode=self.mode,
            turn_index=self.turn_index,
            sender=sender,
            expected_outcome=expected_outcome,
            support_files=support_files,
            inbound_text=inbound_text,
            tool_registry=registry,
            available_agents=available_agents,
        )
        system = render_system_prompt(ctx)
        tools_payload = tools_payload_for_agent(tool_grants, registry)
        self._write_artifact(req_id, agent_code, "prompt",
            f"## System\n```\n{system}\n```\n\n## User\n```\n{running_user_text}\n```\n")

        try:
            for _step in range(max_steps):
                response: LLMResponse = self.llm.chat(
                    system=system,
                    user=running_user_text,
                    tools=tools_payload,
                    temperature=agent.temperature,
                    thinking=agent.thinking_mode,
                )

                if response.thinking:
                    self._write_artifact(req_id, agent_code, "thought", response.thinking)
                    yield ThoughtCaptured(agent_code=agent_code, text=response.thinking)

                # No tool calls: model produced free text. Treat as implicit return_to_user.
                if not response.tool_calls:
                    final = response.content.strip() or "(empty response)"
                    self._record_response(req_id, agent_code, final)
                    with db.connect() as conn:
                        db.update_request_status(conn, req_id, "completed", completed=True)
                    return final

                # Enforce: at most one ask_human per turn.
                seen_ask = False
                tool_responses: list[str] = []
                for call in response.tool_calls:
                    yield ToolCallEmitted(agent_code=agent_code, tool_name=call.name,
                                          arguments=call.arguments)
                    self._write_artifact(req_id, agent_code, "tool_call",
                        f"**{call.name}**\n\n```json\n{call.arguments}\n```")

                    # ---- Control tools --------------------------------------
                    if call.name == "return_to_user":
                        answer = (call.arguments.get("answer") or "").strip()
                        self._record_response(req_id, agent_code, answer)
                        with db.connect() as conn:
                            db.update_request_status(conn, req_id, "completed", completed=True)
                        return answer

                    if call.name == "ask_human":
                        if seen_ask:
                            msg = "REJECTED: only one ask_human is allowed per turn."
                            tool_responses.append(f"[ask_human] {msg}")
                            continue
                        seen_ask = True
                        answer = yield from self._handle_ask_human(
                            req_id, agent_code, call.arguments,
                        )
                        tool_responses.append(f"[ask_human] human answer: {answer}")
                        continue

                    if call.name == "delegate_to":
                        child_code = call.arguments.get("agent_code", "")
                        if child_code == "archivist":
                            msg = "REJECTED: archivist is an internal component and cannot be called via delegate_to."
                            tool_responses.append(f"[delegate_to] {msg}")
                            continue
                        if depth + 1 > MAX_RECURSION_DEPTH:
                            msg = (f"REJECTED: recursion depth {depth + 1} exceeds "
                                   f"limit {MAX_RECURSION_DEPTH}. You must conclude "
                                   f"with the information at hand.")
                            yield RecursionLimitReached(agent_code=agent_code, depth=depth + 1)
                            tool_responses.append(f"[delegate_to] {msg}")
                            continue
                        briefing = call.arguments.get("briefing", "")
                        expected = call.arguments.get("expected", "")
                        sup_files = call.arguments.get("support_files") or []
                        yield DelegationStarted(parent_agent=agent_code,
                                                child_agent=child_code, briefing=briefing)
                        self._write_artifact(req_id, agent_code, "briefing",
                            f"to: {child_code}\nexpected: {expected}\n\n{briefing}")
                        try:
                            child_answer = yield from self._run_request(
                                agent_code=child_code,
                                inbound_text=briefing,
                                expected_outcome=expected,
                                support_files=sup_files,
                                parent_request_id=req_id,
                                depth=depth + 1,
                                sender=agent_code,
                            )
                        except KeyError:
                            child_answer = f"[error] unknown agent: {child_code}"
                        tool_responses.append(f"[delegate_to:{child_code}] {child_answer}")
                        continue

                    # ---- Native Python tools --------------------------------
                    spec = registry.get(call.name)
                    if spec is None:
                        tool_responses.append(f"[{call.name}] REJECTED: unknown tool.")
                        continue
                    try:
                        result = spec.handler(**call.arguments)
                    except TypeError as e:
                        result = f'{{"error": "Bad arguments: {e}"}}'
                    except Exception as e:  # noqa: BLE001
                        result = f'{{"error": "Tool failed: {e}"}}'
                    yield ToolResponseRecorded(agent_code=agent_code,
                                               tool_name=call.name, response=result)
                    self._write_artifact(req_id, agent_code, "tool_response",
                        f"**{call.name}**\n\n```\n{result}\n```")
                    tool_responses.append(f"[{call.name}] {result}")

                # Feed all tool responses back to the model on the next iteration.
                running_user_text = (
                    "[ORCHESTRATOR] Tool results below. Resume execution of your current task.\n\n"
                    + "\n".join(tool_responses)
                )

        except Exception:
            with db.connect() as conn:
                db.update_request_status(conn, req_id, "failed", completed=True)
            raise

        msg = "[orchestrator] step budget exhausted within a single request."
        self._record_response(req_id, agent_code, msg)
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "failed", completed=True)
        return msg

    # ---- Summary helpers ------------------------------------------------

    def _prefix_summary(self, user_input: str) -> str:
        if self.mode == "analyse" or self.turn_index == 0:
            return user_input
        assert self.conv_folder is not None
        summary_path = self.conv_folder / "summary.md"
        if not summary_path.exists():
            return user_input
        summary = summary_path.read_text(encoding="utf-8").strip()
        return (
            "## Conversation summary so far\n"
            f"{summary}\n\n"
            "## New user turn\n"
            f"{user_input}"
        )

    def _run_archivist(self, last_user: str, last_answer: str) -> Generator[object, None, None]:
        assert self.conv_folder is not None
        summary_path = self.conv_folder / "summary.md"
        previous_summary = ""
        if summary_path.exists():
            previous_summary = summary_path.read_text(encoding="utf-8")

        briefing = (
            "Update the running summary.\n\n"
            f"## Previous summary\n{previous_summary or '(none)'}\n\n"
            f"## Latest user turn\n{last_user}\n\n"
            f"## Latest assistant answer\n{last_answer}\n\n"
            "Produce the new summary as the value of return_to_user. "
            "Follow the archivist_format paradigm strictly."
        )

        try:
            new_summary = yield from self._run_request(
                agent_code="archivist",
                inbound_text=briefing,
                expected_outcome="Updated running summary, structured per archivist_format.",
                support_files=[],
                parent_request_id=None,
                depth=0,
                sender="orchestrator",
            )
        except Exception:  # noqa: BLE001
            return  # Keep previous summary on failure; do not block the user.

        summary_path.write_text(new_summary, encoding="utf-8")
        yield SummaryUpdated(path=str(summary_path))

    # ---- ask_human handling ---------------------------------------------

    def _handle_ask_human(self, req_id: str, agent_code: str,
                          args: dict) -> Generator[object, None, str]:
        question = args.get("question", "")
        why = args.get("why", "")
        self._write_artifact(req_id, agent_code, "ask_human",
            f"**why:** {why}\n\n**question:**\n{question}")
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "awaiting_human")
        yield HumanQuestionAsked(agent_code=agent_code, question=question, why=why)
        if self.ask_human_callback is None:
            answer = ""
        else:
            answer = self.ask_human_callback(question=question, why=why)
        self._write_artifact(req_id, agent_code, "human_answer", answer)
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "running")
        yield HumanAnswerReceived(answer=answer)
        return answer

    # ---- Misc ------------------------------------------------------------

    def _record_response(self, req_id: str, agent_code: str, text: str) -> None:
        self._write_artifact(req_id, agent_code, "response", text)

    def _write_artifact(self, request_id: str, agent_code: str,
                        kind: str, body: str) -> str:
        """Write a file artifact and record it in the DB. Returns the filename."""
        assert self.conv_folder is not None
        filename = write_artifact(
            self.conv_folder,
            conversation_id=self.conv_id,
            request_id=request_id,
            agent=agent_code,
            kind=kind,
            body=body,
        )
        with db.connect() as conn:
            db.record_artifact(conn, request_id, filename, kind)
        return filename

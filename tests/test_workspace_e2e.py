"""End-to-end tests for workspace tool calls flowing through the orchestrator.

Scenario: document-builder (via delegation from jean-michel) emits several
workspace tool calls in sequence.  After the run:
- Files appear on disk at the expected workspace path.
- The standard tool_call / tool_response artifacts exist in the DB.
- No workspace file is tracked in the artifacts table (filesystem = inventory).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import FinalAnswer, Orchestrator, ToolCallEmitted

PROFILE = UserProfile(notes="e2e test user")


def _orch(script, tmp_env):
    return Orchestrator(
        llm=MockClient(script=script),
        profile=PROFILE,
        mode="analyse",
    )


def _artifact_paths(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT relative_path FROM artifacts").fetchall()
    conn.close()
    return [r[0] for r in rows]


class TestWorkspaceE2E:
    """Workspace tool calls through the full orchestrator stack."""

    def test_create_and_view_file_via_delegation(self, tmp_env):
        """document-builder creates and then reads a workspace file.

        Verifies:
        - File exists on disk with correct content.
        - No artifact tracks the workspace file itself (only tool_call / tool_response).
        """
        import jeanmichel.config as cfg

        orch = _orch([
            # jean-michel classifies then delegates to document-builder
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "medium_task"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "document-builder",
                    "briefing": "Create a file notes.md with content 'hello world'.",
                    "expected": "confirmation that the file was created",
                    "support_files": [],
                }),
            ]),
            # document-builder: create file
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_create_file", arguments={
                    "relative_path": "notes.md",
                    "content": "hello world",
                    "description": "test note",
                }),
            ]),
            # document-builder: view file then report findings
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_view", arguments={"relative_path": "notes.md"}),
                ToolCall(name="report_findings", arguments={"summary": "Created notes.md with 'hello world'.", "confidence": "high"}),
            ]),
            # jean-michel wraps up
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Done."}),
            ]),
        ], tmp_env)

        events = list(orch.run("Create a notes.md file."))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "Done."

        # File exists on disk at the right location.
        conv_folder = orch.conv_folder
        assert conv_folder is not None
        ws_file = conv_folder / "workspace" / "notes.md"
        assert ws_file.exists(), "workspace/notes.md should exist after creation"
        assert ws_file.read_text() == "hello world"

        # workspace file must NOT appear in the artifacts table.
        artifact_paths = _artifact_paths(cfg.DB_PATH)
        assert not any("workspace/" in p for p in artifact_paths), (
            f"Workspace file tracked in artifacts — should not be: {artifact_paths}"
        )

    def test_str_replace_via_delegation(self, tmp_env):
        """document-builder creates a file then edits it with workspace_str_replace."""

        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "document-builder",
                    "briefing": "Create report.md then fix a typo.",
                    "expected": "edited file",
                    "support_files": [],
                }),
            ]),
            # create
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_create_file", arguments={
                    "relative_path": "report.md",
                    "content": "# Report\nHello wrold.",
                    "description": "draft report",
                }),
            ]),
            # str_replace
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_str_replace", arguments={
                    "relative_path": "report.md",
                    "old_str": "wrold",
                    "new_str": "world",
                }),
                ToolCall(name="report_findings", arguments={"summary": "Typo fixed.", "confidence": "high"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Fixed."}),
            ]),
        ], tmp_env)

        list(orch.run("Fix the typo in report.md."))
        report = orch.conv_folder / "workspace" / "report.md"
        assert report.exists()
        assert "world" in report.read_text()
        assert "wrold" not in report.read_text()

    def test_workspace_list_via_delegation(self, tmp_env):
        """workspace_list returns a non-empty directory listing after a file is created."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "workspace-manager",
                    "briefing": "List the workspace contents.",
                    "expected": "directory listing",
                    "support_files": [],
                }),
            ]),
            # workspace-manager creates a file then lists
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_create_file", arguments={
                    "relative_path": "data.csv",
                    "content": "a,b\n1,2",
                    "description": "data file",
                }),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_list", arguments={}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="report_findings", arguments={"summary": "Listed.", "confidence": "high"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Done."}),
            ]),
        ], tmp_env)

        events = list(orch.run("List workspace."))
        tool_calls_emitted = [e for e in events if isinstance(e, ToolCallEmitted)]
        assert any(e.tool_name == "workspace_list" for e in tool_calls_emitted)
        assert (orch.conv_folder / "workspace" / "data.csv").exists()

    def test_tool_call_artifacts_created_tool_response_recorded(self, tmp_env):
        """Conversational artifacts (tool_call, tool_response) are recorded as usual."""
        import jeanmichel.config as cfg

        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "document-builder",
                    "briefing": "Create file.",
                    "expected": "done",
                    "support_files": [],
                }),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_create_file", arguments={
                    "relative_path": "artifact_test.md",
                    "content": "x",
                    "description": "test",
                }),
                ToolCall(name="report_findings", arguments={"summary": "File created.", "confidence": "high"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Done."}),
            ]),
        ], tmp_env)

        list(orch.run("Create file."))

        conn = sqlite3.connect(cfg.DB_PATH)
        conn.row_factory = sqlite3.Row
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM artifacts").fetchall()]
        conn.close()

        # Standard conversational artifacts should exist.
        assert "tool_call" in kinds
        assert "tool_response" in kinds

        # workspace file itself must not be tracked.
        artifact_paths = _artifact_paths(cfg.DB_PATH)
        assert not any("workspace/" in p for p in artifact_paths)

    def test_path_traversal_blocked_via_orchestrator(self, tmp_env):
        """A path traversal attempt via workspace_create_file is refused gracefully."""

        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "document-builder",
                    "briefing": "Try to write outside workspace.",
                    "expected": "error",
                    "support_files": [],
                }),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="workspace_create_file", arguments={
                    "relative_path": "../escape.md",
                    "content": "bad",
                    "description": "attack",
                }),
                ToolCall(name="report_findings", arguments={"summary": "Tried path traversal.", "confidence": "high"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Done."}),
            ]),
        ], tmp_env)

        list(orch.run("Escape the workspace."))
        conv_folder = orch.conv_folder
        # escape.md must NOT exist at conv root.
        assert not (conv_folder / "escape.md").exists()
        # workspace/escape.md must NOT exist either.
        assert not (conv_folder / "workspace" / "escape.md").exists()

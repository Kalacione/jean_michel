"""Tests for the Context Reconstruction Pipeline (P2, context_packet.py).

The CRP assembles a deterministic context packet for code-mode delegations and
injects it into the worker's first message. Best-effort: no worktree → "".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, context_packet, todo, worktree  # noqa: E402
from jeanmichel.llm import MockClient  # noqa: E402
from jeanmichel.models import ConversationState  # noqa: E402
from jeanmichel.orchestrator_v2 import spawn_subagent  # noqa: E402
from jeanmichel.tools import repo_edit  # noqa: E402

from ._orchestrator_helpers import assistant_response, make_agent, tool_call  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
requires_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not available")

_SAMPLE = (
    "X = 1\n\n"
    "def compute_total(items):\n"
    "    return sum(items)\n\n\n"
    "class WidgetFactory:\n"
    "    def build(self):\n"
    "        return compute_total([1, 2])\n"
)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    def run(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "sample.py").write_text(_SAMPLE, encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")


@pytest.fixture()
def code_conv(tmp_path, monkeypatch):
    """A code conversation: worktree + a living todo. Returns (conv, root)."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    conv = tmp_path / "conv"
    conv.mkdir()
    root = worktree.create_worktree(conv, "c1")
    assert root is not None
    todo.save_todo(conv, "Harden compute_total", [
        {"id": "1", "text": "make compute_total handle empty items", "status": "in_progress"},
    ])
    return conv, root


_BRIEF = "Refactor compute_total used by WidgetFactory to handle empty items safely."


# ---- gating -----------------------------------------------------------------


def test_no_worktree_returns_empty(conv_folder):
    assert context_packet.build_context_packet(conv_folder, briefing="x", support_files=["a.py"]) == ""


# ---- slices -----------------------------------------------------------------


@requires_git
def test_packet_has_task_and_source(code_conv):
    conv, _ = code_conv
    pkt = context_packet.build_context_packet(conv, briefing=_BRIEF, support_files=["sample.py"])
    assert "Reconstructed context" in pkt
    assert "## Task" in pkt and "Harden compute_total" in pkt
    assert "## Source" in pkt and "sample.py" in pkt
    assert "compute_total" in pkt  # source content present
    assert "\t" in pkt  # cat -n formatting


@requires_git
@requires_rg
def test_packet_has_grep_hits(code_conv):
    conv, _ = code_conv
    pkt = context_packet.build_context_packet(conv, briefing=_BRIEF, support_files=["sample.py"])
    assert "## Lexical hits" in pkt
    assert "sample.py:" in pkt  # rg line-numbered hit


@requires_git
def test_packet_shows_recent_diff_after_edit(code_conv):
    conv, root = code_conv
    # A prior step changed the worktree → the diff slice must surface it.
    (root / "sample.py").write_text(_SAMPLE.replace("X = 1", "X = 2"), encoding="utf-8")
    pkt = context_packet.build_context_packet(conv, briefing=_BRIEF, support_files=["sample.py"])
    assert "## Recent changes" in pkt
    assert "X = 2" in pkt or "X = 1" in pkt  # diff hunk content


@requires_git
def test_packet_marks_support_files_read(code_conv):
    conv, _ = code_conv
    # Building the packet reads sample.py → repo_edit should pass the
    # read-before-edit gate WITHOUT an explicit repo_read first.
    context_packet.build_context_packet(conv, briefing=_BRIEF, support_files=["sample.py"])
    out = json.loads(repo_edit.make_spec(conv).handler(
        "sample.py", "def compute_total(items):", "def compute_total(items=()):"
    ))
    assert out.get("occurrences_replaced") == 1


def test_identifiers_mining():
    idents = context_packet._identifiers(
        "Refactor compute_total in WidgetFactory; ignore the and for noise", ["helpers.py"],
    )
    assert "compute_total" in idents       # snake_case
    assert "WidgetFactory" in idents       # CamelCase
    assert "helpers" in idents             # support-file stem
    assert "the" not in idents and "and" not in idents  # stopwords dropped


# ---- integration : injected into the subagent briefing ----------------------


@requires_git
def test_spawn_subagent_injects_packet(code_conv):
    conv, _ = code_conv
    sub = make_agent("code-runner", role="specialist")
    mock = MockClient(script=[assistant_response(
        "", tool_calls=[tool_call("report_back", summary="ok", files_produced=[], confidence="high")],
    )])
    spawn_subagent(
        conv_folder=conv,
        sub_agent=sub,
        tools_registry={},
        llm_client=mock,
        briefing=_BRIEF,
        support_files=["sample.py"],
        expected="done",
        parent_state=ConversationState(depth_current=0),
    )
    sub_files = list(conv.glob("subagent_*.json"))
    assert len(sub_files) == 1
    msgs = json.loads(sub_files[0].read_text(encoding="utf-8"))
    user_msg = msgs[1]["content"]
    assert "## Briefing" in user_msg                  # original briefing kept
    assert "Reconstructed context" in user_msg        # CRP packet appended
    assert "compute_total" in user_msg


def test_spawn_subagent_no_packet_without_worktree(tmp_path):
    # No worktree (not code mode) → briefing carries no CRP packet.
    conv = tmp_path / "conv"
    conv.mkdir()
    sub = make_agent("summarizer", role="specialist")
    mock = MockClient(script=[assistant_response(
        "", tool_calls=[tool_call("report_back", summary="ok", files_produced=[], confidence="high")],
    )])
    spawn_subagent(
        conv_folder=conv, sub_agent=sub, tools_registry={}, llm_client=mock,
        briefing="summarize this", support_files=[], expected="",
        parent_state=ConversationState(depth_current=0),
    )
    msgs = json.loads(list(conv.glob("subagent_*.json"))[0].read_text(encoding="utf-8"))
    assert "Reconstructed context" not in msgs[1]["content"]


@requires_git
def test_packet_reads_workspace_support_file(code_conv):
    """A workspace handoff artifact (a previous specialist's findings) is read inline
    from the WORKSPACE — not looked up in the repo (bug C handoff, conv dfcafc75)."""
    from jeanmichel.tools._workspace import workspace_root_for
    conv, _ = code_conv
    ws = workspace_root_for(conv)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "v1_findings.md").write_text("# v1 findings\n- module A still uses v1\n", encoding="utf-8")
    pkt = context_packet.build_context_packet(
        conv, briefing="summarize the v1 findings", support_files=["v1_findings.md"]
    )
    assert "workspace:v1_findings.md" in pkt        # labelled as a workspace artifact
    assert "module A still uses v1" in pkt          # content injected inline

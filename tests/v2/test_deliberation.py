"""Tests for the dialectic deliberation engine (P5, deliberation.py).

Unit-tests the engine with an injected fake spawn (no LLM), the deterministic
trigger, and one integration pass through run_main_loop (worktree + MockClient)
proving the vetted approach is prepended to the code worker's briefing.
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

from jeanmichel import config, deliberation, worktree  # noqa: E402
from jeanmichel.llm import MockClient  # noqa: E402
from jeanmichel.orchestrator_v2 import SubResult, run_main_loop  # noqa: E402

from ._orchestrator_helpers import assistant_response, make_agent, tool_call  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _fake_spawn(kiss_script):
    """Injected spawn: critical-coder returns a generic analysis; sergent-kiss
    returns the next scripted confidence (high=PASS, low=REWORK)."""
    state = {"kiss": list(kiss_script), "calls": []}

    def spawn(agent_code, brief, sf=None):
        state["calls"].append((agent_code, brief))
        if agent_code == "sergent-kiss":
            conf = state["kiss"].pop(0) if state["kiss"] else "high"
            return SubResult(
                agent="sergent-kiss", summary="verdict", confidence=conf,
                low_confidence_reason=("drop the cache layer" if conf == "low" else ""),
            )
        return SubResult(agent=agent_code, summary=f"{agent_code} analysis", confidence="high")

    return spawn, state


def _syn_calls(state):
    return [c for c in state["calls"] if c[0] == "critical-coder" and "ANGLE: SYNTHESIS" in c[1]]


# ---- trigger ----------------------------------------------------------------


def test_complexity_probe():
    assert deliberation.complexity_probe("Refactor the parser", []) is True   # keyword
    assert deliberation.complexity_probe("change a thing", ["a.py", "b.py"]) is True  # >=2 files
    assert deliberation.complexity_probe("add a print statement", []) is False
    assert deliberation.complexity_probe("tweak one line", ["a.py"]) is False


def test_should_deliberate_gates_on_worker_and_mode(tmp_path):
    # No worktree → never deliberate, even for a hard brief.
    assert deliberation.should_deliberate(tmp_path, "code-runner", "refactor X", []) is False
    assert deliberation.should_deliberate(tmp_path, "wikipedia-specialist", "refactor X", []) is False


# ---- engine (fake spawn) ----------------------------------------------------


def test_deliberate_approach_pass():
    spawn, state = _fake_spawn(["high"])
    out = deliberation.deliberate_approach(spawn=spawn, task="refactor X", support_files=["a.py"])
    assert out.verdict == "pass"
    assert out.synthesis  # vetted approach text present
    stages = [t["stage"] for t in out.transcript]
    assert stages[:3] == ["thesis", "antithesis", "synthesis"]
    assert "sergent-kiss" in stages
    assert len(_syn_calls(state)) == 1  # no rework


def test_deliberate_approach_rework_then_pass():
    spawn, state = _fake_spawn(["low", "high"])
    out = deliberation.deliberate_approach(spawn=spawn, task="refactor X", support_files=["a.py"])
    assert out.verdict == "pass"
    assert len(_syn_calls(state)) == 2  # one revision after the REWORK


def test_deliberate_approach_rework_exhausted():
    spawn, state = _fake_spawn(["low", "low", "low"])
    out = deliberation.deliberate_approach(spawn=spawn, task="refactor X", support_files=["a.py"])
    assert out.verdict == "rework"
    assert out.critique  # the KISS cuts are surfaced
    assert len(_syn_calls(state)) == 3  # initial + 2 bounded revisions


def test_deliberate_approach_skipped_when_agents_unavailable():
    out = deliberation.deliberate_approach(spawn=lambda *a, **k: None, task="x", support_files=[])
    assert out.verdict == "skipped"


def test_review_diff_pass_and_rework():
    spawn, _ = _fake_spawn(["high"])
    out = deliberation.review_diff(spawn=spawn, task="t", diff="--- a\n+++ b\n+x")
    assert out.verdict == "pass"
    spawn2, _ = _fake_spawn(["low"])
    out2 = deliberation.review_diff(spawn=spawn2, task="t", diff="--- a\n+++ b\n+x")
    assert out2.verdict == "rework" and out2.critique


# ---- worktree-backed helpers + integration ---------------------------------


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "sample.py").write_text("X = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")


@pytest.fixture()
def wt(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    conv = tmp_path / "conv"
    conv.mkdir()
    root = worktree.create_worktree(conv, "c1")
    assert root is not None
    return conv, root


@requires_git
def test_should_deliberate_true_with_worktree(wt):
    conv, _ = wt
    assert deliberation.should_deliberate(conv, "code-runner", "refactor across files", []) is True


@requires_git
def test_current_diff(wt):
    conv, root = wt
    assert deliberation.current_diff(conv) == ""          # clean worktree
    (root / "sample.py").write_text("X = 2\n", encoding="utf-8")
    assert "X = 2" in deliberation.current_diff(conv)      # edit shows up


@requires_git
def test_run_main_loop_prepends_vetted_approach(wt):
    """Integration: a hard code delegation triggers the deliberation, and the
    vetted approach is prepended to the code worker's briefing."""
    conv, _ = wt
    main_agent = make_agent("jean-michel", role="router", delegation_targets={"code-runner"})

    def resolver(code):
        if code in ("code-runner", "critical-coder", "sergent-kiss"):
            return make_agent(code, role="specialist")
        return None

    mock = MockClient(script=[
        # router → delegate the hard step
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="code-runner",
            briefing="Refactor the parser across multiple files", support_files=["sample.py"],
        )]),
        # deliberation: thesis, antithesis, synthesis, sergent-kiss(PASS)
        assistant_response("", tool_calls=[tool_call("report_back", summary="thesis", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="antithesis", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="synthesis: minimal plan", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="simple enough", files_produced=[], confidence="high")]),
        # code worker
        assistant_response("", tool_calls=[tool_call("report_back", summary="done", files_produced=[], confidence="high")]),
        # router final
        assistant_response("All set."),
    ])

    run_main_loop(
        conv_folder=conv, agent=main_agent, tools_registry={}, llm_client=mock,
        user_text="refactor", agent_resolver=resolver,
    )

    briefings = [
        json.loads(p.read_text(encoding="utf-8"))[1]["content"]
        for p in conv.glob("subagent_*.json")
    ]
    # critical-coder ran (angle briefings present) ...
    assert any("ANGLE: THESIS" in b for b in briefings)
    # ... and the code worker received the vetted approach prepended.
    assert any("Vetted approach (deliberated" in b and "Refactor the parser" in b for b in briefings)

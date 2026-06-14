"""Tests for the deliberation engine (P5, deliberation.py) — DOWNSTREAM validation.

critical-coder/sergent-kiss are validators (grounding/correctness/simplicity +
PASS/REWORK) of a CONCRETE deliverable, NOT creatives. No upstream pre-planning.
Unit-tests with an injected fake spawn + one integration through run_main_loop.
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
    """Injected spawn: critical-coder returns a generic review; sergent-kiss returns
    the next scripted confidence (high=PASS, low=REWORK)."""
    state = {"kiss": list(kiss_script), "calls": []}

    def spawn(agent_code, brief, sf=None):
        state["calls"].append((agent_code, brief))
        if agent_code == "sergent-kiss":
            conf = state["kiss"].pop(0) if state["kiss"] else "high"
            return SubResult(
                agent="sergent-kiss", summary="verdict", confidence=conf,
                low_confidence_reason=("drop the cache layer" if conf == "low" else ""),
            )
        return SubResult(agent=agent_code, summary=f"{agent_code} review", confidence="high")

    return spawn, state


# ---- trigger ----------------------------------------------------------------


def test_complexity_probe():
    assert deliberation.complexity_probe("Refactor the parser", []) is True   # keyword
    assert deliberation.complexity_probe("change a thing", ["a.py", "b.py"]) is True  # >=2 files
    assert deliberation.complexity_probe("add a print statement", []) is False
    assert deliberation.complexity_probe("tweak one line", ["a.py"]) is False


# ---- engine : validate_deliverable (fake spawn) -----------------------------


def test_validate_deliverable_pass():
    spawn, state = _fake_spawn(["high"])
    out = deliberation.validate_deliverable(spawn=spawn, task="t", kind="diff", content="--- a\n+++ b\n+x")
    assert out.verdict == "pass"
    stages = [t["stage"] for t in out.transcript]
    assert stages == ["review:grounding", "review:correctness", "review:simplicity", "sergent-kiss"]
    # critic spawns are validators, not creatives: no THESIS/SYNTHESIS angle.
    assert not any("ANGLE: THESIS" in b or "ANGLE: SYNTHESIS" in b for _, b in state["calls"])
    assert any("VALIDATOR" in b and "do NOT redesign" in b for _, b in state["calls"])


def test_validate_deliverable_rework():
    spawn, _ = _fake_spawn(["low"])
    out = deliberation.validate_deliverable(spawn=spawn, task="t", kind="analysis report", content="claim Z")
    assert out.verdict == "rework" and out.critique


def test_validate_deliverable_skipped_when_agents_unavailable():
    out = deliberation.validate_deliverable(
        spawn=lambda *a, **k: None, task="t", kind="diff", content="x",
    )
    assert out.verdict == "skipped"


def test_no_upstream_creative_api():
    """The creative upstream pass is gone — critics are downstream validators only."""
    assert not hasattr(deliberation, "deliberate_approach")
    assert not hasattr(deliberation, "should_deliberate")


# ---- worktree-backed helpers + integration ---------------------------------


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    def run(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
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
def test_current_diff(wt):
    conv, root = wt
    assert deliberation.current_diff(conv) == ""          # clean worktree
    (root / "sample.py").write_text("X = 2\n", encoding="utf-8")
    assert "X = 2" in deliberation.current_diff(conv)      # edit shows up


@requires_git
def test_run_main_loop_validates_analysis_report_no_upstream(wt):
    """Integration: a hard code-analyst delegation triggers DOWNSTREAM validation of
    its report (grounding angle present) and NO upstream 'Vetted approach' pre-plan."""
    conv, _ = wt
    main_agent = make_agent("code-router", role="router", delegation_targets={"code-analyst"})

    def resolver(code):
        if code in ("code-analyst", "critical-coder", "sergent-kiss"):
            return make_agent(code, role="specialist")
        return None

    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="code-analyst",
            briefing="Analyse the parser across multiple files", support_files=["sample.py"],
        )]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="the parser uses X in sample.py", files_produced=[], confidence="high")]),
        # downstream validation : grounding / correctness / simplicity + gate
        assistant_response("", tool_calls=[tool_call("report_back", summary="grounding ok", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="correctness ok", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="simple ok", files_produced=[], confidence="high")]),
        assistant_response("", tool_calls=[tool_call("report_back", summary="PASS", files_produced=[], confidence="high")]),
        assistant_response("Done."),
    ])

    run_main_loop(
        conv_folder=conv, agent=main_agent, tools_registry={}, llm_client=mock,
        user_text="analyse", agent_resolver=resolver,
    )

    briefings = [
        json.loads(p.read_text(encoding="utf-8"))[1]["content"]
        for p in conv.glob("subagent_*.json")
    ]
    assert any("ANGLE: GROUNDING" in b and "analysis report" in b for b in briefings)
    assert not any("Vetted approach" in b or "ANGLE: THESIS" in b for b in briefings)


# ---- A1 : code-runner must actually change the repo (anti hallucinated success) ----


def _delegate_results(mock) -> list[dict]:
    """The delegate_to tool results the router saw (parsed payloads)."""
    msgs = mock.calls_v2[-1]["messages"]
    return [
        json.loads(m["content"]) for m in msgs
        if m.get("role") == "tool" and m.get("tool_name") == "delegate_to"
    ]


@requires_git
def test_code_runner_unchanged_repo_downgraded_to_low(wt):
    """code-runner reports HIGH but the worktree is UNCHANGED (it described the edits
    instead of applying them) → the router sees a LOW result + corrective, never a
    false success (conv 825fb5b3)."""
    conv, _ = wt
    main_agent = make_agent("code-router", role="router", delegation_targets={"code-runner"})

    def resolver(code):
        return make_agent(code, role="specialist") if code == "code-runner" else None

    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="code-runner", briefing="edit sample.py to add Y")]),
        assistant_response("", tool_calls=[tool_call(   # CLAIMS success, writes nothing
            "report_back", summary="I've added Y to sample.py", files_produced=[], confidence="high")]),
        assistant_response("Done."),
    ])
    run_main_loop(conv_folder=conv, agent=main_agent, tools_registry={}, llm_client=mock,
                  user_text="add Y", agent_resolver=resolver)
    res = _delegate_results(mock)
    assert res and res[-1]["confidence"] == "low"
    assert "UNCHANGED" in res[-1].get("low_confidence_reason", "")


@requires_git
def test_code_runner_with_real_diff_stays_high(wt):
    """When the worktree HAS changes, a high-confidence code-runner result is trusted
    (no spurious downgrade)."""
    conv, root = wt
    (root / "sample.py").write_text("X = 2  # changed\n", encoding="utf-8")  # real diff present
    main_agent = make_agent("code-router", role="router", delegation_targets={"code-runner"})

    def resolver(code):
        return make_agent(code, role="specialist") if code == "code-runner" else None

    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="code-runner", briefing="change X")]),
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="changed X to 2", files_produced=[], confidence="high")]),
        assistant_response("Done."),
    ])
    run_main_loop(conv_folder=conv, agent=main_agent, tools_registry={}, llm_client=mock,
                  user_text="change X", agent_resolver=resolver)
    res = _delegate_results(mock)
    assert res and res[-1]["confidence"] == "high"

"""Determinism map of the orchestrator — generated reference.

The orchestrator is becoming a black box: this emits a single, regenerable
reference of everything DETERMINISTIC (code-controlled, not LLM-decided) —
the tunable parameters with their LIVE values (read from `config` at runtime,
so they never go stale) and a registry of the control points (hooks, gates,
pipelines) with their code anchors.

    ./jm.sh --orchestrator-map            # writes docs/orchestrator_determinism.md
    ./jm.sh --orchestrator-map --stdout

NARRATIVE / rationale lives in README.md (§Orchestrateur, §Hooks, §Compaction).
This file is the generated *reference* (live values + where to look in the code),
NOT a second narrative — keep prose in the README, keep values here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import config, deliberation

# ---- Tunable parameters : (category, config attribute, env var, governs) ----
# Live values are read from the `config` module at render time.
_PARAMS: list[tuple[str, str, str, str]] = [
    ("Delegation & budgets", "MAX_DEPTH", "JEANMICHEL_MAX_DEPTH", "max nested delegation depth (PreToolUse deny beyond)"),
    ("Delegation & budgets", "MAX_DELEGATIONS", "JEANMICHEL_MAX_DELEGATIONS", "hard cap on delegations per turn"),
    ("Delegation & budgets", "MAX_SEARCH_CALLS_PER_TURN", "JEANMICHEL_MAX_SEARCH_TURN", "turn-wide search-tool budget (PreToolUse)"),
    ("Wall-clock", "LLM_CALL_TIMEOUT_SECONDS", "JEANMICHEL_LLM_TIMEOUT", "single LLM call timeout"),
    ("Wall-clock", "REQUEST_WALL_CLOCK_SECONDS", "JEANMICHEL_REQUEST_TIMEOUT", "one agent request budget"),
    ("Wall-clock", "TURN_WALL_CLOCK_SECONDS", "JEANMICHEL_TURN_TIMEOUT", "whole-turn budget (router + children)"),
    ("Wall-clock", "SOFT_DEADLINE_RATIO", "JEANMICHEL_SOFT_DEADLINE_RATIO", "fraction of budget after which tools are restricted to conclude"),
    ("Wall-clock", "ASK_HUMAN_TIMEOUT_SECONDS", "JEANMICHEL_ASK_HUMAN_TIMEOUT", "ask_human wait before fallback"),
    ("Compaction & context", "COMPACTION_THRESHOLDS", "—", "WORKING-budget escalade thresholds (snip/microcompact/collapse/autocompact)"),
    ("Compaction & context", "OUTPUT_RESERVE_RATIO", "JEANMICHEL_OUTPUT_RESERVE_RATIO", "context reserved for the final answer"),
    ("Compaction & context", "MICROCOMPACT_TOKEN_THRESHOLD", "JEANMICHEL_MICROCOMPACT_THRESHOLD", "tool-result size above which microcompaction stubs it"),
    ("Compaction & context", "SUBAGENT_BUDGET_RATIO", "JEANMICHEL_SUBAGENT_BUDGET_RATIO", "fraction of parent budget given to a subagent"),
    ("Compaction & context", "DEFAULT_MODEL_CONTEXT_WINDOW", "JEANMICHEL_DEFAULT_CTX_WINDOW", "assumed ctx window when a model is unknown"),
    ("Code mode", "CODE_WORKTREE_ENABLED", "JEANMICHEL_CODE_WORKTREE_ENABLED", "opt-in: isolate code-mode convs in a git worktree"),
    ("Code mode", "REPO_TEST_CMD", "JEANMICHEL_REPO_TEST_CMD", "repo_test command run in the worktree"),
    ("Code mode", "REPO_TEST_TIMEOUT", "JEANMICHEL_REPO_TEST_TIMEOUT", "repo_test timeout"),
    ("Code mode", "REPO_PROTECTED_PATHS", "—", "paths repo_edit/repo_write must never touch"),
    ("Memory caps", "MEMORY_WORLD_CAP", "JEANMICHEL_MEMORY_WORLD_CAP", "world-scope entries injected"),
    ("Memory caps", "MEMORY_USER_CAP", "JEANMICHEL_MEMORY_USER_CAP", "user-scope entries injected"),
    ("Memory caps", "MEMORY_PROJECT_CAP", "JEANMICHEL_MEMORY_PROJECT_CAP", "project-scope entries injected"),
    ("Memory caps", "MEMORY_TOOL_CAP_PER_TOOL", "JEANMICHEL_MEMORY_TOOL_CAP", "tool-note entries per granted tool"),
    ("Models (per role/mode)", "DISPATCH_MODEL", "JEANMICHEL_DISPATCH_MODEL", "Tier-0 dispatcher"),
    ("Models (per role/mode)", "MAIN_MODEL", "JEANMICHEL_MAIN_MODEL", "router default (non-code)"),
    ("Models (per role/mode)", "CODE_MODEL", "JEANMICHEL_CODE_MODEL", "router model in code mode"),
    ("Models (per role/mode)", "SUBAGENT_DEFAULT_MODEL", "JEANMICHEL_SUBAGENT_MODEL", "specialist default (unless model_override)"),
    ("Models (per role/mode)", "COMPACTOR_MODEL", "JEANMICHEL_COMPACTOR_MODEL", "LLM used for compaction levels 3-4"),
    ("MCP", "MCP_CALL_TIMEOUT_SECONDS", "JEANMICHEL_MCP_CALL_TIMEOUT", "per MCP tool-call timeout"),
    ("MCP", "MCP_MAX_TOOLS_PER_SERVER", "JEANMICHEL_MCP_MAX_TOOLS_PER_SERVER", "cap on tools exposed per MCP server"),
]

# ---- Control points : deterministic mechanisms with code anchors -----------
_CONTROL_POINTS: list[dict[str, str]] = [
    {"name": "Tier-0 dispatch", "kind": "router", "where": "src/jeanmichel/dispatcher.py",
     "governs": "DISPATCH_MODEL", "summary": "classify alexa vs deep (JSON-forced, temp 0); code mode always forces deep."},
    {"name": "PreLLMCall hook", "kind": "hook", "where": "src/jeanmichel/hooks.py · PreLLMCall",
     "governs": "COMPACTION_THRESHOLDS, MICROCOMPACT_TOKEN_THRESHOLD", "summary": "compaction escalade before each LLM call + TODO recap re-injection (main agent only)."},
    {"name": "PreToolUse hook", "kind": "gate", "where": "src/jeanmichel/hooks.py · PreToolUse",
     "governs": "MAX_DEPTH, MAX_SEARCH_CALLS_PER_TURN", "summary": "grant check + delegation whitelist + depth cap + search budget + contextual dedup (deny → tool_error)."},
    {"name": "PostToolUse hook", "kind": "hook", "where": "src/jeanmichel/hooks.py · PostToolUse",
     "governs": "—", "summary": "counter updates, dedup-cache population, force-persist nudge after N research calls."},
    {"name": "OnDelegateReturn hook", "kind": "hook", "where": "src/jeanmichel/hooks.py · OnDelegateReturn",
     "governs": "—", "summary": "push structured SubResult into parent messages; reject confidence=low without a reason."},
    {"name": "Compaction escalade", "kind": "pipeline", "where": "src/jeanmichel/compaction.py",
     "governs": "COMPACTION_THRESHOLDS, OUTPUT_RESERVE_RATIO", "summary": "4 levels: snip / microcompact (deterministic) → context collapse / autocompact (LLM)."},
    {"name": "Memory inclusion", "kind": "pipeline", "where": "src/jeanmichel/prompts.py · render_memory_block + db.py:73",
     "governs": "MEMORY_*_CAP", "summary": "100% SQL scope-driven injection (world/user/project/tool); paradigms gated by paradigm_modes."},
    {"name": "Wall-clock guards", "kind": "budget", "where": "src/jeanmichel/orchestrator_v2.py · _run_agent_loop",
     "governs": "LLM/REQUEST/TURN timeouts, SOFT_DEADLINE_RATIO", "summary": "nested timeouts; soft deadline restricts tools to the conclusion verb to wrap up gracefully."},
    {"name": "Worktree isolation", "kind": "code-mode", "where": "src/jeanmichel/worktree.py",
     "governs": "CODE_WORKTREE_ENABLED", "summary": "code-mode conv gets an isolated git worktree (branch jm/conv-<id>); live tree untouched."},
    {"name": "Repo edit gates", "kind": "gate", "where": "src/jeanmichel/tools/_repo.py · edit_preflight + repo_edit/repo_write",
     "governs": "REPO_PROTECTED_PATHS", "summary": "read-before-edit + freshness (mtime) + protected-path deny — in the tool layer."},
    {"name": "Context packet (CRP)", "kind": "pipeline", "where": "src/jeanmichel/context_packet.py",
     "governs": "—", "summary": "deterministic per-delegation context (graphify + grep + source + git-diff + memory); code mode only."},
    {"name": "repo_test", "kind": "code-mode", "where": "src/jeanmichel/tools/repo_test.py",
     "governs": "REPO_TEST_CMD, REPO_TEST_TIMEOUT", "summary": "structured test result (passed/failed/counts) instead of raw stdout."},
    {"name": "Deliberation trigger", "kind": "gate", "where": "src/jeanmichel/deliberation.py · should_deliberate",
     "governs": "complexity_probe, MAX_REWORK", "summary": "selective: code worker + worktree + hard step → thesis/antithesis/synthesis + KISS gate (bounded REWORK)."},
]


def _fmt(value: object) -> str:
    if value == "":
        return "(auto)"
    if isinstance(value, (tuple, list)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def render_orchestrator_map() -> str:
    extra = {"complexity_probe": "keywords / >=2 files", "MAX_REWORK": deliberation.MAX_REWORK}

    # Parameters grouped by category, live values from config.
    param_lines = ["| Parameter | Live value | Env var | Governs |", "|---|---|---|---|"]
    last_cat = None
    for cat, attr, env, governs in _PARAMS:
        if cat != last_cat:
            param_lines.append(f"| **{cat}** | | | |")
            last_cat = cat
        val = getattr(config, attr, "<unset>")
        param_lines.append(f"| `{attr}` | `{_fmt(val)}` | `{env}` | {governs} |")
    params = "\n".join(param_lines)

    cp_lines = ["| Control point | Kind | Code | Governed by | What it enforces |",
                "|---|---|---|---|---|"]
    for cp in _CONTROL_POINTS:
        gov = cp["governs"]
        cp_lines.append(
            f"| **{cp['name']}** | {cp['kind']} | `{cp['where']}` | {gov} | {cp['summary']} |"
        )
    control = "\n".join(cp_lines)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# Orchestrator — determinism map\n\n"
        f"> Généré le {stamp}. Valeurs **lues en live** depuis `config` ; le registre des points "
        f"de contrôle est ancré au code. Régénérer : `./jm.sh --orchestrator-map`.\n"
        f">\n"
        f"> **Le narratif (pourquoi / comment) vit dans `README.md`** (§Orchestrateur, §Hooks, "
        f"§Compaction). Ce fichier est la *référence* (quoi / quelle valeur / où dans le code) — "
        f"pas une seconde prose.\n\n"
        f"Tout ici est **déterministe** (décidé par du code Python, pas par un LLM). Ce que le LLM "
        f"décide (quel step, quel worker, quand conclure) est *hors* de cette carte — c'est "
        f"précisément ce que ces garde-fous encadrent.\n\n"
        f"## Paramètres réglables (valeurs live)\n\n{params}\n\n"
        f"Déclencheur de délibération : `complexity_probe` = {extra['complexity_probe']} ; "
        f"`MAX_REWORK` = {extra['MAX_REWORK']}.\n\n"
        f"## Points de contrôle déterministes\n\n{control}\n\n"
        f"Voir aussi le synoptic des agents : `docs/agents_synoptic.md` (`./jm.sh --synoptic`).\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the orchestrator determinism map.")
    parser.add_argument("--out", default=str(config.REPO_ROOT / "docs" / "orchestrator_determinism.md"))
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    md = render_orchestrator_map()
    if args.stdout:
        sys.stdout.write(md)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"orchestrator map written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

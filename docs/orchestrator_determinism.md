# Orchestrator — determinism map

> Généré le 2026-06-18 17:13 UTC. Valeurs **lues en live** depuis `config` ; le registre des points de contrôle est ancré au code. Régénérer : `./jm.sh --orchestrator-map`.
>
> **Le narratif (pourquoi / comment) vit dans `README.md`** (§Orchestrateur, §Hooks, §Compaction). Ce fichier est la *référence* (quoi / quelle valeur / où dans le code) — pas une seconde prose.

Tout ici est **déterministe** (décidé par du code Python, pas par un LLM). Ce que le LLM décide (quel step, quel worker, quand conclure) est *hors* de cette carte — c'est précisément ce que ces garde-fous encadrent.

## Paramètres réglables (valeurs live)

| Parameter | Live value | Env var | Governs |
|---|---|---|---|
| **Delegation & budgets** | | | |
| `MAX_DEPTH` | `5` | `JEANMICHEL_MAX_DEPTH` | max nested delegation depth (PreToolUse deny beyond) |
| `MAX_DELEGATIONS` | `8` | `JEANMICHEL_MAX_DELEGATIONS` | hard cap on delegations per turn |
| `MAX_SEARCH_CALLS_PER_TURN` | `10` | `JEANMICHEL_MAX_SEARCH_TURN` | turn-wide search-tool budget (PreToolUse) |
| **Wall-clock** | | | |
| `LLM_CALL_TIMEOUT_SECONDS` | `600` | `JEANMICHEL_LLM_TIMEOUT` | single LLM call timeout |
| `REQUEST_WALL_CLOCK_SECONDS` | `1800` | `JEANMICHEL_REQUEST_TIMEOUT` | one agent request budget |
| `TURN_WALL_CLOCK_SECONDS` | `3600` | `JEANMICHEL_TURN_TIMEOUT` | whole-turn budget (router + children) |
| `SOFT_DEADLINE_RATIO` | `0.75` | `JEANMICHEL_SOFT_DEADLINE_RATIO` | fraction of budget after which tools are restricted to conclude |
| `ASK_HUMAN_TIMEOUT_SECONDS` | `300` | `JEANMICHEL_ASK_HUMAN_TIMEOUT` | ask_human wait before fallback |
| **Compaction & context** | | | |
| `COMPACTION_THRESHOLDS` | `0.7, 0.8, 0.9, 0.95` | `—` | WORKING-budget escalade thresholds (snip/microcompact/collapse/autocompact) |
| `OUTPUT_RESERVE_RATIO` | `0.15` | `JEANMICHEL_OUTPUT_RESERVE_RATIO` | context reserved for the final answer |
| `MICROCOMPACT_TOKEN_THRESHOLD` | `1500` | `JEANMICHEL_MICROCOMPACT_THRESHOLD` | tool-result size above which microcompaction stubs it |
| `SUBAGENT_BUDGET_RATIO` | `0.4` | `JEANMICHEL_SUBAGENT_BUDGET_RATIO` | fraction of parent budget given to a subagent |
| `DEFAULT_MODEL_CONTEXT_WINDOW` | `32768` | `JEANMICHEL_DEFAULT_CTX_WINDOW` | assumed ctx window when a model is unknown |
| **Code mode** | | | |
| `CODE_WORKTREE_ENABLED` | `True` | `JEANMICHEL_CODE_WORKTREE_ENABLED` | opt-in: isolate code-mode convs in a git worktree |
| `REPO_TEST_CMD` | `(auto)` | `JEANMICHEL_REPO_TEST_CMD` | repo_test command run in the worktree |
| `REPO_TEST_TIMEOUT` | `300` | `JEANMICHEL_REPO_TEST_TIMEOUT` | repo_test timeout |
| `REPO_PROTECTED_PATHS` | `jeanmichel.db, .env, .api_secret, conversations/, backups/, voice_models/, .venv/, .git/` | `—` | paths repo_edit/repo_write must never touch |
| **Memory caps** | | | |
| `MEMORY_USER_CAP` | `40` | `JEANMICHEL_MEMORY_USER_CAP` | user-scope entries injected |
| `MEMORY_PROJECT_CAP` | `30` | `JEANMICHEL_MEMORY_PROJECT_CAP` | project-scope entries injected |
| `MEMORY_TOOL_CAP_PER_TOOL` | `5` | `JEANMICHEL_MEMORY_TOOL_CAP` | tool-note entries per granted tool |
| **Models (per role/mode)** | | | |
| `DISPATCH_MODEL` | `granite4.1:8b` | `JEANMICHEL_DISPATCH_MODEL` | Tier-0 dispatcher |
| `MAIN_MODEL` | `gemma4:26b` | `JEANMICHEL_MAIN_MODEL` | router default (non-code) |
| `CODE_MODEL` | `qwen3:14b` | `JEANMICHEL_CODE_MODEL` | router model in code mode |
| `SUBAGENT_DEFAULT_MODEL` | `gemma4:26b` | `JEANMICHEL_SUBAGENT_MODEL` | specialist default (unless model_override) |
| `COMPACTOR_MODEL` | `gemma4:26b` | `JEANMICHEL_COMPACTOR_MODEL` | LLM used for compaction levels 3-4 |
| **MCP** | | | |
| `MCP_CALL_TIMEOUT_SECONDS` | `25` | `JEANMICHEL_MCP_CALL_TIMEOUT` | per MCP tool-call timeout |
| `MCP_MAX_TOOLS_PER_SERVER` | `30` | `JEANMICHEL_MCP_MAX_TOOLS_PER_SERVER` | cap on tools exposed per MCP server |

Gate de validation (downstream) : `complexity_probe` = keywords / >=2 files (validation gate).

## Points de contrôle déterministes

| Control point | Kind | Code | Governed by | What it enforces |
|---|---|---|---|---|
| **Tier-0 dispatch** | router | `src/jeanmichel/dispatcher.py` | DISPATCH_MODEL | classify alexa vs deep (JSON-forced, temp 0); code mode always forces deep. |
| **PreLLMCall hook** | hook | `src/jeanmichel/hooks.py · PreLLMCall` | COMPACTION_THRESHOLDS, MICROCOMPACT_TOKEN_THRESHOLD | compaction escalade before each LLM call + TODO recap re-injection (main agent only). |
| **PreToolUse hook** | gate | `src/jeanmichel/hooks.py · PreToolUse` | MAX_DEPTH, MAX_SEARCH_CALLS_PER_TURN | grant check + delegation whitelist + depth cap + search budget + contextual dedup (deny → tool_error). |
| **PostToolUse hook** | hook | `src/jeanmichel/hooks.py · PostToolUse` | — | counter updates, dedup-cache population, force-persist nudge after N research calls. |
| **OnDelegateReturn hook** | hook | `src/jeanmichel/hooks.py · OnDelegateReturn` | — | push structured SubResult into parent messages; reject confidence=low without a reason. |
| **Compaction escalade** | pipeline | `src/jeanmichel/compaction.py` | COMPACTION_THRESHOLDS, OUTPUT_RESERVE_RATIO | 4 levels: snip / microcompact (deterministic) → context collapse / autocompact (LLM). |
| **Memory inclusion** | pipeline | `src/jeanmichel/prompts.py · render_memory_block + db.py:73` | MEMORY_*_CAP | 100% SQL scope-driven injection (user/project/tool); paradigms gated by paradigm_modes. |
| **Wall-clock guards** | budget | `src/jeanmichel/orchestrator_v2.py · _run_agent_loop` | LLM/REQUEST/TURN timeouts, SOFT_DEADLINE_RATIO | nested timeouts; soft deadline restricts tools to the conclusion verb to wrap up gracefully. |
| **Worktree isolation** | code-mode | `src/jeanmichel/worktree.py` | CODE_WORKTREE_ENABLED | code-mode conv gets an isolated git worktree (branch jm/conv-<id>); live tree untouched. |
| **Repo edit gates** | gate | `src/jeanmichel/tools/_repo.py · edit_preflight + repo_edit/repo_write` | REPO_PROTECTED_PATHS | read-before-edit + freshness (mtime) + protected-path deny — in the tool layer. |
| **Context packet (CRP)** | pipeline | `src/jeanmichel/context_packet.py` | — | deterministic per-delegation context (grep + source + git-diff + memory); code mode only. |
| **repo_test** | code-mode | `src/jeanmichel/tools/repo_test.py` | REPO_TEST_CMD, REPO_TEST_TIMEOUT | structured test result (passed/failed/counts) instead of raw stdout. |
| **Deliberation gate** | gate | `src/jeanmichel/deliberation.py · validate_deliverable` | complexity_probe | DOWNSTREAM validation: a concrete deliverable (diff / analysis report) checked against the real repo (grounding/correctness/simplicity) + PASS/REWORK gate. Validators, not creatives. |

Voir aussi le synoptic des agents : `docs/agents_synoptic.md` (`./jm.sh --synoptic`).

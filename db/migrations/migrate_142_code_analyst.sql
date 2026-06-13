-- =============================================================================
-- migrate_142_code_analyst.sql
-- =============================================================================
-- Fix the "casting errors" of code mode (forensic: conv 2026-06-13_16-58_127ce9a1,
-- 129 LLM calls for a garbage "owner/repo not specified" answer on a checked-out
-- repo). The code-router had no READ-ONLY analyst cast — only producers
-- (code-runner/node) + an EXTERNAL fetcher (code-fetcher). A read-only ANALYSIS
-- task was therefore mis-cast: code-fetcher asked for owner/repo (its external
-- reflex) and code-runner triggered production deliberation that spun.
--
-- Fix:
--   1) add `code-analyst` — read-only repo analyst (repo_read/grep/glob/git +
--      workspace notes ; NO edit/exec/test ; NOT a CODE_WORKER → no deliberation).
--   2) scope `code-fetcher` to EXTERNAL only (drop repo_read/grep/glob).
--   3) rewrite the code-router mission with 3 explicit casts + never ask owner/repo.
--   4) add routing paradigm `route_analysis_to_code_analyst` (bound to code-router).
--
-- Named-column INSERT for the agent (live table column order differs from
-- schema.sql). Idempotent (INSERT OR IGNORE / DELETE / UPDATE re-runnable).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) New agent : code-analyst (id 22) ----------------------------------------
INSERT OR IGNORE INTO agents
  (id, code, name, role, mission, thinking_mode, temperature, active, sandbox_image, model_override, created_at, modified_at)
VALUES (
  22, 'code-analyst', 'Code Analyst', 'specialist',
  'Read-only analyst of the ATTACHED code repository. Explore, understand and audit it: map structure and dependencies, find usages, answer whether something is still used or how a feature works, and propose plans. You NEVER edit, run, build, or fetch external sources. The repo is a LOCAL git worktree — read it with repo_read/repo_grep/repo_glob and inspect history with repo_git (log, show, diff, status, blame). Write your findings to a workspace markdown file and conclude via report_back, citing path:line. Never ask the human for a repository (owner/repo): the repo is already attached.',
  1, 0.2, 1, NULL, 'qwen3:14b', '2026-06-13 00:00:00', '2026-06-13 00:00:00'
);

-- read-only repo tools + workspace for findings (NO edit/write/exec/test)
INSERT OR IGNORE INTO agent_tools VALUES(22,'repo_read');
INSERT OR IGNORE INTO agent_tools VALUES(22,'repo_grep');
INSERT OR IGNORE INTO agent_tools VALUES(22,'repo_glob');
INSERT OR IGNORE INTO agent_tools VALUES(22,'repo_git');
INSERT OR IGNORE INTO agent_tools VALUES(22,'workspace_view');
INSERT OR IGNORE INTO agent_tools VALUES(22,'workspace_list');
INSERT OR IGNORE INTO agent_tools VALUES(22,'workspace_create_file');
INSERT OR IGNORE INTO agent_tools VALUES(22,'workspace_append');
INSERT OR IGNORE INTO agent_tools VALUES(22,'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools VALUES(22,'manage_memory');

-- code-router can now cast analysis to code-analyst
INSERT OR IGNORE INTO agent_delegation_targets VALUES(21,'code-analyst','2026-06-13 00:00:00');

-- code-analyst inherits the read-only code doctrine
INSERT OR IGNORE INTO agent_paradigms VALUES(22,149);  -- code_space_doctrine
INSERT OR IGNORE INTO agent_paradigms VALUES(22,145);  -- repo_intervention_discipline
INSERT OR IGNORE INTO agent_paradigms VALUES(22,146);  -- prefer_repo_tools_over_bash

-- 2) code-fetcher → EXTERNAL only : drop its repo navigation grants -----------
DELETE FROM agent_tools WHERE agent_id = 17 AND tool_code IN ('repo_read','repo_grep','repo_glob');

UPDATE agents SET
  mission = 'Lookup specialist for EXTERNAL sources only: code, developer documentation, troubleshooting and package metadata from GitHub (github_search_code, github_search_repos), Stack Overflow (stackoverflow_search), PyPI (pypi_lookup) and the web (web_fetch). Surface candidate URLs, then web_fetch the 1-3 most relevant to read full content; synthesize into a workspace markdown file, one section per source (cite repo+path or question+accepted answer). You do NOT touch the ATTACHED repository — analysing it is code-analyst''s job, changing it is code-runner''s. You do not write or execute project code.',
  modified_at = '2026-06-13 00:00:00'
WHERE id = 17 AND code = 'code-fetcher';

-- 3) code-router mission : 3 explicit casts + never ask owner/repo ------------
UPDATE agents SET
  mission = 'Router for code mode: you orchestrate work on the ATTACHED code repository — you do NOT read, write, run, or answer code yourself. Decompose the request into a living TODO (todo_write) and delegate each step to a fresh worker with a precise briefing; the system assembles the repo context. Choose the cast: code-analyst to UNDERSTAND / ANALYSE / AUDIT the repo read-only (is X used, how does Y work, propose a plan); code-runner to PRODUCE or CHANGE code (write/run/test in the worktree); code-fetcher for EXTERNAL lookups only (GitHub/Stack Overflow/PyPI). Read their report_back, revise the TODO, repeat, then synthesize for the human. The repo is a LOCAL worktree reached via repo_* tools — NEVER claim you cannot see it and NEVER ask the human for a repository (owner/repo); to inspect it, delegate to code-analyst.',
  modified_at = '2026-06-13 00:00:00'
WHERE id = 21 AND code = 'code-router';

-- 4) routing paradigm : send repo analysis to code-analyst -------------------
INSERT OR IGNORE INTO paradigms VALUES(
  152, 35, 'route_analysis_to_code_analyst', 'Route repo analysis to code-analyst',
  unistr('- Route any READ-ONLY understanding of the attached repo — analyse, audit, "is X used?", "how does Y work?", explain, map dependencies, propose a plan/cleanup — to code-analyst.
- code-runner is for PRODUCING or CHANGING code (write/run/test); code-fetcher is for EXTERNAL sources only (GitHub/SO/PyPI), never the attached repo.
- The attached repo is a LOCAL worktree reached via repo_* tools. Never ask the human for a repository (owner/repo).'),
  'Bug 2026-06-13 (conv 127ce9a1): a read-only analysis was mis-cast to code-fetcher (external -> asked owner/repo) and code-runner (triggered production deliberation, 129 calls). code-analyst is the read-only cast.',
  0, 36, 1, '2026-06-13 00:00:00', '2026-06-13 00:00:00'
);
INSERT OR IGNORE INTO agent_paradigms VALUES(21,152);  -- bound to code-router

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code FROM agents WHERE code='code-analyst';                         -- 1 row
-- SELECT tool_code FROM agent_tools WHERE agent_id=17 AND tool_code LIKE 'repo_%'; -- empty
-- SELECT target_code FROM agent_delegation_targets WHERE agent_id=21;        -- incl. code-analyst
-- SELECT COUNT(*) FROM agents WHERE active=1;                                -- 20
-- SELECT COUNT(*) FROM paradigms WHERE active=1;                             -- 127

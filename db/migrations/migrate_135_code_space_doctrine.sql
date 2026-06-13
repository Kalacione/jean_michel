-- =============================================================================
-- migrate_135_code_space_doctrine.sql
-- =============================================================================
-- Étage A of the "code chain audit" fix (docs/20260612_improve_thinking/
-- audit_de_la_bouse.md). The coding worker reached for bash_sandbox to inspect
-- the repo (and failed: the sandbox is network-less and mounts only the scratch),
-- because its loud, ALL-mode paradigms (workspace_tools_only @prio 10, the
-- sandbox-centric mission) drowned the buried code-only repo paradigms (@prio
-- 38-39). Three moves, all in code mode only:
--   1) NEW paradigm `code_space_doctrine` (prio 8 → leads the behavioural
--      paradigms) teaching the three spaces: REPOSITORY (repo_* tools, the source
--      of truth) / WORKSPACE (scratch, reports & throwaway) / SANDBOX (generated
--      code only, cannot see the repo).
--   2) GATE `workspace_tools_only` OUT of code mode (it claims the scratch is "the
--      source of truth" — false when a repo is attached). It stays for the other
--      modes.
--   3) GRANT the new read-only `repo_git` tool to both coding workers — git
--      history/status/diff is the one repo question no other repo_* tool answers.
--
-- Idempotent: NOT EXISTS guard on the paradigm insert; INSERT OR IGNORE on the
-- (PK-backed) paradigm_modes / agent_paradigms / agent_tools join tables.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) code_space_doctrine (Process / tool_discipline = category 29) ------------
INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 29, 'code_space_doctrine', 'Code space doctrine',
'In code mode you work on the project''s REPOSITORY, checked out in an isolated git worktree — that is where the real work and the files you create belong. Three distinct spaces; do not confuse them. REPOSITORY (the attached repo): read, search, and find its files with repo_read / repo_grep / repo_glob, edit them with repo_edit / repo_write, inspect its history with repo_git (log / show / diff / status / blame), and run its tests with repo_test. This is the source of truth and where the bulk of the task happens. WORKSPACE (the per-conversation scratch, the workspace_* tools): use it only for reports, notes, or throwaway snippets — NOT as the place the task''s code lives when a repo is attached. SANDBOX (bash_sandbox): a locked, network-less container that mounts ONLY the scratch workspace and CANNOT see the repository; use it to run generated or throwaway code you wrote in the workspace — never to read, search, or run git or other commands against the attached repo (it will fail: the repo is not mounted there). To inspect or query the repo, always use the repo_* tools, never bash.',
'Étage A / code chain audit: a high-priority code-only doctrine that resolves the contradiction between the loud sandbox/workspace-centric paradigms and the buried repo paradigms, so the worker uses the repo (repo_* tools) instead of defaulting to bash_sandbox.',
0, 8, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'code_space_doctrine');

-- gate the new doctrine to 'code' only (anti-leak) ----------------------------
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'code' FROM paradigms WHERE code = 'code_space_doctrine';

-- bind the doctrine to BOTH coding workers -----------------------------------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a
CROSS JOIN paradigms p
WHERE a.code IN ('code-runner', 'code-runner-node')
  AND p.code = 'code_space_doctrine';

-- 2) gate workspace_tools_only OUT of code (it currently has NO mode rows ⇒ ALL
--    modes ; adding analyse/chat/vocal restricts it to those, excluding code) --
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT p.id, m.mode
FROM paradigms p
CROSS JOIN (SELECT 'analyse' AS mode UNION SELECT 'chat' UNION SELECT 'vocal') m
WHERE p.code = 'workspace_tools_only';

-- 3) grant the read-only repo_git tool to both coding workers ----------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'repo_git'
FROM agents a
WHERE a.code IN ('code-runner', 'code-runner-node');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT order_priority FROM paradigms WHERE code='code_space_doctrine';  -- 8
-- SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id
--   WHERE p.code='code_space_doctrine';                                   -- code
-- SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id
--   WHERE p.code='workspace_tools_only';                                  -- analyse, chat, vocal (NOT code)
-- SELECT COUNT(*) FROM agent_tools t JOIN agents a ON a.id=t.agent_id
--   WHERE t.tool_code='repo_git' AND a.code IN ('code-runner','code-runner-node'); -- 2

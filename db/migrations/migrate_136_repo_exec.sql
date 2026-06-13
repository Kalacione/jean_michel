-- =============================================================================
-- migrate_136_repo_exec.sql
-- =============================================================================
-- Étage B (project sandbox) — wire the new `repo_exec` tool:
--   1) GRANT repo_exec to both coding workers (code-runner, code-runner-node).
--   2) EXTEND code_space_doctrine to name the 4th space — the PROJECT SANDBOX:
--      `repo_exec` runs arbitrary commands AGAINST the repo (build/lint/run/
--      move/rename/delete) in a per-project, offline, confined container —
--      distinct from bash_sandbox (scratch only, cannot see the repo).
--
-- repo_exec executes in a Docker container that mounts the repo worktree at /app,
-- --network=none, --user host-uid, --cap-drop=ALL (cf. tools/repo_exec.py). No
-- per-command allowlist: the confinement is the container; the worktree is
-- git-isolated and disposable.
--
-- Idempotent: INSERT OR IGNORE on the grant; UPDATE sets a fixed content value.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) grant repo_exec to both coding workers --------------------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'repo_exec'
FROM agents a
WHERE a.code IN ('code-runner', 'code-runner-node');

-- 2) extend code_space_doctrine (the PROJECT SANDBOX / repo_exec) -----------
UPDATE paradigms
SET content = 'In code mode you work on the project''s REPOSITORY, checked out in an isolated git worktree — that is where the real work and the files you create belong. Distinct spaces; do not confuse them. REPOSITORY (the attached repo): read, search, and find its files with repo_read / repo_grep / repo_glob, edit them with repo_edit / repo_write, inspect its history with repo_git (log / show / diff / status / blame), and run its tests with repo_test. This is the source of truth and where the bulk of the task happens. PROJECT SANDBOX (repo_exec): to RUN arbitrary commands against the attached repo — build, lint, run a script, move/rename/delete files — use repo_exec; it runs inside a per-project container that mounts the repo, offline and confined (no network, no host access). WORKSPACE (the per-conversation scratch, the workspace_* tools): use it only for reports, notes, or throwaway snippets — NOT as the place the task''s code lives when a repo is attached. SANDBOX (bash_sandbox): a locked, network-less container that mounts ONLY the scratch workspace and CANNOT see the repository; use it solely to run generated or throwaway code you wrote in the workspace. To inspect the repo use the repo_* tools (repo_git for git); to run commands against it use repo_exec; never bash_sandbox for the repo.',
    modified_at = datetime('now')
WHERE code = 'code_space_doctrine';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT COUNT(*) FROM agent_tools t JOIN agents a ON a.id=t.agent_id
--   WHERE t.tool_code='repo_exec' AND a.code IN ('code-runner','code-runner-node'); -- 2
-- SELECT content LIKE '%repo_exec%' FROM paradigms WHERE code='code_space_doctrine'; -- 1

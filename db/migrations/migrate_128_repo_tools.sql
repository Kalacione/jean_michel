-- =============================================================================
-- migrate_128_repo_tools.sql
-- =============================================================================
-- P1 of the codebase-intervention plan: grant the deterministic repo_* tools
-- (in-place edits on a git worktree of PROJECT_ROOT, code mode) to the coding
-- agents.
--   * code-runner / code-runner-node : full set (read/grep/glob/edit/write).
--   * code-fetcher                    : read-only navigation (read/grep/glob).
--
-- The tools are only REGISTERED at runtime when a worktree exists (code mode);
-- these grants gate which agents may call them. No leakage into other modes.
-- Idempotent (INSERT OR IGNORE on the (agent_id, tool_code) PK).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- Full repo toolkit for the coding workers (Python + Node).
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, v.tool_code
FROM agents a
CROSS JOIN (
    SELECT 'repo_read'  AS tool_code UNION ALL
    SELECT 'repo_grep'  UNION ALL
    SELECT 'repo_glob'  UNION ALL
    SELECT 'repo_edit'  UNION ALL
    SELECT 'repo_write'
) v
WHERE a.code IN ('code-runner', 'code-runner-node');

-- Read-only navigation for the lookup specialist.
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, v.tool_code
FROM agents a
CROSS JOIN (
    SELECT 'repo_read'  AS tool_code UNION ALL
    SELECT 'repo_grep'  UNION ALL
    SELECT 'repo_glob'
) v
WHERE a.code = 'code-fetcher';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code, COUNT(*) FROM agent_tools t JOIN agents a ON a.id = t.agent_id
--   WHERE t.tool_code LIKE 'repo_%' GROUP BY a.code;
--   -- code-fetcher | 3   code-runner | 5   code-runner-node | 5

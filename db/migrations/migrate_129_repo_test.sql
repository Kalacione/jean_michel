-- =============================================================================
-- migrate_129_repo_test.sql
-- =============================================================================
-- P3 of the codebase-intervention plan: grant the structured test runner and
-- the code-graph refresh tool to the coding workers (Python + Node).
--   * repo_test          : run the project's tests in the worktree, structured result.
--   * repo_graph_refresh : rebuild the graphify code graph (graphify update).
-- Idempotent (INSERT OR IGNORE on the (agent_id, tool_code) PK).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, v.tool_code
FROM agents a
CROSS JOIN (
    SELECT 'repo_test'          AS tool_code UNION ALL
    SELECT 'repo_graph_refresh'
) v
WHERE a.code IN ('code-runner', 'code-runner-node');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code, COUNT(*) FROM agent_tools t JOIN agents a ON a.id = t.agent_id
--   WHERE t.tool_code IN ('repo_test','repo_graph_refresh') GROUP BY a.code;
--   -- code-runner | 2   code-runner-node | 2

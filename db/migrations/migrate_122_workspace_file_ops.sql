-- =============================================================================
-- migrate_122_workspace_file_ops.sql
-- =============================================================================
-- Grant the new workspace file-management tools (workspace_create_dir,
-- workspace_delete_file, workspace_delete_dir — cf. DevNotes/ORCHESTRATOR/04 R4,
-- ported from ollamacode's file-op palette) to the agents that manage files:
-- code-runner (multi-file code editing) and workspace-manager.
-- Idempotent (INSERT OR IGNORE on the agent_tools PK).
-- =============================================================================

PRAGMA foreign_keys = ON;
BEGIN TRANSACTION;

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_create_dir' FROM agents WHERE code IN ('code-runner', 'workspace-manager');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_delete_file' FROM agents WHERE code IN ('code-runner', 'workspace-manager');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_delete_dir' FROM agents WHERE code IN ('code-runner', 'workspace-manager');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code, group_concat(at.tool_code) FROM agent_tools at
--   JOIN agents a ON a.id=at.agent_id
--   WHERE at.tool_code LIKE 'workspace_%dir' OR at.tool_code='workspace_delete_file'
--   GROUP BY a.code;

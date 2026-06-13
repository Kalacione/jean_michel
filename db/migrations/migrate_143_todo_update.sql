-- =============================================================================
-- migrate_143_todo_update.sql
-- =============================================================================
-- Bug A (conv dfcafc75) : the router adds TODO items but never marks them done —
-- todo_write is a WHOLE-LIST replace, too hard for a small model to re-emit just
-- to flip one status, so items stay 'pending' (no progress, no resume).
--
-- Grant the new granular `todo_update(item_id, status)` tool to the agents that
-- already own the plan (= those with todo_write): jean-michel (1) + code-router (21).
-- Idempotent (INSERT OR IGNORE).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO agent_tools VALUES(1,'todo_update');   -- jean-michel (router)
INSERT OR IGNORE INTO agent_tools VALUES(21,'todo_update');  -- code-router

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id
--   WHERE t.tool_code='todo_update';   -- jean-michel, code-router

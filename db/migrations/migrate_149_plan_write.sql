-- =============================================================================
-- migrate_149_plan_write.sql
-- =============================================================================
-- Rich plan document (Claude-style two-artifact model). The PLAN turn now authors
-- a SUBSTANTIVE markdown plan (Context/analysis, steps with detail + rationale,
-- verification) via the new `plan_write(markdown)` tool — stored at plan.md and
-- re-injected into every execution turn — while todo.json stays the terse tracker.
-- The bare-todo plan carried no analysis, which drove weak/inconsistent execution.
--
-- Grant `plan_write` to the agents that own the plan (= those with todo_write):
-- jean-michel (1, router) + code-router (21). Idempotent (INSERT OR IGNORE).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO agent_tools VALUES(1,'plan_write');   -- jean-michel (router)
INSERT OR IGNORE INTO agent_tools VALUES(21,'plan_write');  -- code-router

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id
--   WHERE t.tool_code='plan_write';   -- jean-michel, code-router

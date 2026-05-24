-- MIGRATION 029 — planner workspace write grant
-- Le planner crée workspace/plan.md — il lui faut agent_workspace_grants.

INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'planner';

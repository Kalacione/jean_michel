-- MIGRATION 035 — workspace write grant for research specialist agents
-- =====================================================================
-- web-search-specialist and wikipedia-specialist had workspace_create_file
-- in agent_tools but were missing from agent_workspace_grants.
-- They need write access to deliver their research output as workspace files
-- (sources_found.md, encyclopedic_sources.md, etc. as specified in plans).

INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code IN ('web-search-specialist', 'wikipedia-specialist');

-- =============================================================================
-- migrate_115_image_search.sql
-- =============================================================================
-- Grant the image_search tool (SearXNG images category) to the router and the
-- web-search-specialist. Tool code: src/jeanmichel/tools/image_search.py (+ the
-- registry in tools/__init__.py). Additive + idempotent (INSERT OR IGNORE on
-- the agent_tools PK), so it is safe to re-apply.
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'image_search' FROM agents WHERE code IN ('jean-michel', 'web-search-specialist');

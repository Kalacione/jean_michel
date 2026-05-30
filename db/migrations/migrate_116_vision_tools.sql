-- =============================================================================
-- migrate_116_vision_tools.sql
-- =============================================================================
-- Grant the vision tools to the router + the web-search-specialist :
--   - analyze_image : transient gemma4 vision call on a workspace image → text.
--   - image_fetch   : download a web image into the workspace (SSRF-guarded).
-- Code: src/jeanmichel/tools/analyze_image.py + image_fetch.py (+ the registry).
-- Additive + idempotent (INSERT OR IGNORE on the agent_tools PK).
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, t.code
FROM agents a
CROSS JOIN (SELECT 'analyze_image' AS code UNION ALL SELECT 'image_fetch') t
WHERE a.code IN ('jean-michel', 'web-search-specialist');

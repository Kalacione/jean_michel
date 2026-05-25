-- MIGRATION 059 — coherence audit fixes (2026-05-25)
-- =====================================================
-- Five drifts surfaced by a paradigm/tools coherence audit:
--   1. archivist was bound to document_workspace_output, but archivist has
--      ZERO tools — the paradigm tells it to call workspace_create_file, a
--      cul-de-sac. Unbind.
--   2. web-search-specialist's document_workspace_output paradigm references
--      workspace_str_replace, but the tool was not granted. Grant it so the
--      agent can iterate on its output file instead of recreating it.
--   3. wikipedia-specialist was bound to wikipedia_deliver_directly, which is
--      already inactive (filtered out at load time) — just a residue. Unbind.
--   4. Sync the workspace_append grant for every existing writer (this was
--      done by migration 056_workspace_append at runtime, but never reflected
--      in schema.sql, so fresh installs were starting without it).
--   5. Three paradigms have been inactive for a while and reference removed
--      concepts (planner role, plan_update tool). Drop them.

BEGIN;

-- 1. archivist: drop document_workspace_output binding (paradigm itself stays;
--    other agents still use it).
DELETE FROM agent_paradigms
WHERE agent_id = (SELECT id FROM agents WHERE code = 'archivist')
  AND paradigm_id = (SELECT id FROM paradigms WHERE code = 'document_workspace_output');

-- 2. web-search-specialist: grant workspace_str_replace.
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_str_replace' FROM agents WHERE code = 'web-search-specialist';

-- 3. wikipedia-specialist: unbind the inactive paradigm.
DELETE FROM agent_paradigms
WHERE agent_id = (SELECT id FROM agents WHERE code = 'wikipedia-specialist')
  AND paradigm_id = (SELECT id FROM paradigms WHERE code = 'wikipedia_deliver_directly');

-- 4. workspace_append for every writer (mirror of migration 056_workspace_append,
--    now committed to schema). Idempotent.
INSERT INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'workspace_append'
FROM agents a
JOIN agent_tools at ON at.agent_id = a.id
WHERE at.tool_code = 'workspace_create_file'
ON CONFLICT DO NOTHING;

-- 5. Drop three inactive, obsolete paradigms.
--    - wikipedia_deliver_directly (105): superseded, kept inactive for history.
--    - planner_plan_format (115): planner role was removed in migration 044.
--    - plan_not_execute (116): same — plan_update tool no longer exists.
--    Their agent_paradigms rows are removed above or were never there.
DELETE FROM agent_paradigms WHERE paradigm_id IN (105, 115, 116);
DELETE FROM paradigms WHERE id IN (105, 115, 116) AND active = 0;

COMMIT;

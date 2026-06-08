-- =============================================================================
-- migrate_126_memory_paradigms.sql
-- =============================================================================
-- Align paradigms + grants with the scoped memory system (migrate_125) :
--   1. Rename the tool grant everywhere : manage_user_memory → manage_memory.
--   2. Rename + rewrite the discipline paradigm for scopes + search + shortcuts.
--   3. Add `tool_note_discipline` (when to write a scope='tool' note) — bound to
--      jean-michel. Distinct from paradigms (static) : tool memory is *learned*.
--
-- Idempotent-ish : UPDATEs are safe to re-run ; the INSERT is guarded by code.
-- =============================================================================

BEGIN TRANSACTION;

-- 1. Tool rename (manage_user_memory → manage_memory) on every agent grant.
UPDATE agent_tools SET tool_code = 'manage_memory' WHERE tool_code = 'manage_user_memory';

-- 2. Discipline paradigm : scoped, search-aware, shortcut-aware.
UPDATE paradigms
SET code = 'memory_discipline',
    title = 'Memory discipline',
    content = '- Save memory when something durable emerges: a fact about the human
  (scope user), a project decision (scope project), a reusable lesson about a
  tool (scope tool), or a globally useful fact (scope world).
- Before saving, search existing memory (action=''search'') to avoid duplicates
  and to catch a contradicting entry — extend/update the existing one instead.
- Update an entry when the conversation refines or contradicts it; delete when
  it becomes obsolete.
- Recall (action=''recall'') the full body of an entry whose code you saw in the
  memory index; search when you don''t know the code, before concluding you don''t
  know something.
- Keep entries concise: title < 60 chars, description < 150, content < 1000.
- Use the note_for_<scope> shortcuts when adding a new note.',
    rationale = 'Frames jean-michel''s use of manage_memory. Discipline, not a
mechanical must — the shadow consolidation pass proposes saves for the human to
confirm; nothing is written unattended.'
WHERE code = 'user_memory_discipline';

-- 3. New paradigm : tool-scope note discipline (learned ≠ paradigm).
INSERT INTO paradigms
    (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 143, 29, 'tool_note_discipline', 'Tool note discipline',
    '- A tool note (scope=''tool'') captures a durable, reusable lesson about HOW
  to use a specific tool well: a parameter that matters, a failure mode and its
  fix, an input format the tool expects. Never a one-off result.
- Key it by the tool name (tool_code) so it loads automatically for every agent
  granted that tool.
- Do not restate the tool''s own description or an existing paradigm — record
  only what experience taught.',
    'Distingue la mémoire tool (apprise, éditable au runtime, chargée par grant)
des paradigmes (statiques, écrits en migration). Évite le doublon.',
    0, 61, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'tool_note_discipline');

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'jean-michel' AND p.code = 'tool_note_discipline'
  AND NOT EXISTS (
    SELECT 1 FROM agent_paradigms ap WHERE ap.agent_id = a.id AND ap.paradigm_id = p.id
  );

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code FROM paradigms WHERE code IN ('memory_discipline','tool_note_discipline');
-- SELECT DISTINCT tool_code FROM agent_tools WHERE tool_code LIKE 'manage_%';  -- → manage_memory

-- Migration 019 : workspace_view pour jean-michel + paradigme synthèse web-search
--
-- Deux correctifs issus de l'analyse de la conversation e1236e7ce411 :
--
-- 1. jean-michel n'avait pas workspace_view → il tentait de lire les fichiers
--    workspace via conv_read_file (qui ne lit que les artefacts de conversation)
--    et bouclait jusqu'à épuisement du step budget.
--
-- 2. web-search-specialist enchaînait 20+ recherches sans conclure. On ajoute
--    un paradigme "cherche puis synthétise" qui le force à retourner après
--    4-5 recherches maximum.

-- 1. Grant workspace_view à jean-michel --------------------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_view' FROM agents WHERE code = 'jean-michel';

-- 2. Paradigme search_then_synthesize ----------------------------------
INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  33,  -- catégorie web_search
  'search_then_synthesize',
  'Search then synthesize',
  '- Limit web_search calls to 5 per request maximum. After 3-4 searches, synthesise findings and return_to_user — do not keep searching indefinitely.
- Each search should cover a distinct sub-topic. Do not repeat similar queries.
- If a result URL points to a PDF or requires login, skip it and note it as inaccessible.
- Return a structured summary of all findings, not a raw dump of search results.',
  'Prevents web-search-specialist from burning its step budget on endless searches without ever concluding.',
  0, 5, 1, datetime('now'), datetime('now')
);

-- 3. Binding -----------------------------------------------------------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'web-search-specialist'
  AND p.code = 'search_then_synthesize';

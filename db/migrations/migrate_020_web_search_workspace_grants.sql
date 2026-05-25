-- Migration 020 : workspace write grants pour web-search-specialist
--    + update paradigme search_then_synthesize pour compacter dans workspace
--
-- Problème : web-search-specialist collait ses findings bruts dans le briefing
-- text au lieu d'écrire un fichier compact dans le workspace. Résultat :
-- les briefings explosent en taille sur les tâches de recherche approfondie.
-- Le document-builder recevait tout en inline au lieu de lire un fichier propre.

-- 1. Grants workspace pour web-search-specialist -----------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_create_file' FROM agents WHERE code = 'web-search-specialist';

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_view' FROM agents WHERE code = 'web-search-specialist';

-- 2. Mise à jour du paradigme search_then_synthesize -------------------
UPDATE paradigms SET
  content = '- Limit web_search calls to 5 per request maximum. After 3-4 searches, compact and persist findings — do not keep searching indefinitely.
- Each search should cover a distinct sub-topic. Do not repeat similar queries.
- If a result URL points to a PDF or requires login, skip it and note it as inaccessible.
- After gathering enough results, write a compact structured summary to the workspace via workspace_create_file (file naming: web-search-specialist_<topic-slug>.md). Include source URLs inline. Do NOT dump raw search result JSON.
- Return the workspace file path in your return_to_user answer so the calling agent can reference it in subsequent briefings.',
  modified_at = datetime('now')
WHERE code = 'search_then_synthesize';

-- Migration 017 : web-search-specialist + outil web_search
--
-- Ajoute un agent spécialiste dédié à la recherche web via SearXNG,
-- un paradigme de discipline propre à cet agent, et les grants nécessaires.

-- 1. Agent ----------------------------------------------------------------
INSERT OR IGNORE INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES (
  'web-search-specialist',
  'Web Search Specialist',
  'specialist',
  'Search the web for current information, news, and facts not covered by Wikipedia. Use web_search to retrieve results, select the most relevant hits, summarise findings clearly with source URLs. Never fabricate URLs.',
  1, 0.2, 1, datetime('now'), datetime('now')
);

-- 2. Catégorie + paradigme ------------------------------------------------
INSERT OR IGNORE INTO categories (section_id, code, title, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT id FROM sections WHERE code = 'process'),
  'web_search', 'Web Search', 55, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT c.id FROM categories c JOIN sections s ON s.id = c.section_id
   WHERE s.code = 'process' AND c.code = 'web_search'),
  'web_search_discipline',
  'Web search discipline',
  '- Always include the source URL alongside each piece of information retrieved from web_search.
- Prefer recent results; note the recency when it matters.
- Do not invent or guess URLs — only report URLs returned by the tool.
- If results are insufficient, reformulate the query and search again before concluding.',
  'Keeps web-search responses grounded and traceable.',
  0, 10, 1, datetime('now'), datetime('now')
);

-- 3. Paradigme binding ----------------------------------------------------
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'web-search-specialist'
  AND p.code IN (
    'web_search_discipline',
    'faithful_to_sources',
    'omit_unsourced_claims'
  );

-- 4. Grants outils --------------------------------------------------------
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'web_search' FROM agents WHERE code = 'web-search-specialist';

-- 5. Grant web_search à jean-michel (routing + fallback direct) -----------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'web_search' FROM agents WHERE code = 'jean-michel';

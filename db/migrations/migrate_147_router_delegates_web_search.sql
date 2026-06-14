-- =============================================================================
-- migrate_147_router_delegates_web_search.sql
-- =============================================================================
-- Le routeur jean-michel avait web_search en outil DIRECT → pour les requêtes simples
-- il court-circuitait web-search-specialist (qui, lui, écrit ses findings dans un
-- fichier workspace + omet les claims non sourcés). Résultat : aucune trace fichier,
-- pas le grounding du spécialiste (conv 2026-06-14 couscous). On force la délégation :
-- retrait du grant web_search + doctrine. « Chaque agent sa spécialité, pas de
-- spécialiste en tout. » Idempotent.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) jean-michel (id 1) ne cherche plus le web lui-même → il délègue
DELETE FROM agent_tools WHERE agent_id = 1 AND tool_code = 'web_search';

-- 2) doctrine : déléguer la recherche web au spécialiste
INSERT OR IGNORE INTO paradigms VALUES(
  155, 33, 'delegate_web_search', 'Delegate web research to the specialist',
  '- Do NOT search the web yourself. Delegate any web research to web-search-specialist: it searches AND writes its findings to a workspace file (the reusable handoff), and omits unsourced claims.
- You orchestrate and synthesize ; specialists do their specialty. Same for encyclopaedic lookups (wikipedia-specialist) and current events (news-specialist).',
  'Conv 2026-06-14 : le routeur appelait web_search en direct → pas de fichier workspace, pas le grounding du spécialiste. Chaque agent sa spécialité.',
  0, 39, 1, '2026-06-14 00:00:00', '2026-06-14 00:00:00'
);
INSERT OR IGNORE INTO agent_paradigms VALUES(1, 155);  -- bound to jean-michel

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT 1 FROM agent_tools WHERE agent_id=1 AND tool_code='web_search';  -- 0 rows
-- SELECT code FROM paradigms WHERE id=155;                                -- delegate_web_search
-- SELECT agent_id FROM agent_paradigms WHERE paradigm_id=155;            -- 1

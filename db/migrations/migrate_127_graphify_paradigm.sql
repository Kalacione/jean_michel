-- =============================================================================
-- migrate_127_graphify_paradigm.sql
-- =============================================================================
-- Paradigme de routing pour l'outil graphify (graphe de code, serveur MCP optionnel),
-- + mécanisme générique de gating de paradigme par disponibilité d'outil.
--
--   1. Table `paradigm_requires_tool` (latérale, comme `paradigm_modes`) : un paradigme
--      n'est injecté QUE si l'agent possède au runtime un outil dont le nom commence par
--      `tool_prefix`. Absence de ligne => paradigme toujours injecté (défaut inchangé).
--   2. Paradigme `graphify_codebase_navigation`, bindé à jean-michel + code-fetcher,
--      gardé par le préfixe `mcp__graphify__` → invisible tant que graphify n'est pas
--      branché (JEANMICHEL_GRAPHIFY_ENABLED + serveur up).
--
-- Idempotent (CREATE IF NOT EXISTS + INSERT gardés par NOT EXISTS / OR IGNORE).
-- =============================================================================

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS paradigm_requires_tool (
  paradigm_id INTEGER PRIMARY KEY REFERENCES paradigms(id) ON DELETE CASCADE,
  tool_prefix TEXT NOT NULL
);

INSERT INTO paradigms
    (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 144, 29, 'graphify_codebase_navigation', 'Graphify codebase navigation',
    '- This repo has a queryable structure graph exposed by the graphify MCP tools
  (mcp__graphify__*). For STRUCTURAL questions about the codebase — who calls X, what
  breaks if I change X, where does a symbol/feature live, how do modules connect — query
  the graph BEFORE grepping the tree blindly.
- get_node / get_neighbors / shortest_path / god_nodes / graph_stats are deterministic
  reads of a prebuilt graph; query_graph answers a natural-language structural question;
  get_pr_impact finds what a change ripples into.
- The graph reflects the last build — for very recent edits, trust the live code.',
    'Active uniquement quand graphify est branché (requires_tool mcp__graphify__) — évite de
grep à l''aveugle sur une grosse codebase. Outil dev externe, opt-in par projet via
JEANMICHEL_GRAPHIFY_ENABLED.',
    0, 62, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'graphify_codebase_navigation');

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code IN ('jean-michel', 'code-fetcher') AND p.code = 'graphify_codebase_navigation'
  AND NOT EXISTS (
    SELECT 1 FROM agent_paradigms ap WHERE ap.agent_id = a.id AND ap.paradigm_id = p.id
  );

INSERT OR IGNORE INTO paradigm_requires_tool (paradigm_id, tool_prefix)
SELECT id, 'mcp__graphify__' FROM paradigms WHERE code = 'graphify_codebase_navigation';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code FROM paradigms WHERE code='graphify_codebase_navigation';        -- → 1 ligne
-- SELECT tool_prefix FROM paradigm_requires_tool;                              -- → mcp__graphify__
-- SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id
--   JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='graphify_codebase_navigation';

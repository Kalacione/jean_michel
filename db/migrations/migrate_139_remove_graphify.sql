-- =============================================================================
-- migrate_139_remove_graphify.sql
-- =============================================================================
-- Retire l'intégration graphify : câblée mais INERTE (0 appel mcp__graphify__ dans
-- les runs réels), auto-démarrée à chaque jm.sh (RAM pour rien), et contraire à la
-- thèse (contexte POUSSÉ déterministe via la CRP, pas TIRÉ au jugement du LLM).
-- On garde graphify.sh + docs/20260608_graphify pour le ré-instancier plus tard en
-- OPT-IN par-projet (via le Dockerfile du projet, cf. C3).
--
-- Supprime : le paradigme `graphify_codebase_navigation` (id 144), ses bindings
-- agent, sa ligne paradigm_requires_tool/paradigm_modes, et les grants de l'outil
-- `repo_graph_refresh`. La TABLE paradigm_requires_tool reste (mécanisme générique).
-- Idempotent (DELETE).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DELETE FROM paradigm_requires_tool
WHERE paradigm_id IN (SELECT id FROM paradigms WHERE code = 'graphify_codebase_navigation');

DELETE FROM agent_paradigms
WHERE paradigm_id IN (SELECT id FROM paradigms WHERE code = 'graphify_codebase_navigation');

DELETE FROM paradigm_modes
WHERE paradigm_id IN (SELECT id FROM paradigms WHERE code = 'graphify_codebase_navigation');

DELETE FROM agent_tools WHERE tool_code = 'repo_graph_refresh';

DELETE FROM paradigms WHERE code = 'graphify_codebase_navigation';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT COUNT(*) FROM paradigms WHERE code='graphify_codebase_navigation';  -- 0
-- SELECT COUNT(*) FROM agent_tools WHERE tool_code='repo_graph_refresh';     -- 0
-- SELECT COUNT(*) FROM paradigms WHERE active=1;                             -- 125

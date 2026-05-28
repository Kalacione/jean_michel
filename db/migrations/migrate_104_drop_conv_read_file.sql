-- =============================================================================
-- migrate_104_drop_conv_read_file.sql
-- =============================================================================
-- `conv_read_file` est un sous-ensemble strict de `workspace_view` : il ne
-- lit que la racine du conv_folder, alors que `workspace_view` lit ET le
-- workspace/ ET la racine du conv_folder. Sa coexistence avec `workspace_view`
-- a coûté des tours LLM (cas observé dans la conversation
-- 2026-05-28_14-54_cedfc137c997 : 2 deny "Duplicate call" parce que jean-michel
-- tâtonnait entre les deux outils pour lire un fichier du workspace).
--
-- On supprime ses grants en BDD. Le code Python du tool est supprimé en
-- parallèle (src/jeanmichel/tools/conv_read_file.py + retrait du registry
-- dans src/jeanmichel/tools/__init__.py).
--
-- Idempotente : DELETE par tool_code.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DELETE FROM agent_tools WHERE tool_code = 'conv_read_file';

COMMIT;

-- =============================================================================
-- VALIDATION post-migration
-- =============================================================================
-- SELECT COUNT(*) FROM agent_tools WHERE tool_code='conv_read_file';
--   -- attendu : 0

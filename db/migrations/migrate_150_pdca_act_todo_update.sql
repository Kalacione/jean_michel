-- =============================================================================
-- migrate_150_pdca_act_todo_update.sql
-- =============================================================================
-- Bug : les items du TODO ne sont jamais marqués done. Le paradigme pdca (142,
-- code-router) disait dans l'ACT « call todo_write again to mark that step done »
-- alors que le nudge runtime (+ le bon outil) dit todo_update — un petit modèle
-- recevait deux ordres contradictoires et un todo_write whole-list ratait souvent
-- les statuts. On aligne la doctrine sur todo_update (granulaire) pour marquer fini ;
-- todo_write reste pour re-scoper/ajouter/réordonner.
-- Idempotent (REPLACE no-op si la nouvelle formulation est déjà en place).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

UPDATE paradigms SET
  content = REPLACE(
    content,
    'ACT: call todo_write again to mark that step done, set the next one in_progress, and fold in any suggested_todo_updates the worker returned (add, re-scope, reorder, or retry)',
    'ACT: mark the finished step done with todo_update(item_id, ''done'') and set the next one in_progress with todo_update — use todo_write only to re-scope, add, reorder, or retry steps (e.g. to fold in the worker''s suggested_todo_updates)'
  ),
  modified_at = '2026-06-15 00:00:00'
WHERE id = 142
  AND content LIKE '%call todo_write again to mark that step done%';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT 1 FROM paradigms WHERE id=142 AND content LIKE '%mark the finished step done with todo_update%';  -- 1 row
-- SELECT COUNT(*) FROM paradigms WHERE content LIKE '%call todo_write again to mark that step done%';        -- 0

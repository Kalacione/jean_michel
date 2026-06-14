-- =============================================================================
-- migrate_148_router_planning_sobriety.sql
-- =============================================================================
-- Le routeur jean-michel (sur gemma4, faible en orchestration) part en vrille sur des
-- questions SIMPLES : il hallucine un plan à exécuter / à faire valider (convs 15-43,
-- 15-51) au lieu de déléguer + répondre. Cause : le paradigme pdca cadre TOUTE tâche en
-- boucle PLAN-DO-CHECK-ACT avec todo. On le retire de jean-michel (gardé sur code-router
-- où le multi-fichier le justifie). + « free the todo » : plus de plafond rigide 3-7.
-- Idempotent (DELETE no-op si déjà fait ; REPLACE no-op si '3-7' déjà remplacé).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) jean-michel (id 1) n'est plus obsédé par le PDCA-todo
DELETE FROM agent_paradigms WHERE agent_id = 1 AND paradigm_id = 142;

-- 2) free the todo : plus de plafond 3-7 (c'était de la prose, jamais validé)
UPDATE paradigms SET
  content = REPLACE(content, '3-7 scoped steps',
                    'as many scoped steps as the task needs (no minimum, no cap)'),
  modified_at = '2026-06-14 00:00:00'
WHERE id = 142 AND content LIKE '%3-7 scoped steps%';

UPDATE paradigms SET
  content = REPLACE(content, '3-7 disjoint', 'as many disjoint'),
  modified_at = '2026-06-14 00:00:00'
WHERE id = 133 AND content LIKE '%3-7 disjoint%';

UPDATE paradigms SET
  content = REPLACE(content, '3-7 DISJOINT', 'as many DISJOINT'),
  modified_at = '2026-06-14 00:00:00'
WHERE id = 132 AND content LIKE '%3-7 DISJOINT%';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT 1 FROM agent_paradigms WHERE agent_id=1 AND paradigm_id=142;          -- 0 rows
-- SELECT 1 FROM agent_paradigms WHERE paradigm_id=142;                         -- code-router stays
-- SELECT COUNT(*) FROM paradigms WHERE content LIKE '%3-7%';                   -- 0

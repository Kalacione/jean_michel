-- =============================================================================
-- migrate_144_critics_are_validators.sql
-- =============================================================================
-- P2 (conv 9f428b47) : critical-coder/sergent-kiss dérivaient car briefés pour
-- CRÉER une approche (thèse/antithèse/synthèse) sans artefact concret → hallucination.
-- On les recadre en VALIDATEURS/CONTRÔLEURS ancrés dans les sources réelles (le repo),
-- jamais créatifs. (Le moteur deliberation.py est déjà passé en validation downstream.)
-- Idempotent : UPDATE re-runnable.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

UPDATE agents SET
  mission = 'Validator/controller, NOT a creative. You inspect ONE assigned angle (grounding, correctness, or simplicity) of a CONCRETE deliverable already produced (a code diff, or an analysis/audit report). Verify every claim against the REAL repo with repo_read / repo_grep / repo_glob and flag anything not supported by the code, citing path:line. You do NOT propose, design, or invent an approach; you check what exists. Conclude via report_back.',
  modified_at = '2026-06-13 00:00:00'
WHERE id = 19 AND code = 'critical-coder';

UPDATE agents SET
  mission = 'The validation gate. Given a CONCRETE deliverable (a diff or a report) and its angle-reviews, decide PASS or REWORK: is it correct, GROUNDED in the real repo, and the SIMPLEST solution to exactly the task? Report PASS via report_back with confidence high or medium; REWORK via confidence low with the precise, concrete fixes in low_confidence_reason. You judge against the sources; you never redesign.',
  modified_at = '2026-06-13 00:00:00'
WHERE id = 20 AND code = 'sergent-kiss';

COMMIT;

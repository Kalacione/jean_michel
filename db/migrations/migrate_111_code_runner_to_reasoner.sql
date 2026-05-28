-- =============================================================================
-- migrate_111_code_runner_to_reasoner.sql
-- =============================================================================
-- Promote `code-runner` to the reasoner tier (gemma4:26b via model_override).
--
-- Justification : code-runner's job is WRITING code that compiles and runs —
-- that's reasoning-intensive, not lookup. The default subagent model
-- (gemma4:latest, 9b) was producing scripts the user described as "too dumb
-- for the task". The 26b variant is required for serious code production.
--
-- Reasoners list now : strategist + critical-thinker + comparator-specialist
-- + meta-analyst + code-runner. Each has `model_override='gemma4:26b'`.
--
-- Idempotent : conditional UPDATE.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

UPDATE agents
SET model_override = 'gemma4:26b', modified_at = datetime('now')
WHERE code = 'code-runner'
  AND (model_override IS NULL OR model_override <> 'gemma4:26b');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code, model_override FROM agents WHERE code='code-runner';
--   -- expected : code-runner | gemma4:26b

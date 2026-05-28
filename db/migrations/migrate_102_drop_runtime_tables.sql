-- =============================================================================
-- migrate_102_drop_runtime_tables.sql
-- =============================================================================
-- Phase 6 of the v2 migration (DevNotes/REVOLUCION/07_plan_implementation.md) :
--
--   1. Drop the legacy runtime tables the v2 orchestrator no longer needs.
--      In v2, this state lives in the filesystem :
--        - request tree           → conversations/<id>/messages.json
--                                 + conversations/<id>/subagent_*.json
--        - artifacts index        → conversations/<id>/* (the filesystem IS the index)
--        - conversation phases    → concept removed (no more phase machine)
--        - sandbox_executions     → ~/.jean-michel/sandbox_audit.jsonl (global JSONL)
--
--   2. Add the `model_override` column to `agents` so per-agent model selection
--      becomes possible (cf. §1.3 doc 06 ; §11 ter doc 06). NULL means : use
--      the configured slot (MAIN_MODEL / SUBAGENT_DEFAULT_MODEL).
--
--   3. Hard-delete the `archivist` agent. Migration 100 marked it inactive ;
--      this migration removes it definitively. Its role (running summary) is
--      replaced by the native `messages.json` history.
--
-- WARNING — destructive, *one-shot* :
--   * The `DROP TABLE IF EXISTS` statements are idempotent.
--   * The `ALTER TABLE agents ADD COLUMN` is NOT — SQLite has no
--     "ADD COLUMN IF NOT EXISTS". Re-applying this migration on a database
--     that already has `model_override` will error with
--     "duplicate column name". The legacy migration runner (which applies
--     a file at most once per DB) prevents this in practice.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- 1. Drop legacy runtime tables (FK-dependent ones first)
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS sandbox_executions;
DROP TABLE IF EXISTS artifacts;
DROP TABLE IF EXISTS conversation_phases;
DROP TABLE IF EXISTS requests;

-- ---------------------------------------------------------------------------
-- 2. Add per-agent model override column
-- ---------------------------------------------------------------------------

ALTER TABLE agents ADD COLUMN model_override TEXT NULL;

-- ---------------------------------------------------------------------------
-- 3. Definitive archivist removal
-- ---------------------------------------------------------------------------
-- The archivist agent was deactivated (active=0) by migrate_100. With the
-- v2 main loop persisting `messages.json` natively, it serves no purpose.
-- We delete it now ; its paradigm bindings and tool grants cascade via the
-- FK ON DELETE CASCADE declarations in schema.sql, but we DELETE explicitly
-- for audit clarity.

DELETE FROM agent_paradigms WHERE agent_id IN (
    SELECT id FROM agents WHERE code = 'archivist'
);
DELETE FROM agent_tools WHERE agent_id IN (
    SELECT id FROM agents WHERE code = 'archivist'
);
DELETE FROM agent_workspace_grants WHERE agent_id IN (
    SELECT id FROM agents WHERE code = 'archivist'
);
DELETE FROM agent_sandbox_grants WHERE agent_id IN (
    SELECT id FROM agents WHERE code = 'archivist'
);
DELETE FROM agent_delegation_targets WHERE agent_id IN (
    SELECT id FROM agents WHERE code = 'archivist'
);
DELETE FROM agents WHERE code = 'archivist';

-- ---------------------------------------------------------------------------
-- 4. Clean up dead `agent_tools` grants
-- ---------------------------------------------------------------------------
-- Migration 100 deleted the matching paradigms but the `agent_tools` rows
-- pointing at tools we're removing from the codebase were left in place.
-- They are silently ignored by `_build_tools_payload` (registry.get returns
-- None), but cleaning them up keeps the DB readable.

DELETE FROM agent_tools WHERE tool_code IN (
    'set_task_class',
    'manage_todo_list',
    'signal_convergence',
    'report_findings'  -- replaced by the report_back control verb in v2
);

COMMIT;


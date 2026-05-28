-- =============================================================================
-- migrate_101_user_memory.sql
-- =============================================================================
-- Creates the `user_memory` table for the v2 long-term cross-conversation
-- memory feature (cf. DevNotes/REVOLUCION/06_proposition_v2.md §10).
--
-- A single tool `manage_user_memory(action, ...)` operates on this table.
-- The orchestrator's prompt renderer prepends the index (type + code +
-- description) of all entries to the `## Human` block of every system prompt,
-- so the LLM sees what it remembers without loading every content body.
--
-- Idempotent : CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS user_memory (
    id           INTEGER PRIMARY KEY,
    type         TEXT NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    code         TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,  -- one-line hook, injected into the prompt index
    content      TEXT NOT NULL,  -- full markdown body, loaded on demand via recall
    created_at   TEXT NOT NULL,
    modified_at  TEXT NOT NULL,
    UNIQUE (type, code)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_type ON user_memory(type);
CREATE INDEX IF NOT EXISTS idx_user_memory_modified ON user_memory(modified_at DESC);

COMMIT;

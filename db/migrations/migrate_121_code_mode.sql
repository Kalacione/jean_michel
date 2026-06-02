-- =============================================================================
-- migrate_121_code_mode.sql
-- =============================================================================
-- Introduce the `code` interaction mode + fix the worker thinking mismatch found
-- in the first E2E test (cf. DevNotes/ORCHESTRATOR/03).
--
--   * `mode` CHECK constraints (conversations + paradigm_modes) only allowed
--     'analyse'/'chat'/'vocal' → extend both to include 'code' (SQLite can't ALTER
--     a CHECK, so we rebuild each table, preserving data).
--   * jean-michel: revert model_override to NULL → defaults to MAIN_MODEL (gemma4)
--     for chat/analyse/vocal (vision-capable, light). The `code` mode selects a
--     stronger router model (config.CODE_MODEL=qwen3:14b) at the turn_runner level.
--   * code-runner: thinking_mode = 0 — qwen3-coder:latest does NOT support Ollama
--     `think` (returns HTTP 400). Without this, every code delegation aborts and
--     the router falls back to writing code inline (the monolithic failure observed).
--   * scope the PDCA paradigm to the `code` mode so it loads ONLY there (no prompt
--     bloat / over-decomposition in chat/analyse/vocal).
--
-- Idempotent (rebuilds are repeatable ; conditional UPDATEs ; INSERT OR IGNORE).
-- =============================================================================

PRAGMA foreign_keys=OFF;

-- ---- Rebuild conversations (extend mode CHECK with 'code', preserve rows) ---
DROP TABLE IF EXISTS conversations_new;
CREATE TABLE conversations_new (
  id             TEXT PRIMARY KEY,
  title          TEXT,
  folder_path    TEXT NOT NULL,
  user_language  TEXT,
  status         TEXT NOT NULL DEFAULT 'active',
  mode           TEXT NOT NULL DEFAULT 'analyse'
                 CHECK (mode IN ('analyse','chat','vocal','code')),
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL,
  task_class     TEXT,
  current_phase  TEXT
);
INSERT INTO conversations_new
  (id, title, folder_path, user_language, status, mode, created_at, modified_at, task_class, current_phase)
  SELECT id, title, folder_path, user_language, status, mode, created_at, modified_at, task_class, current_phase
  FROM conversations;
DROP TABLE conversations;
ALTER TABLE conversations_new RENAME TO conversations;

-- ---- Rebuild paradigm_modes (extend mode CHECK with 'code') ----------------
DROP TABLE IF EXISTS paradigm_modes_new;
CREATE TABLE paradigm_modes_new (
  paradigm_id INTEGER NOT NULL REFERENCES paradigms(id) ON DELETE CASCADE,
  mode        TEXT    NOT NULL CHECK (mode IN ('analyse','chat','vocal','code')),
  PRIMARY KEY (paradigm_id, mode)
);
INSERT INTO paradigm_modes_new (paradigm_id, mode)
  SELECT paradigm_id, mode FROM paradigm_modes;
DROP TABLE paradigm_modes;
ALTER TABLE paradigm_modes_new RENAME TO paradigm_modes;

PRAGMA foreign_keys=ON;

-- ---- Models ----------------------------------------------------------------
UPDATE agents SET model_override = NULL, modified_at = datetime('now')
WHERE code = 'jean-michel' AND model_override IS NOT NULL;

UPDATE agents SET thinking_mode = 0, modified_at = datetime('now')
WHERE code = 'code-runner' AND thinking_mode <> 0;

-- ---- Scope the PDCA paradigm to the `code` mode ----------------------------
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'code' FROM paradigms WHERE code = 'pdca_decompose_delegate_revise';

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT mode FROM conversations LIMIT 0;  -- CHECK now allows 'code'
-- SELECT code, model_override, thinking_mode FROM agents WHERE code IN ('jean-michel','code-runner');
--   -- jean-michel | (null) | 1   /   code-runner | qwen3-coder:latest | 0
-- SELECT mode FROM paradigm_modes WHERE paradigm_id=(SELECT id FROM paradigms WHERE code='pdca_decompose_delegate_revise'); -- code

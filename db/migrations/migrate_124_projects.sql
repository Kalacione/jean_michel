-- =============================================================================
-- migrate_124_projects.sql
-- =============================================================================
-- Introduce first-class "projects" : a long-term container a user owns and that
-- groups conversations (1 project → N conversations ; a conversation has 0 or 1
-- project). Memory of scope='project' (migrate_125) keys off projects.id.
--
--   1. Create the `projects` table (owned by a web_user, unique code per user).
--   2. Add a nullable `project_id` FK to `conversations` (ON DELETE SET NULL :
--      deleting a project orphans its conversations, never deletes them).
--
-- Idempotent-ish : guarded by IF NOT EXISTS where SQLite allows it. The ADD
-- COLUMN is a one-shot (re-running raises "duplicate column").
--
-- ADD COLUMN with a REFERENCES clause is legal because the implicit default is
-- NULL (SQLite requirement), so no table rebuild is needed for conversations.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
  code        TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at  TEXT NOT NULL,
  modified_at TEXT NOT NULL,
  UNIQUE (user_id, code)
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);

ALTER TABLE conversations
  ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);

COMMIT;

PRAGMA foreign_keys = ON;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT name FROM sqlite_master WHERE type='table' AND name='projects';        -- → projects
-- SELECT name FROM pragma_table_info('conversations') WHERE name='project_id';  -- → project_id

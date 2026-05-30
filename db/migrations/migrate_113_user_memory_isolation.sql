-- =============================================================================
-- migrate_113_user_memory_isolation.sql
-- =============================================================================
-- Isolate user_memory per user (audit: DevNotes/WEBUI/02_audit_user_memory_isolation.md).
--
--   1. Add the profile columns (the cli_profile.toml structure) to web_users.
--   2. Insert a reserved `cli` user (unusable password, never logs in on the web)
--      — the CLI runs as this user.
--   3. Rebuild user_memory with a `user_id` FK + UNIQUE(user_id, type, code).
--      SQLite can't ALTER a UNIQUE constraint, hence the table rebuild. Existing
--      rows are Jeremy's CLI-era memory → assigned to the cli user.
--
-- ⚠ ONE-SHOT migration (the rebuild is not re-applicable like an IF NOT EXISTS).
--   Back up first (./jm.sh --export-db).
--
-- The foreign_keys pragma is a no-op INSIDE a transaction, so it's toggled
-- around the BEGIN/COMMIT (the rebuild drops + renames a table).
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- 1. Structured profile on web_users (TOML fields, reprise en BDD).
ALTER TABLE web_users ADD COLUMN name      TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN birthdate TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN city      TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN country   TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN language  TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN interests TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN notes     TEXT NOT NULL DEFAULT '';

-- 2. Reserved cli user. NO forced id (web accounts may already exist).
INSERT INTO web_users (username, password_hash, created_at)
VALUES ('cli', '!', datetime('now'));

-- 3. Rebuild user_memory scoped per user.
CREATE TABLE user_memory_new (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES web_users(id),
    type         TEXT NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    code         TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    modified_at  TEXT NOT NULL,
    UNIQUE (user_id, type, code)
);

INSERT INTO user_memory_new
    (id, user_id, type, code, title, description, content, created_at, modified_at)
SELECT id, (SELECT id FROM web_users WHERE username = 'cli'),
       type, code, title, description, content, created_at, modified_at
FROM user_memory;

DROP TABLE user_memory;
ALTER TABLE user_memory_new RENAME TO user_memory;

CREATE INDEX idx_user_memory_user ON user_memory(user_id);
CREATE INDEX idx_user_memory_type ON user_memory(type);
CREATE INDEX idx_user_memory_modified ON user_memory(modified_at DESC);

COMMIT;

PRAGMA foreign_keys = ON;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT name FROM pragma_table_info('user_memory') WHERE name='user_id';   -- → user_id
-- SELECT username FROM web_users WHERE username='cli';                       -- → cli
-- SELECT COUNT(*) FROM pragma_table_info('web_users');                       -- → 11

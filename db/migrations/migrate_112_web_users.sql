-- =============================================================================
-- migrate_112_web_users.sql
-- =============================================================================
-- Multi-user support for the web frontend (audit: DevNotes/WEBUI/01).
--
-- Two ADDITIVE tables — nothing existing is touched (constraint: "ne pas
-- éclater la BDD") :
--   web_users           : a login (username + argon2 password hash).
--   conversation_users  : association user <-> conversation. A conversation is
--                         visible in the web frontend only if it has a row
--                         here. The CLI does NOT associate, so CLI-created
--                         conversations stay invisible to the web — by design.
--
-- user_memory remains GLOBAL in v1 (shared across web users) — documented
-- limitation, see the audit's risks section.
--
-- Idempotent : CREATE ... IF NOT EXISTS.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS web_users (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_users (
  user_id         INTEGER NOT NULL REFERENCES web_users(id),
  conversation_id TEXT    NOT NULL REFERENCES conversations(id),
  created_at      TEXT    NOT NULL,
  PRIMARY KEY (user_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_users_user ON conversation_users(user_id);

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT name FROM sqlite_master WHERE type='table'
--   AND name IN ('web_users','conversation_users');
--   -- expected : web_users, conversation_users

-- =============================================================================
-- migrate_114_conversation_cascade.sql
-- =============================================================================
-- Make conversation deletion robust via SQL cascade. Rebuild conversation_users
-- with ON DELETE CASCADE on BOTH foreign keys :
--
--   - REFERENCES conversations(id) ON DELETE CASCADE
--       → deleting a conversation auto-removes its ownership links (and any
--         FUTURE child table declared the same way cleans itself up — no code
--         change needed when the schema grows).
--   - REFERENCES web_users(id)    ON DELETE CASCADE
--       → deleting a web user auto-removes their links (conversations stay).
--
-- SQLite can't ALTER a constraint, so this is a table REBUILD.
--
-- ⚠ ONE-SHOT migration (not re-applicable like an IF NOT EXISTS). Back up first.
--   The foreign_keys pragma is a no-op INSIDE a transaction, so it's toggled
--   around the BEGIN/COMMIT (the rebuild drops + renames a table).
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

CREATE TABLE conversation_users_new (
  user_id         INTEGER NOT NULL REFERENCES web_users(id)     ON DELETE CASCADE,
  conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  created_at      TEXT    NOT NULL,
  PRIMARY KEY (user_id, conversation_id)
);

INSERT INTO conversation_users_new (user_id, conversation_id, created_at)
SELECT user_id, conversation_id, created_at FROM conversation_users;

DROP TABLE conversation_users;
ALTER TABLE conversation_users_new RENAME TO conversation_users;

CREATE INDEX idx_conv_users_user ON conversation_users(user_id);

COMMIT;

PRAGMA foreign_keys = ON;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT "table", "on_delete" FROM pragma_foreign_key_list('conversation_users');
--   → conversations|CASCADE  and  web_users|CASCADE

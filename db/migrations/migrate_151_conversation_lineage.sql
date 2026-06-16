-- =============================================================================
-- migrate_151_conversation_lineage.sql
-- =============================================================================
-- Fork lineage (Phase 2, Track A) : when a conversation is forked from another at
-- a given per-conversation git commit, record where it came from so the UI can
-- show "forké de X @ commit".
--
--   parent_conv_id : the source conversation's id (the conv this one was forked
--                    from). Nullable. No FK : the parent may later be deleted ;
--                    we keep the id as a historical pointer (the UI degrades to a
--                    short id when the parent is gone).
--   parent_commit  : the source git commit (snapshot) the fork was taken at.
--
-- Both NULL for conversations created normally (not forked) and for forks made
-- before this migration.
--
-- The ADD COLUMN is a one-shot (re-running raises "duplicate column"). Implicit
-- default NULL → no table rebuild.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

ALTER TABLE conversations ADD COLUMN parent_conv_id TEXT;
ALTER TABLE conversations ADD COLUMN parent_commit  TEXT;

COMMIT;

PRAGMA foreign_keys = ON;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT name FROM pragma_table_info('conversations') WHERE name='parent_conv_id';  -- → parent_conv_id
-- SELECT name FROM pragma_table_info('conversations') WHERE name='parent_commit';   -- → parent_commit

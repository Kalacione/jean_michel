-- =============================================================================
-- migrate_125_memory_scopes.sql
-- =============================================================================
-- Generalise `user_memory` → `memory` : a single *scope* dimension replaces the
-- flat `type` enum. Scope drives DETERMINISTIC prompt inclusion (no LLM in the
-- inclusion path) :
--
--   world   → injected everywhere (global, shared)
--   user    → injected for the conversation's user            (key: user_id)
--   project → injected for the conversation's project         (key: project_id)
--   tool    → injected for any agent granted that tool        (key: tool_code)
--
-- Plus SQLite FTS5 (BM25 ranking) over title/description/content for deterministic
-- full-text recall and save-time dedup/contradiction surfacing.
--
-- Migration of existing rows : everything was per-user → scope='user', user_id
-- preserved. The old `type` is folded into the code to keep (user_id, code)
-- unique : non-'user' types get a `<type>-` prefix.
--
-- ⚠ ONE-SHOT (table rebuild, like migrate_113). Depends on migrate_124 (projects).
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- 1. New scope-aware table. A CHECK enforces exactly one target key per scope,
--    so the data model is self-consistent at the DB level (deterministic).
CREATE TABLE memory (
  id          INTEGER PRIMARY KEY,
  scope       TEXT NOT NULL CHECK (scope IN ('world', 'user', 'project', 'tool')),
  user_id     INTEGER REFERENCES web_users(id) ON DELETE CASCADE,
  project_id  INTEGER REFERENCES projects(id)  ON DELETE CASCADE,
  tool_code   TEXT,
  code        TEXT NOT NULL,
  title       TEXT NOT NULL,
  description TEXT NOT NULL,  -- one-line hook, injected into the prompt index
  content     TEXT NOT NULL,  -- full markdown body, loaded on demand via recall
  created_at  TEXT NOT NULL,
  modified_at TEXT NOT NULL,
  CHECK (
    (scope = 'world'   AND user_id IS NULL     AND project_id IS NULL     AND tool_code IS NULL) OR
    (scope = 'user'    AND user_id IS NOT NULL AND project_id IS NULL     AND tool_code IS NULL) OR
    (scope = 'project' AND user_id IS NULL     AND project_id IS NOT NULL AND tool_code IS NULL) OR
    (scope = 'tool'    AND user_id IS NULL     AND project_id IS NULL     AND tool_code IS NOT NULL)
  )
);

-- 2. Migrate existing user_memory rows (all per-user → scope='user').
INSERT INTO memory
    (id, scope, user_id, project_id, tool_code, code, title, description, content, created_at, modified_at)
SELECT id, 'user', user_id, NULL, NULL,
       CASE WHEN type = 'user' THEN code ELSE type || '-' || code END,
       title, description, content, created_at, modified_at
FROM user_memory;

DROP TABLE user_memory;

-- 3. Uniqueness per scope target (partial indexes : NULL-safe, deterministic).
CREATE UNIQUE INDEX ux_memory_world   ON memory(code)             WHERE scope = 'world';
CREATE UNIQUE INDEX ux_memory_user    ON memory(user_id, code)    WHERE scope = 'user';
CREATE UNIQUE INDEX ux_memory_project ON memory(project_id, code) WHERE scope = 'project';
CREATE UNIQUE INDEX ux_memory_tool    ON memory(tool_code, code)  WHERE scope = 'tool';

CREATE INDEX idx_memory_scope    ON memory(scope);
CREATE INDEX idx_memory_user     ON memory(user_id);
CREATE INDEX idx_memory_project  ON memory(project_id);
CREATE INDEX idx_memory_tool     ON memory(tool_code);
CREATE INDEX idx_memory_modified ON memory(modified_at DESC);

-- 4. FTS5 full-text index (external content : title/description/content).
CREATE VIRTUAL TABLE memory_fts USING fts5(
  title, description, content,
  content = 'memory', content_rowid = 'id'
);

CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
  INSERT INTO memory_fts(rowid, title, description, content)
  VALUES (new.id, new.title, new.description, new.content);
END;

CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, title, description, content)
  VALUES ('delete', old.id, old.title, old.description, old.content);
END;

CREATE TRIGGER memory_au AFTER UPDATE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, title, description, content)
  VALUES ('delete', old.id, old.title, old.description, old.content);
  INSERT INTO memory_fts(rowid, title, description, content)
  VALUES (new.id, new.title, new.description, new.content);
END;

-- 5. Backfill the FTS index from the migrated rows.
INSERT INTO memory_fts(memory_fts) VALUES ('rebuild');

COMMIT;

PRAGMA foreign_keys = ON;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT name FROM sqlite_master WHERE type='table' AND name='memory';      -- → memory
-- SELECT DISTINCT scope FROM memory;                                         -- → user (post-migration)
-- SELECT count(*) FROM memory_fts;                                           -- → same as count(memory)

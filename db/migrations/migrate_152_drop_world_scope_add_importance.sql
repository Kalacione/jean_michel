-- =============================================================================
-- migrate_152_drop_world_scope_add_importance.sql
-- =============================================================================
-- Refonte mémoire (Phase 0). On RETIRE le scope `world` : le savoir global /
-- comportemental relève désormais des paradigmes (promotion à venir), pas d'une
-- mémoire injectée partout sans propriétaire ni revue. Les éventuelles lignes
-- `world` existantes sont PRÉSERVÉES (migrées vers user/cli, code dé-collisionné
-- avec un suffixe -w<id>) — rien n'est perdu ; review/suppression via l'UI mémoire.
--
-- + colonne `importance` (1..5) : signal de pertinence noté à la consolidation,
--   utilisé pour classer l'injection (importance DESC, puis récence).
--
-- SQLite ne sait pas restreindre un CHECK en place → rebuild de `memory`
-- (recette canonique : table_new + copie + swap + index + triggers FTS + rebuild).
-- ONE-SHOT (comme migrate_102 ADD COLUMN) : à n'appliquer qu'une fois.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN;

-- 1. Dé-collision : un code de mémoire `world` qui entre en conflit avec un code
--    user(cli) existant est suffixé AVANT la bascule (l'unique user = (user_id, code)).
UPDATE memory
   SET code = code || '-w' || id
 WHERE scope = 'world'
   AND code IN (
       SELECT code FROM memory
        WHERE scope = 'user'
          AND user_id = (SELECT id FROM web_users WHERE username = 'cli')
   );

-- 2. Triggers FTS retirés le temps du rebuild (recréés en 6).
DROP TRIGGER IF EXISTS memory_ai;
DROP TRIGGER IF EXISTS memory_ad;
DROP TRIGGER IF EXISTS memory_au;

-- 3. Table cible : CHECK à 3 scopes + colonne importance.
CREATE TABLE memory_new (
  id          INTEGER PRIMARY KEY,
  scope       TEXT NOT NULL CHECK (scope IN ('user', 'project', 'tool')),
  user_id     INTEGER REFERENCES web_users(id) ON DELETE CASCADE,
  project_id  INTEGER REFERENCES projects(id)  ON DELETE CASCADE,
  tool_code   TEXT,
  code        TEXT NOT NULL,
  title       TEXT NOT NULL,
  description TEXT NOT NULL,
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  modified_at TEXT NOT NULL,
  importance  INTEGER NOT NULL DEFAULT 3,
  CHECK (
    (scope = 'user'    AND user_id IS NOT NULL AND project_id IS NULL     AND tool_code IS NULL) OR
    (scope = 'project' AND user_id IS NULL     AND project_id IS NOT NULL AND tool_code IS NULL) OR
    (scope = 'tool'    AND user_id IS NULL     AND project_id IS NULL     AND tool_code IS NOT NULL)
  )
);

-- 4. Copie : world → user(cli), importance défaut 3.
INSERT INTO memory_new
  (id, scope, user_id, project_id, tool_code, code, title, description, content, created_at, modified_at, importance)
SELECT
  id,
  CASE WHEN scope = 'world' THEN 'user' ELSE scope END,
  CASE WHEN scope = 'world'
       THEN (SELECT id FROM web_users WHERE username = 'cli')
       ELSE user_id END,
  project_id, tool_code, code, title, description, content, created_at, modified_at, 3
FROM memory;

DROP TABLE memory;
ALTER TABLE memory_new RENAME TO memory;

-- 5. Index (sans ux_memory_world).
CREATE UNIQUE INDEX ux_memory_user    ON memory(user_id, code)    WHERE scope = 'user';
CREATE UNIQUE INDEX ux_memory_project ON memory(project_id, code) WHERE scope = 'project';
CREATE UNIQUE INDEX ux_memory_tool    ON memory(tool_code, code)  WHERE scope = 'tool';
CREATE INDEX idx_memory_scope    ON memory(scope);
CREATE INDEX idx_memory_user     ON memory(user_id);
CREATE INDEX idx_memory_project  ON memory(project_id);
CREATE INDEX idx_memory_tool     ON memory(tool_code);
CREATE INDEX idx_memory_modified ON memory(modified_at DESC);

-- 6. Triggers FTS recréés (mêmes définitions que migrate_125 / schema.sql).
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

-- 7. Resync l'index FTS (external content) depuis la table reconstruite.
INSERT INTO memory_fts(memory_fts) VALUES('rebuild');

COMMIT;

PRAGMA foreign_keys = ON;

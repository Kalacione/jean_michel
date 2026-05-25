-- Migration 053: extend artifacts.kind CHECK constraint to allow
-- 'duplicate_blocked' kind. Used by the orchestrator when persisting
-- the synthetic artifact for blocked duplicate tool calls (Chantier 6).
--
-- SQLite cannot ALTER a CHECK constraint in place — recreate the table.

PRAGMA foreign_keys = OFF;

CREATE TABLE artifacts_new (
  id             INTEGER PRIMARY KEY,
  request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
  relative_path  TEXT NOT NULL,
  kind           TEXT NOT NULL
                 CHECK (kind IN ('prompt','thought','briefing','tool_call',
                                 'tool_response','ask_human','human_answer',
                                 'response','summary','report',
                                 'duplicate_blocked')),
  created_at     TEXT NOT NULL
);

INSERT INTO artifacts_new (id, request_id, relative_path, kind, created_at)
SELECT id, request_id, relative_path, kind, created_at FROM artifacts;

DROP TABLE artifacts;
ALTER TABLE artifacts_new RENAME TO artifacts;

CREATE INDEX idx_artifacts_request ON artifacts(request_id);

PRAGMA foreign_keys = ON;

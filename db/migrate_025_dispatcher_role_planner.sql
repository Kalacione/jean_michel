-- MIGRATION 025 — dispatcher role: specialist → planner
-- Extends the role CHECK constraint to include 'planner'.
-- A planner receives [ask_human, return_to_user] only — no delegate_to, no Delegation targets.
-- Prevents the dispatcher from executing research steps instead of just planning them.

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

CREATE TABLE agents_new (
  id             INTEGER PRIMARY KEY,
  code           TEXT UNIQUE NOT NULL,
  name           TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('router','specialist','finalizer','planner')),
  mission        TEXT NOT NULL,
  thinking_mode  INTEGER NOT NULL DEFAULT 1,
  temperature    REAL NOT NULL DEFAULT 0.2,
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL,
  sandbox_image  TEXT
);

INSERT INTO agents_new SELECT * FROM agents;

DROP TABLE agents;
ALTER TABLE agents_new RENAME TO agents;

UPDATE agents
SET role = 'planner', modified_at = datetime('now')
WHERE code = 'dispatcher';

COMMIT;

PRAGMA foreign_keys = ON;

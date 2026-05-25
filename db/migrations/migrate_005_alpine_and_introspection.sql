-- =============================================================
-- Migration 005 — Alpine sandbox + meta-analyst introspection
-- Apply to existing jeanmichel.db instances:
--   sqlite3 jeanmichel.db < db/migrate_005_alpine_and_introspection.sql
-- Idempotent: safe to run multiple times.
-- =============================================================

PRAGMA foreign_keys = ON;

-- 1. Add sandbox_image column to agents (idempotent via ALTER TABLE).
--    SQLite does not support IF NOT EXISTS for ADD COLUMN; wrap in a
--    SAVEPOINT to suppress the error if the column already exists.
SAVEPOINT add_sandbox_image;
ALTER TABLE agents ADD COLUMN sandbox_image TEXT;
RELEASE SAVEPOINT add_sandbox_image;

-- 2. Meta-analysis category
INSERT OR IGNORE INTO categories (id, section_id, code, title, order_priority, active, created_at, modified_at) VALUES
  (32, 3, 'meta_analysis', 'Meta-analysis', 90, 1, datetime('now'), datetime('now'));

-- 3. Meta-analysis paradigms
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES
(94, 32, 'inspect_before_proposing', 'Inspect before proposing',
 '- Always call self_inspect before making any statement about the system configuration.
- Never rely on your training data or prior context to describe the current agent roster, tool grants, or paradigm assignments.
- Observe, then reason.',
 'Prevents hallucinating system state.',
 0, 10, 1, datetime('now'), datetime('now')),

(95, 32, 'improvement_proposals_format', 'Structured improvement proposals',
 '- Structure proposals as:
  1. Observation — what you observed from self_inspect data.
  2. Problem statement — what is sub-optimal and why.
  3. Proposed change — concrete SQL INSERTs/UPDATEs or Python changes.
  4. Risk assessment — what could break, what to test.
- Never propose a change you cannot justify with data from self_inspect.
- Proposals are written to workspace files, not returned inline.',
 'Enforces traceable, data-driven proposals.',
 0, 20, 1, datetime('now'), datetime('now')),

(96, 32, 'no_self_modification', 'No self-modification',
 '- You produce proposals — you do not execute them.
- Never call workspace_create_file to write Python source files that would alter system behavior.
- Write SQL proposals, human-readable analysis documents, and checklists only.',
 'Hard safety boundary: the meta-analyst observes and proposes, the human decides and applies.',
 0, 30, 1, datetime('now'), datetime('now'));

-- 4. meta-analyst agent
INSERT OR IGNORE INTO agents (id, code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
(11, 'meta-analyst', 'Meta-Analyst', 'specialist',
 'Analyze Jean-Michel''s own configuration, activity patterns, and conversation history to identify sub-optimal setups, missing tool grants, underused agents, and improvement opportunities. Produce structured proposals as workspace documents. Observe via self_inspect and workspace tools — never assume system state from memory.',
 1, 0.3, 1, datetime('now'), datetime('now'));

-- 5. meta-analyst paradigm bindings
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES
  (11,  4),
  (11,  8),
  (11, 36),
  (11, 39),
  (11, 41),
  (11, 49),
  (11, 68),
  (11, 73),
  (11, 77),
  (11, 80),
  (11, 87),
  (11, 88),
  (11, 89),
  (11, 90),
  (11, 94),
  (11, 95),
  (11, 96);

-- 6. meta-analyst tool grants
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (11, 'self_inspect'),
  (11, 'conv_read_file'),
  (11, 'workspace_create_file'),
  (11, 'workspace_str_replace'),
  (11, 'workspace_view'),
  (11, 'workspace_list');

-- 7. meta-analyst workspace write grant
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (11);

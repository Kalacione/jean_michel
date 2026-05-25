-- =============================================================
-- Migration 007 — verify_execution_output paradigm for code-runner
-- Apply to existing jeanmichel.db instances:
--   sqlite3 jeanmichel.db < db/migrate_007_verify_execution.sql
-- Idempotent: safe to run multiple times.
--
-- Root cause: code-runner reported success based solely on the
-- script's own stdout message ("Numbers 1 through 100 written..."),
-- without calling workspace_view to confirm the output file actually
-- exists and contains correct content.
-- =============================================================

PRAGMA foreign_keys = ON;

-- 1. New paradigm: verify output after execution (category workspace_management = 31)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES
(99, 31, 'verify_execution_output', 'Verify execution output',
 '- After bash_sandbox execution, do not rely on the script''s own stdout to confirm success.
- A zero exit_code is necessary but not sufficient — call workspace_view on the expected output file to confirm its existence and content.
- A non-zero exit_code is always a failure: diagnose from stderr before concluding.
- Only report task complete after observing the actual output via a workspace tool.
- If the expected output file is missing or has unexpected content, treat the task as incomplete and investigate.',
 'Prevents false-success reports where the script claimed to succeed but the output file was not actually created or has wrong content.',
 0, 15, 1, datetime('now'), datetime('now'));

-- 2. Bind to code-runner (id=12)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (12, 99);

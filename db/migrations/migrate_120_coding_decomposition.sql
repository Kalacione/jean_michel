-- =============================================================================
-- migrate_120_coding_decomposition.sql
-- =============================================================================
-- Wire the PDCA coding-orchestrator (cf. DevNotes/ORCHESTRATOR/01) :
--   * jean-michel (router) → qwen3:14b : robust enough to decompose and revise
--     a plan over a whole codebase. Vision turns keep gemma4 (guarded in
--     turn_runner: in-context images force the vision model).
--   * grant `todo_write` to jean-michel — it OWNS the living TODO (todo.json).
--   * grant `manage_user_memory` to code-runner — coding workers read/write the
--     shared memory.
--   * code-runner (worker) → qwen3-coder:latest : the coding model.
--   * paradigm `pdca_decompose_delegate_revise` bound to jean-michel : the
--     PLAN-DO-CHECK-ACT loop + crafted briefings + folding worker
--     `suggested_todo_updates` back into the plan.
--
-- Idempotent : conditional UPDATEs, INSERT OR IGNORE on PK tables, NOT EXISTS
-- guards on the paradigm/binding.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ---- Models ---------------------------------------------------------------
UPDATE agents
SET model_override = 'qwen3:14b', modified_at = datetime('now')
WHERE code = 'jean-michel'
  AND (model_override IS NULL OR model_override <> 'qwen3:14b');

UPDATE agents
SET model_override = 'qwen3-coder:latest', modified_at = datetime('now')
WHERE code = 'code-runner'
  AND (model_override IS NULL OR model_override <> 'qwen3-coder:latest');

-- ---- Tool grants ----------------------------------------------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'todo_write' FROM agents WHERE code = 'jean-michel';

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'manage_user_memory' FROM agents WHERE code = 'code-runner';

-- ---- Paradigm : PDCA decomposition discipline (router) --------------------
INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT
    (SELECT c.id FROM categories c JOIN sections s ON s.id = c.section_id
        WHERE s.code = 'process' AND c.code = 'planning'),
    'pdca_decompose_delegate_revise',
    'Decompose, delegate, and revise the plan (PDCA)',
    'For a complex or multi-step task (especially coding or work spanning several files), run a PLAN-DO-CHECK-ACT loop tracked by a living TODO; do not improvise delegations and never write the code yourself. PLAN: look at the sources first (read the workspace, delegate a lookup to code-fetcher if needed), then call todo_write(goal, items) to decompose into 3-7 scoped steps with exactly ONE step in_progress. DO: delegate the in_progress step with delegate_to to the right worker (code-runner to write and run code, code-fetcher for lookups), with a precise briefing (goal, constraints, expected output) plus the relevant support_files — one step per delegation, since each worker starts from a fresh context. CHECK: read the worker''s report_back (summary, confidence); judge whether it succeeded or surfaced new work. ACT: call todo_write again to mark that step done, set the next one in_progress, and fold in any suggested_todo_updates the worker returned (add, re-scope, reorder, or retry) — keeping the plan current after each return is the #1 quality lever. Repeat until all steps are done (the plan then clears), then write the final answer yourself.',
    'Migration 120: core of the coding orchestrator — methodical decomposition plus living-TODO revision on every worker return (the #1 quality lever).',
    0, 37, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'pdca_decompose_delegate_revise');

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'jean-michel'),
       (SELECT id FROM paradigms WHERE code = 'pdca_decompose_delegate_revise')
WHERE NOT EXISTS (
    SELECT 1 FROM agent_paradigms
    WHERE agent_id = (SELECT id FROM agents WHERE code = 'jean-michel')
      AND paradigm_id = (SELECT id FROM paradigms WHERE code = 'pdca_decompose_delegate_revise')
);

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code, model_override FROM agents WHERE code IN ('jean-michel','code-runner');
--   -- jean-michel | qwen3:14b   /   code-runner | qwen3-coder:latest
-- SELECT COUNT(*) FROM agent_tools WHERE tool_code='todo_write';          -- 1
-- SELECT COUNT(*) FROM paradigms WHERE code='pdca_decompose_delegate_revise'; -- 1

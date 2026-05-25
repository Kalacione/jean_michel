-- Migration 059: renforcer le déclenchement de manage_todo_list
-- 
-- Problème observé : le LLM satisfait l'obligation de planification via le
-- thought channel (plan_before_complex_action) sans jamais appeler
-- manage_todo_list. Deux corrections :
--   1. planning_with_todos — langage "MUST" + rappel que <think> ≠ persistance
--   2. plan_before_complex_action — pointer vers manage_todo_list pour l'externalisaiton

-- ── planning_with_todos ────────────────────────────────────────────────────
UPDATE paradigms
SET
    content = 'For requests that decompose into 3 or more distinct sub-questions, or whenever comparative / cross-research / multi-source work is involved, you MUST call `manage_todo_list(operation="write")` BEFORE any delegation or tool call.

IMPORTANT: writing your plan in <think> is NOT sufficient — it is ephemeral and private. The TODO list MUST be externalised via `manage_todo_list` so the orchestrator can track progress and surface it in the plan.

Rules:
- Write the full todo list FIRST, then start delegating.
- Call `update_status(id, status)` as soon as a delegation or tool call returns.
- Before each new delegation, scan pending items: if several are independent (no depends_on overlap), emit multiple delegate_to calls in the same turn.
- Stop when all items are `completed` or `skipped`.
- Anti-pattern: do NOT create a todo list for trivial / single-step requests ("what time is it?").',
    modified_at = datetime('now')
WHERE code = 'planning_with_todos';

-- ── plan_before_complex_action ─────────────────────────────────────────────
UPDATE paradigms
SET
    content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers. Then externalise it with `manage_todo_list(operation="write")` — the thought channel alone does not persist.
- For deep_research requests, think through your research strategy before delegating: which agents cover which aspects, what each should deliver. Then call `manage_todo_list(operation="write")` to persist the plan before the first delegation.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, reconsider before delegating.
- After each delegation completes, evaluate the result. If there is a gap: follow up with a targeted sub-delegation, or proceed to synthesis if the gap is acceptable.',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

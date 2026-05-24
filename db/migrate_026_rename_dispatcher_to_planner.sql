-- MIGRATION 026 — rename agent dispatcher → planner
-- Aligns the agent code/name with its role ('planner').
-- KISS: one name, one role, no ambiguity.

UPDATE agents
SET code = 'planner', name = 'Planner', modified_at = datetime('now')
WHERE code = 'dispatcher';

UPDATE paradigms
SET code = 'planner_plan_format', title = 'Planner plan format', modified_at = datetime('now')
WHERE code = 'dispatcher_plan_format';

UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, do NOT plan yourself. Delegate to planner first with the full user request. The planner will produce workspace/plan.md — follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, delegate to planner instead of guessing.',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

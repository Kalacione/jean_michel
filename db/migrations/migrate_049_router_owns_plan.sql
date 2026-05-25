-- migrate_049_router_owns_plan.sql
-- Router-owns-the-plan: update task_plan_file paradigm + add router_synthesis_discipline.

-- 1. Update task_plan_file: clarify that specialists read, router writes.
UPDATE paradigms
SET
  content = '- Plan ownership: plan.md belongs to the router (jean-michel). Only the router writes to it.
- Specialists may call plan_update(action="read") to inspect the plan, never the write actions (init, mark, add_substep, reset).
- Specialists report their findings via the report_findings control verb (not return_to_user, not signal_convergence).
- The router reads each report_findings response and updates plan.md via plan_update(action="mark", ...) and plan_update(action="add_substep", ...).
- Step ids are auto-assigned (S1, S2, S3, …). Never invent ids; only use those returned by plan_update or visible in the plan.
- plan_update(action="init") is idempotent: if a plan already exists it is returned as-is. Do not call init more than once.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'task_plan_file';

-- 2. Add router_synthesis_discipline paradigm (category: inquiry_method, id=34).
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  34,
  'router_synthesis_discipline',
  'Router synthesis discipline',
  '- After any specialist returns via report_findings, your FIRST tool_call MUST be plan_update(action="mark", step_id=..., status=..., findings=<one-line synthesis>).
- If the report contains sub_questions you decide to follow up on, add each via plan_update(action="add_substep", parent_step_id=..., title=..., reason=...).
- Only then may you delegate again or call return_to_user.
- The findings field must capture: (a) what was produced (files_produced), (b) the headline finding, (c) the most important sub_question if any. Be specific. "Done" is not a valid synthesis.',
  'Enforced also at the orchestrator level: if the router calls any tool other than plan_update/delegate_to/ask_human/return_to_user immediately after a specialist returns, a reminder is injected.',
  0,
  100,
  1,
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
);

-- 3. Grant router_synthesis_discipline to jean-michel only.
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE a.code = 'jean-michel' AND p.code = 'router_synthesis_discipline';

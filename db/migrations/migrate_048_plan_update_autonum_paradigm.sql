-- migrate_048: update task_plan_file paradigm to reflect auto-numbered step ids
-- and the new constraint (no id in steps/new_steps, ids always S1..Sn).

UPDATE paradigms
SET content =
'- For deep_research or multi-turn tasks, maintain a workspace/plan.md file as the single source of truth for the task state. Create it via plan_update(action="init", ...) at the start of the first turn.
- When calling plan_update(action="init") or plan_update(action="reset"), pass steps/new_steps as an array of {title, agent?, deliverable?}. Do NOT include an "id" field — ids are auto-assigned as S1, S2, S3, … The response includes "step_ids" listing the assigned ids.
- Read the current plan via plan_update(action="read") before deciding what to do next. The plan shows each step''s id (e.g. S1, S1.1).
- Mark steps as you progress via plan_update(action="mark", step_id="S1", status="in_progress" | "done" | "blocked", findings="..."). Use the exact step_id shown in the plan (e.g. "S1", not "step_1" or "root").
- If a sub-research emerges (disambiguation, link to follow), call plan_update(action="add_substep", parent_step_id="S1", title="...", reason="..."). Use the exact parent step_id from the plan. If the call returns an error listing available step_ids, use one of those.
- NEVER call workspace_create_file with relative_path="plan.md". The plan is managed exclusively via plan_update.
- NEVER invent step ids. Only use ids returned by a previous plan_update call or visible in the current plan.'
WHERE code = 'task_plan_file';

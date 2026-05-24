-- migrate_049: rewrite task_plan_file paradigm with role-aware guidance
-- and attach it to ALL agents that have the plan_update grant (so specialists
-- learn the discipline instead of inventing init/reset calls).

UPDATE paradigms
SET content =
'- A single workspace/plan.md file is the source of truth for the task state. It is managed exclusively via the plan_update tool. NEVER call workspace_create_file with relative_path="plan.md".
- ROUTER (jean-michel) is the SOLE owner of the plan structure:
  - At the very first turn of a deep_research task, call plan_update(action="init", title=..., steps=[...]) once.
  - Use plan_update(action="reset", title=..., new_steps=[...]) only to replace the entire plan after a major scope change.
  - Pass steps/new_steps as an array of {title, agent?, deliverable?}. Do NOT include an "id" field — ids are auto-assigned as S1, S2, S3, … The response returns "step_ids" listing the assigned ids.
- SPECIALISTS (web-search-specialist, wikipedia-specialist, critical-thinker, document-builder, …) MUST NOT call action="init" or action="reset". The plan already exists when you are invoked — your delegation briefing tells you which step_id you own.
  - Call plan_update(action="read") at the start of your turn to confirm the current plan and your assigned step.
  - Call plan_update(action="mark", step_id="S1", status="in_progress" | "done" | "blocked", findings="...") to update your step.
  - If a sub-research emerges, call plan_update(action="add_substep", parent_step_id="S1", title=..., reason=...) — max 3 substeps per delegation.
- ALL AGENTS: only use step_ids that are returned by a previous plan_update call or visible in the current plan. NEVER invent ids like "step_1", "root", or "T1". If an error lists "Available step_ids: [...]", pick one from that list.
- If plan_update(action="init") returns an error saying the plan already exists, that is a bug in your turn — call action="read" instead and continue from there.'
WHERE code = 'task_plan_file';

-- Attach task_plan_file to every agent that has the plan_update grant.
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT DISTINCT at.agent_id, p.id
FROM agent_tools at
CROSS JOIN paradigms p
WHERE at.tool_code = 'plan_update'
  AND p.code = 'task_plan_file';

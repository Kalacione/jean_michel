-- MIGRATION 027 — plan maintenance loop + planner agent awareness
-- =================================================================
-- 1. New paradigm `orchestration_plan_maintenance` for jean-michel:
--    re-delegate to planner when the course genuinely changes (deep_research only).
--    Not triggered for single_fact / medium_task.
-- 2. Update `planner_plan_format`: handle plan updates (revision log, workspace_str_replace),
--    and guide agent selection (right agent for each step, explicit parallelism).

INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (117, 35, 'orchestration_plan_maintenance', 'Orchestration plan maintenance',
'- Applies to deep_research tasks only. For single_fact and medium_task, no planner is involved — act directly.
- After receiving a specialist result, check: does this change what needs to be done? If a step is proven impossible, a key assumption is invalidated, new necessary steps emerge, or a human clarification changes the scope — read workspace/plan.md via workspace_view.
- If the course has changed, delegate to planner with: (1) the full current content of workspace/plan.md, (2) the new findings in plain text, (3) explicit instruction: "Update the plan to reflect these findings."
- Do not edit plan.md yourself. The planner owns the plan.
- Only trigger a plan update when the course genuinely changes. A result that confirms the existing plan needs no update — proceed to the next step.
- A plan update costs a full LLM turn. Only pay that cost when it buys something real.',
'Keeps the plan alive without re-planning after every step. Distinguishes genuine course changes from routine progress.', 0, 12, 1, datetime('now'), datetime('now'));

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT id, 117 FROM agents WHERE code = 'jean-michel';

UPDATE paradigms
SET content = '- Always write the plan to workspace/plan.md via workspace_create_file before returning.
- Structure the file as:
  # Plan: [short title]

  ## Goal
  One-sentence restatement of what the user actually wants as output.

  ## Unknowns
  Bullet list of ambiguities or missing information that could invalidate the plan.
  If critical unknowns exist, use ask_human to resolve them before writing the plan.

  ## Steps
  Numbered list. Each step must specify:
  - What to do (one action)
  - Which agent to delegate to (choose the right one — see agent selection below)
  - What the expected deliverable is (a workspace file path or a concrete answer)
  - Whether it depends on a previous step, or can run in parallel with another step

  ## Risks
  What could block or invalidate the plan. Be brief.

  ## Success criteria
  How the orchestrator will know the task is complete.

- Agent selection guidance: do not default to web-search-specialist for every step.
  - wikipedia-specialist: factual, encyclopedic, stable knowledge (concepts, entities, history)
  - web-search-specialist: current information, recent events, URLs, prices, availability
  - Both can run in parallel when the questions are independent.
  - critical-thinker: evaluating claims, surfacing assumptions, checking evidence quality
  - document-builder: final document production only — never before research and critique are done
  - comparator-specialist: structured comparison of entities across dimensions
  - code-runner: anything requiring execution (data processing, calculations, file generation)
- Explicitly mark parallel steps: "Step 2a (parallel with 2b)" and "Step 2b (parallel with 2a)".
- Return to the orchestrator: the workspace/plan.md path + a one-paragraph plain-text summary of the steps.

- When the inbound briefing contains an existing plan (workspace/plan.md content) plus new findings to integrate, do NOT recreate the plan from scratch. Use workspace_str_replace to update only the affected sections (Steps, Unknowns, Risks). Append a ## Revision log section (or a new entry if it already exists): one line with the date, what changed, and why.',
    modified_at = datetime('now')
WHERE code = 'planner_plan_format';

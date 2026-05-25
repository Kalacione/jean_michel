-- MIGRATION 031 — sprint A2: MANDATORY check-before-create for planner
-- =========================================================================
-- The planner was calling workspace_create_file blindly, receiving "file exists",
-- reading the file with workspace_view, then returning without updating.
-- Adding an explicit ordered protocol at the top of planner_plan_format.

UPDATE paradigms
SET content = 'BEFORE writing or updating the plan:
  1. Call workspace_view(''plan.md'') to check if the file already exists.
  2. If it DOES NOT exist: use workspace_create_file with relative_path=''plan.md''.
  3. If it DOES exist: use workspace_str_replace to update only what changed — never recreate from scratch.
  4. Only call return_to_user AFTER a successful workspace_create_file or workspace_str_replace response — never after an error or after workspace_view alone.

- Always write the plan to plan.md via workspace_create_file before returning.
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
- When workspace_create_file succeeds, call return_to_user(answer=''plan.md written.'') — nothing more. The file is the deliverable, not the answer field.

- When the inbound briefing contains an existing plan (plan.md content) plus new findings to integrate, do NOT recreate the plan from scratch. Use workspace_str_replace to update only the affected sections (Steps, Unknowns, Risks). Append a ## Revision log section (or a new entry if it already exists): one line with the date, what changed, and why.',
    modified_at = datetime('now')
WHERE code = 'planner_plan_format';

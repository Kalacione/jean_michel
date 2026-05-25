-- MIGRATION 033 — sprint B: living plan with Status table + jean-michel tracking
-- =============================================================================
-- B1: Add ## Status execution tracker section to the plan template.
--     The orchestrator fills in step statuses as it progresses.
-- B2+B3: jean-michel must read plan.md after planner returns, follow ⬜ pending
--        steps in order, and mark each step ✅ done after delegation completes.

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

  ## Status
  Execution tracker — the orchestrator updates this after each delegation.
  | Step | Agent | Status | Deliverable |
  |------|-------|--------|-------------|
  | 1    | agent-name | ⬜ pending | output.md |
  Statuses: ⬜ pending / 🔄 in_progress / ✅ done

  ## Risks
  What could block or invalidate the plan. Be brief.

  ## Success criteria
  How the orchestrator will know the task is complete.

- Agent selection guidance: do not default to web-search-specialist for every step.
  - wikipedia-specialist: factual, encyclopedic, stable knowledge (concepts, entities, history)
  - web-search-specialist: current information, recent events, URLs, prices, availability
  - Default for research tasks: run BOTH in parallel. wikipedia-specialist covers stable/
    conceptual knowledge; web-search-specialist covers current state and verification.
    Use only one when the question is exclusively time-sensitive (web-search only) or
    exclusively historical/definitional (wikipedia only).
  - critical-thinker: evaluating claims, surfacing assumptions, checking evidence quality
  - document-builder: final document production only — never before research and critique are done
  - comparator-specialist: structured comparison of entities across dimensions
  - code-runner: anything requiring execution (data processing, calculations, file generation)
- Explicitly mark parallel steps: "Step 2a (parallel with 2b)" and "Step 2b (parallel with 2a)".
- When workspace_create_file succeeds, call return_to_user(answer=''plan.md written.'') — nothing more. The file is the deliverable, not the answer field.

- When the inbound briefing contains an existing plan (plan.md content) plus new findings to integrate, do NOT recreate the plan from scratch. Use workspace_str_replace to update only the affected sections (Steps, Status, Unknowns, Risks). Preserve all ✅ done rows in the Status table unchanged. Append a ## Revision log section (or a new entry if it already exists): one line with the date, what changed, and why.',
    modified_at = datetime('now')
WHERE code = 'planner_plan_format';

UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, delegate to planner FIRST — no exceptions. Do not start any research or delegation before plan.md exists.
- The planner will produce plan.md. Follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, delegate to planner instead of guessing.
- After the planner returns: call workspace_view(''plan.md'') to read the current plan. Find the first ⬜ pending step in the Status table and execute it. Do NOT reconstruct the plan from memory — always read plan.md.
- After each delegation completes: call workspace_str_replace on plan.md to mark the step ✅ done in the Status table (replace ''⬜ pending'' with ''✅ done'' on that row).',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

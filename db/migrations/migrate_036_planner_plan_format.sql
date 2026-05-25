-- MIGRATION 036 — planner: atomic step sizing rules
-- =====================================================
-- Umbrella steps ("find all sources in all domains") caused specialists
-- to loop indefinitely. The planner must now create one step per domain/
-- sub-question, each answerable in max 5 searches.

UPDATE paradigms
SET content = 'MANDATORY write protocol — follow exactly:
  1. Call workspace_create_file with relative_path=''plan.md''.
  2a. If it succeeds → call return_to_user(answer=''plan.md written.'').
  2b. If you get {"error": "File already exists"} →
       i.  Call workspace_view(''plan.md'') to read the current plan.
       ii. Call workspace_str_replace to update only what changed — never recreate from scratch.
       iii.Call return_to_user(answer=''plan.md updated.'').
  Never call return_to_user after an error or after workspace_view alone.

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

  STEP SIZING RULES — mandatory:
  - Each research step must target ONE specific domain, technology, or sub-question.
    Never create an umbrella step covering multiple unrelated domains at once.
  - If the topic spans several domains, create one parallel step per domain.
    Example — DO NOT: ''Step 1: find sources in Science, News, Tech, Geography''
    Example — DO: ''Step 1a: Science sources | 1b: News sources | 1c: Tech sources | 1d: Geography sources''
  - A step that would require more than 5 searches to complete is too broad — split it.
  - An umbrella research step WILL cause the agent to loop indefinitely. Avoid it.

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

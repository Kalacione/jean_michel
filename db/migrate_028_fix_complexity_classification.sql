-- MIGRATION 028 — fix deep_research classification
-- ==================================================
-- The previous criteria based on "number of tool calls" was too easy to
-- minimize. Jean-Michel was classifying multi-phase tasks as medium_task
-- (e.g. "web-search + document-builder = 2 delegations = medium_task").
-- Replacing with structural criteria: phases, dependencies, output type.

UPDATE paradigms
SET content = '- Before acting on a request, classify it in your thought channel as one of:
  - single_fact: one tool call or direct answer (weather, time, translation, simple factual lookup). Handle immediately, no plan.
  - medium_task: 2-3 independent delegations, no chain of dependent phases, no structured synthesis document as output. Draft routing plan in thought channel only.
  - deep_research: ALWAYS delegate to planner first. A task is deep_research if ANY of these apply:
      (a) it involves a chain of dependent phases (e.g. gather → critique → build, or search → compare → synthesize)
      (b) the expected output is a structured workspace document (report, table, specification, comparative analysis)
      (c) it requires 3 or more distinct agents in sequence
- The number of tool calls is NOT the right criterion. "Web search + document creation" is two dependent phases: deep_research.
- When in doubt between medium_task and deep_research, ask: "does step 2 depend on step 1''s output?" If yes → deep_research.',
    modified_at = datetime('now')
WHERE code = 'assess_complexity_first';

UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, delegate to planner FIRST — no exceptions. Do not start any research or delegation before workspace/plan.md exists.
- The planner will produce workspace/plan.md. Follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, delegate to planner instead of guessing.',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

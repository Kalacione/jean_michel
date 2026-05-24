-- MIGRATION 044 — remove `planner` agent + phase control verbs
-- ====================================================================
-- The planner LLM agent was non-deterministic on a deterministic task
-- (patching a markdown file). Replaced by the mechanical plan_update tool
-- (see doc 06). Phase control verbs (planner_done / gather_done / critic_done /
-- build_done) are now declared in prompts.py and handled by the orchestrator.
--
-- NOTE: SQLite does not support ALTER TABLE … MODIFY COLUMN, so the 'planner'
-- role cannot be removed from the agents.role CHECK constraint in the live DB.
-- New installs use an updated schema.sql that excludes 'planner' from the CHECK.
-- Existing rows with role='planner' remain valid (inactive) for FK integrity.

-- 1. Remove paradigm bindings for planner
DELETE FROM agent_paradigms
WHERE agent_id = (SELECT id FROM agents WHERE code = 'planner');

-- 2. Remove tool grants for planner
DELETE FROM agent_tools
WHERE agent_id = (SELECT id FROM agents WHERE code = 'planner');

-- 3. Remove workspace grant for planner
DELETE FROM agent_workspace_grants
WHERE agent_id = (SELECT id FROM agents WHERE code = 'planner');

-- 4. Deactivate the agent (keep row for historical FK in requests/artifacts)
UPDATE agents
SET active = 0, modified_at = datetime('now')
WHERE code = 'planner';

-- 5. Deactivate planner-specific paradigms
UPDATE paradigms
SET active = 0, modified_at = datetime('now')
WHERE code IN ('planner_plan_format', 'plan_not_execute');

-- 6. Rewrite assess_complexity_first: remove 'ALWAYS delegate to planner first'
UPDATE paradigms
SET content = '- Before acting on a request, classify it in your thought channel as one of:
  - single_fact: one tool call or direct answer (weather, time, translation, simple factual lookup). Handle immediately, no plan.
  - medium_task: 2-3 independent delegations, no chain of dependent phases, no structured synthesis document as output. Draft routing plan in thought channel only.
  - deep_research: A task is deep_research if ANY of these apply:
      (a) it involves a chain of dependent phases (e.g. gather → critique → build, or search → compare → synthesize)
      (b) the expected output is a structured workspace document (report, table, specification, comparative analysis)
      (c) it requires 3 or more distinct agents in sequence
- The number of tool calls is NOT the right criterion. "Web search + document creation" is two dependent phases: deep_research.
- When in doubt between medium_task and deep_research, ask: "does step 2 depend on step 1''s output?" If yes → deep_research.',
    modified_at = datetime('now')
WHERE code = 'assess_complexity_first';

-- 7. Rewrite plan_before_complex_action: replace 'delegate to planner' with
--    'call plan_update' (plan_update tool will be added in migration 045)
UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, call plan_update FIRST — no exceptions. Do not start any research or delegation before plan.md exists.
- plan_update will write plan.md. Follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, call plan_update instead of guessing.
- After plan_update returns: call workspace_view(''plan.md'') to read the current plan. Find the first ⬜ pending step in the Status table and execute it. Do NOT reconstruct the plan from memory — always read plan.md.
- After each delegation completes:
  Read the return_to_user answer. If the agent reported gaps (e.g. ''Missing: Geography''),
  decide before marking ✅:
    - Gap is minor or acceptable → mark ✅ done and continue.
    - Gap requires a targeted follow-up → create a new focused sub-delegation first
      (same agent, narrower mission: e.g. ''find Geography sources only'').
    - Gap invalidates the plan → call plan_update to update plan.md before continuing.
  Then call workspace_str_replace on plan.md to mark the step ✅ done.',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

-- 8. Rewrite orchestration_plan_maintenance: remove 'delegate to planner'
UPDATE paradigms
SET content = '- Applies to deep_research tasks only. For single_fact and medium_task, act directly.
- After receiving a specialist result, check: does this change what needs to be done? If a step is proven impossible, a key assumption is invalidated, new necessary steps emerge, or a human clarification changes the scope — read workspace/plan.md via workspace_view.
- If the course has changed, call plan_update with: (1) the full current content of workspace/plan.md, (2) the new findings in plain text, (3) explicit instruction: "Update the plan to reflect these findings."
- Only trigger a plan update when the course genuinely changes. A result that confirms the existing plan needs no update — proceed to the next step.
- A plan update costs a tool call. Only pay that cost when it buys something real.',
    modified_at = datetime('now')
WHERE code = 'orchestration_plan_maintenance';

-- 9. Create conversation_phases table for phase-completion tracking
CREATE TABLE IF NOT EXISTS conversation_phases (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  phase           TEXT NOT NULL CHECK (phase IN ('planner','gather','critic','build')),
  agent_code      TEXT NOT NULL,
  summary         TEXT NOT NULL,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversation_phases_conv
  ON conversation_phases(conversation_id);

-- 10. Grant plan_update to agents that need to manage the plan
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'),           'plan_update'),
  ((SELECT id FROM agents WHERE code='web-search-specialist'), 'plan_update'),
  ((SELECT id FROM agents WHERE code='wikipedia-specialist'),  'plan_update'),
  ((SELECT id FROM agents WHERE code='critical-thinker'),      'plan_update'),
  ((SELECT id FROM agents WHERE code='document-builder'),      'plan_update');

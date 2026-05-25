-- MIGRATION 060 — set_task_class tool + classify-before-delegate gates
--
-- Adds the set_task_class tool to jean-michel's toolkit and wires the
-- assess_complexity_first paradigm to make the LLM call it (instead of only
-- classifying in <think>).
--
-- Two orchestrator-side structural gates now enforce:
--   Gate 1 (classify_first): delegate_to blocked until set_task_class called.
--   Gate 2 (plan_first):     delegate_to blocked for deep_research until
--                            manage_todo_list(write) has been called.

-- 1. Grant set_task_class to jean-michel (router, agent_id=1).
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
VALUES (1, 'set_task_class');

-- 2. Update assess_complexity_first: LLM must now CALL set_task_class, not
--    just think the classification.  All other criteria are unchanged.
UPDATE paradigms
SET content = '- Before acting on a request, classify it as one of:
  - single_fact: one tool call or direct answer (weather, time, translation, simple factual lookup). Handle immediately, no plan.
  - medium_task: 2-3 independent delegations, no chain of dependent phases, no structured synthesis document as output. Draft routing plan in thought channel only.
  - deep_research: A task is deep_research if ANY of these apply:
      (a) it involves a chain of dependent phases (e.g. gather → critique → build, or search → compare → synthesize)
      (b) the expected output is a structured workspace document (report, table, specification, comparative analysis)
      (c) it requires 3 or more distinct agents in sequence
- The number of tool calls is NOT the right criterion. "Web search + document creation" is two dependent phases: deep_research.
- When in doubt between medium_task and deep_research, ask: "does step 2 depend on step 1''s output?" If yes → deep_research.
- After classifying in your thought channel, call set_task_class(task_class=...) to persist the classification before any delegation.',
    modified_at = datetime('now')
WHERE code = 'assess_complexity_first';

-- 3. Simplify planning_with_todos: the structural gate now enforces the
--    deep_research case; align the text with the new gating model.
UPDATE paradigms
SET content = 'For deep_research requests: after set_task_class("deep_research"), you MUST call manage_todo_list(operation="write", todos=[...]) before any delegation. List all planned steps with their assignee_hint and expected deliverables.

For medium_task requests with 3+ sub-questions or comparative work: also call manage_todo_list(operation="write") to externalise the routing plan before delegating.

IMPORTANT: writing your plan in <think> is NOT sufficient — it is ephemeral and private. The TODO list is persisted and surfaced in plan.md so the orchestrator and peer specialists can track progress.',
    modified_at = datetime('now')
WHERE code = 'planning_with_todos';

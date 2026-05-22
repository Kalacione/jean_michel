-- =============================================================
-- Migration 006 — code-runner agent + delegation routing fixes
-- Apply to existing jeanmichel.db instances:
--   sqlite3 jeanmichel.db < db/migrate_006_code_runner.sql
-- Idempotent: safe to run multiple times.
--
-- Fixes two real-world bugs discovered during testing:
--   1. Jean-Michel confuses agent codes with tool names (calls
--      'workspace-manager' as a direct tool function instead of
--      using delegate_to). Fixed by two new paradigms bound to
--      jean-michel.
--   2. No agent had bash_sandbox in agent_tools. The entire Docker
--      execution capability was unreachable. Fixed by the new
--      code-runner agent.
-- =============================================================

PRAGMA foreign_keys = ON;

-- 1. New paradigm: agents ≠ tools (category tool_discipline = 29)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES
(97, 29, 'delegate_not_direct_call', 'Agents are not tools',
 '- The entries listed under "Delegation targets" are AGENT codes, not tool functions.
- To hand off work to an agent, call delegate_to(agent_code=''...'', briefing=''...'', expected=''...'').
- Never call an agent code (workspace-manager, code-runner, document-builder, etc.) as a direct tool name — it will always fail with "unknown tool".',
 'Prevents the Gemma 4 pattern of confusing available-agent names with callable tool functions.',
 0, 5, 1, datetime('now'), datetime('now'));

-- 2. New paradigm: routing for code execution tasks (category handoff = 11)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES
(98, 11, 'code_execution_routing', 'Route code execution to code-runner',
 '- When the user wants to create AND execute code (Python, bash, etc.) in the workspace, delegate to code-runner.
- code-runner can write files with workspace tools and run them inside the Docker sandbox in one turn.
- workspace-manager can only manage files — it cannot execute code.
- Never ask the user to run the code themselves unless the Docker sandbox is explicitly unavailable.',
 'Ensures code-write+run tasks are routed to the agent that can actually execute them.',
 0, 40, 1, datetime('now'), datetime('now'));

-- 3. Bind both new paradigms to jean-michel (id=1)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES
  (1, 97),
  (1, 98);

-- 4. code-runner agent
INSERT OR IGNORE INTO agents (id, code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
(12, 'code-runner', 'Code Runner', 'specialist',
 'Write code files to the conversation workspace and execute them inside the Docker sandbox. Handles the full write-then-run cycle: create or edit Python/bash scripts with workspace tools, execute with bash_sandbox, and report results. Never returns code inline — always writes to workspace files.',
 1, 0.1, 1, datetime('now'), datetime('now'));

-- 5. code-runner paradigm bindings
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES
  (12,  4),  -- one_question_at_a_time
  (12, 36),  -- parse_briefing_first
  (12, 68),  -- address_then_clarify
  (12, 77),  -- plan_before_complex_action  (plan script before running)
  (12, 79),  -- prefer_tool_over_parametric_for_volatile
  (12, 80),  -- no_permission_for_obvious_tools
  (12, 91),  -- workspace_tools_only
  (12, 92);  -- report_before_acting

-- 6. code-runner tool grants
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (12, 'conv_read_file'),
  (12, 'workspace_create_file'),
  (12, 'workspace_str_replace'),
  (12, 'workspace_view'),
  (12, 'workspace_list'),
  (12, 'bash_sandbox');

-- 7. code-runner workspace write grant
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (12);

-- 8. code-runner sandbox grants (python3, bash, and common inspection commands)
INSERT OR IGNORE INTO agent_sandbox_grants (agent_id, command) VALUES
  (12, 'python3'),
  (12, 'bash'),
  (12, 'cat'),
  (12, 'ls'),
  (12, 'jq'),
  (12, 'echo');

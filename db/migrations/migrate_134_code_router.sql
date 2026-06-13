-- =============================================================================
-- migrate_134_code_router.sql
-- =============================================================================
-- ROUND 3: dedicated `code-router` — the Tier-1 entry in `code` mode (instead of
-- the generalist jean-michel). Same orchestrator machinery (PDCA, CRP, workers,
-- deliberation) but a LEAN, code-focused identity: it binds only ~15 code/routing
-- paradigms (vs jean-michel's 46 generalist ones), so a small model delegates
-- reliably instead of chit-chatting. jean-michel stays the entry for
-- analyse/chat/vocal. Switch is on the explicit `code` mode (turn_runner).
--
-- Reuses existing paradigm rows (zero new content). Idempotent (INSERT OR IGNORE).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ---- Agent (role=router, code reasoner model) ------------------------------
INSERT OR IGNORE INTO agents
    (code, name, role, mission, thinking_mode, temperature, active, model_override, sandbox_image, created_at, modified_at)
VALUES (
    'code-router', 'Code Router', 'router',
    'Router for code mode: you orchestrate work on the ATTACHED code repository — you do NOT write, run, or answer code yourself. Decompose the request into a living TODO (todo_write) and delegate each step to a fresh worker — code-runner (write/run/test in the repo worktree) or code-fetcher (external lookups) — with a precise briefing; the system assembles the repo context for them. Read their report_back, revise the TODO, repeat, then synthesize the result for the human. Never claim you cannot see the repo: delegate.',
    1, 0.2, 1, 'qwen3:14b', NULL, datetime('now'), datetime('now')
);

-- ---- Paradigm bindings (REUSE existing rows — lean, code-focused) -----------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'code-router'), p.id
FROM paradigms p
WHERE p.code IN (
    'pdca_decompose_delegate_revise', 'code_runner_for_code_production_briefs',
    'delegate_not_direct_call', 'nested_delegation_discipline', 'router_synthesis_discipline',
    'output_contract_no_inline_dump', 'briefing_contract', 'memory_discipline',
    'tool_note_discipline', 'no_permission_for_obvious_tools', 'graphify_codebase_navigation',
    'plan_before_complex_action', 'address_then_clarify', 'concise_output', 'brutal_truth'
);

-- ---- Delegation targets (code workers only) --------------------------------
INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'code-router'), v
FROM (SELECT 'code-runner' AS v UNION SELECT 'code-runner-node' UNION SELECT 'code-fetcher');

-- ---- Tool grants (owns the PDCA TODO + memory + read the scratch workspace) -
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'code-router'), v
FROM (SELECT 'todo_write' AS v UNION SELECT 'manage_memory' UNION SELECT 'workspace_view');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code, role, model_override FROM agents WHERE code='code-router';  -- router | qwen3:14b
-- SELECT COUNT(*) FROM agent_paradigms WHERE agent_id=(SELECT id FROM agents WHERE code='code-router'); -- 15

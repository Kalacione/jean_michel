-- =============================================================================
-- migrate_131_deliberation.sql
-- =============================================================================
-- P5: the dialectic deliberation layer. Two new specialists, invoked by the
-- deterministic deliberation engine (NOT by the router's delegate_to) :
--   * critical-coder : critical-thinker generalised to code. Inspects an
--     approach or a diff from ONE assigned angle (thesis/antithesis/synthesis,
--     or a review angle). Read-only repo nav; never writes code.
--   * sergent-kiss   : the anti-over-engineering gate. Verdict via report_back
--     confidence (high/medium = PASS, low = REWORK + cuts in low_confidence_reason).
--
-- Two new paradigms, BOTH gated to paradigm_modes='code'. critical-coder also
-- reuses a curated subset of the existing critical-thinking paradigms (shared
-- DNA). Neither agent is a delegation target of jean-michel — they are internal
-- to the engine.
--
-- Idempotent: INSERT OR IGNORE on agents/joins, NOT EXISTS on paradigm inserts.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ---- Agents ----------------------------------------------------------------
INSERT OR IGNORE INTO agents
    (code, name, role, mission, thinking_mode, temperature, active, model_override, sandbox_image, created_at, modified_at)
VALUES (
    'critical-coder', 'Critical Coder', 'specialist',
    'Inspect a coding approach or a diff from ONE assigned angle (thesis / antithesis / synthesis, or a review angle). Verify against the real code with repo_read / repo_grep / repo_glob and the graph; surface unstated assumptions, failure modes, simpler alternatives, and side effects on callers. You do NOT write or run code — you return a focused critical analysis. Generalises critical-thinker to code and architecture.',
    1, 0.2, 1, 'gemma4:26b', NULL, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agents
    (code, name, role, mission, thinking_mode, temperature, active, model_override, sandbox_image, created_at, modified_at)
VALUES (
    'sergent-kiss', 'Sergent KISS', 'specialist',
    'The anti-over-engineering gate. Given a proposed approach (or a diff) and its critiques, decide whether it is the SIMPLEST design that solves exactly what was asked — no speculative generality, no unrequested features, no layer that does not earn its keep. Report PASS via report_back(confidence=high or medium) or REWORK via confidence=low with the precise cuts in low_confidence_reason.',
    1, 0.1, 1, NULL, NULL, datetime('now'), datetime('now')
);

-- ---- Paradigms (code mode) -------------------------------------------------
INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 28, 'critical_coder_method', 'Critical coder method',
'You inspect a coding approach or a diff from ONE assigned angle (stated at the top of your briefing: thesis, antithesis, synthesis, or a review angle). Verify claims against the REAL code — use repo_read, repo_grep, repo_glob, and the graph rather than assuming. Surface unstated assumptions, concrete failure modes, simpler alternatives, and side effects on callers (cite path:line). You do NOT write or run code; you return a focused critical analysis via report_back. Stay on your assigned angle — do not try to cover all of them at once.',
'P5: critical-coder method — generalises the critical-thinker discipline to code, one angle per pass, grounded in the real code.',
0, 40, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'critical_coder_method');

INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 12, 'sergent_kiss_gate', 'Sergent KISS gate',
'You are the anti-over-engineering gate. You receive a proposed approach (or a diff) together with its critiques. Decide whether it is the SIMPLEST design that solves EXACTLY what was asked: no speculative generality, no features that were not requested, no abstraction or layer that does not earn its keep. Verdict via report_back: confidence=high or medium means PASS (appropriately simple); confidence=low means REWORK — put the specific cuts to make in low_confidence_reason (drop this layer, inline this helper, delete this option). If the task was trivial enough that this deliberation was unnecessary, say so. Be brief and decisive.',
'P5: the KISS gate — turns over-engineering into a structured PASS/REWORK verdict via report_back confidence (no hallucinated score, cf. convergence_gate lesson).',
0, 41, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'sergent_kiss_gate');

-- mode-gate both to 'code' only (anti-leak)
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'code' FROM paradigms WHERE code IN ('critical_coder_method', 'sergent_kiss_gate');

-- ---- Bindings --------------------------------------------------------------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'critical-coder'),
       (SELECT id FROM paradigms WHERE code = 'critical_coder_method');
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'sergent-kiss'),
       (SELECT id FROM paradigms WHERE code = 'sergent_kiss_gate');

-- critical-coder reuses a curated critical-thinking subset + report_back_format
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'critical-coder'), p.id
FROM paradigms p
WHERE p.code IN ('assumption_surface', 'steelman_first', 'hold_tension',
                 'occam_razor', 'understand_before_judge', 'report_back_format');

-- sergent-kiss needs report_back formatting
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'sergent-kiss'),
       (SELECT id FROM paradigms WHERE code = 'report_back_format');

-- ---- Tool grants (read-only; neither writes code) --------------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'critical-coder'), v
FROM (SELECT 'repo_read' AS v UNION SELECT 'repo_grep' UNION SELECT 'repo_glob' UNION SELECT 'workspace_view');

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'sergent-kiss'), v
FROM (SELECT 'repo_read' AS v UNION SELECT 'workspace_view');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code, role, model_override FROM agents WHERE code IN ('critical-coder','sergent-kiss');
-- SELECT COUNT(*) FROM paradigms WHERE active = 1;  -- 124

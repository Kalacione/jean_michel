-- Migration 008: convergence_gate paradigm
-- Teaches analytical agents to signal convergence instead of looping indefinitely
-- at recursion depth >= 2. Bound to: critical-thinker, meta-analyst, synthesizer.

-- Paradigm 100: convergence_gate (category: recursion, id=18)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
    100,
    18,
    'convergence_gate',
    'Convergence gate',
    '- At recursion_depth >= 2, after receiving results from sub-agents, evaluate whether further analysis would add new information or simply restate what is already known.
- If your analysis has plateaued (no new contradictions to resolve, no new evidence to gather, no new sub-questions opened), call signal_convergence(synthesis, open_questions) instead of delegating further.
- signal_convergence is NOT giving up — it is the correct exit when depth has been reached and the parent agent is better positioned to integrate the results.
- If a delegate_to result contains "converged": true, the child has already signalled it reached its depth limit. Integrate its synthesis; do not re-delegate the same question downward.
- Only call signal_convergence when genuinely converged. If meaningful work remains, continue.',
    'Prevents infinite analytical loops by giving agents an explicit, structured exit signal when depth > 2 and further recursion would not improve the output.',
    0,
    90,
    1,
    datetime('now'),
    datetime('now')
);

-- Bind to analytical agents only (not document-builder, workspace-manager, etc.)
-- critical-thinker (id=8)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (8, 100);

-- meta-analyst (id=11)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (11, 100);

-- synthesizer (id=3)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (3, 100);

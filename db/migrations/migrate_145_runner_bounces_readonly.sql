-- =============================================================================
-- migrate_145_runner_bounces_readonly.sql
-- =============================================================================
-- P4 (casting durci). migrate_142 already routes repo analysis to code-analyst on
-- the code-router side (paradigm 152). This adds the DEFENSIVE backstop on the
-- worker side: if the router still mis-casts a read-only analysis/audit to a code
-- RUNNER, the runner must BOUNCE it (report_back low -> route to code-analyst)
-- instead of fumbling into a code-production spiral (conv 127ce9a1: 129 calls).
-- Bound to code-runner (12) AND code-runner-node (18).
-- Idempotent : INSERT OR IGNORE re-runnable.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO paradigms VALUES(
  153, 35, 'bounce_readonly_to_code_analyst', 'Bounce read-only briefs to code-analyst',
  '- You PRODUCE or CHANGE code (write/edit/run/test in the worktree). If a briefing asks only to UNDERSTAND, ANALYSE, AUDIT, EXPLAIN, MAP, or answer "is X used?" / "how does Y work?" with NO file to change and nothing to run, that is read-only analysis - code-analyst''s job, not yours.
- Do NOT start writing or running code to satisfy a read-only brief. Bounce it: report_back(confidence="low", low_confidence_reason="Read-only repo analysis - route to code-analyst") so the router re-casts.
- Proceed normally when the brief asks to create, edit, fix, implement, refactor, or run something, or expects a diff. When in doubt and a concrete change is named, proceed.',
  'Casting backstop (conv 127ce9a1): a read-only analysis mis-cast to code-runner triggered a 129-call production spiral. The runner bounces read-only briefs to code-analyst instead of fumbling them.',
  0, 37, 1, '2026-06-13 00:00:00', '2026-06-13 00:00:00'
);
INSERT OR IGNORE INTO agent_paradigms VALUES(12, 153);  -- code-runner
INSERT OR IGNORE INTO agent_paradigms VALUES(18, 153);  -- code-runner-node

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code FROM paradigms WHERE id=153;                                  -- bounce_readonly_to_code_analyst
-- SELECT agent_id FROM agent_paradigms WHERE paradigm_id=153 ORDER BY agent_id; -- 12, 18

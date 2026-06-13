-- =============================================================================
-- migrate_137_git_checkpoint.sql
-- =============================================================================
-- Étage C / C1 — git checkpoint discipline. Now that the code-mode checkout is a
-- standalone clone with a committer identity (worktree.py), the worker CAN commit
-- its work on the conversation branch via repo_exec. This paradigm tells it to:
-- checkpoint coherent, tested changes as small commits — the branch is the
-- deliverable and the undo. Code-only, bound to both coding workers.
--
-- Idempotent: NOT EXISTS guard on the paradigm ; INSERT OR IGNORE on joins.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 14, 'git_checkpoint_discipline', 'Git checkpoint discipline',
'After a coherent change that you have tested green in code mode, checkpoint it on the conversation branch via repo_exec: stage and commit your work (e.g. git add -A && git commit -m with a concise, specific message). The branch jm/conv-<id> IS the deliverable and your undo — prefer small, working commits over one giant change, and never commit a failing test suite. Review what changed with repo_git (status / diff) before committing.',
'Étage C/C1: the checkout is a clone with a committer identity, so the worker can git-commit via repo_exec; checkpointing gives a reviewable history + real undo (the git safety-net thesis).',
0, 40, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'git_checkpoint_discipline');

-- code mode only (anti-leak) -------------------------------------------------
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'code' FROM paradigms WHERE code = 'git_checkpoint_discipline';

-- bind to BOTH coding workers ------------------------------------------------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a
CROSS JOIN paradigms p
WHERE a.code IN ('code-runner', 'code-runner-node')
  AND p.code = 'git_checkpoint_discipline';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id
--   WHERE p.code='git_checkpoint_discipline';   -- code
-- SELECT COUNT(*) FROM agent_paradigms ap JOIN paradigms p ON p.id=ap.paradigm_id
--   WHERE p.code='git_checkpoint_discipline';   -- 2

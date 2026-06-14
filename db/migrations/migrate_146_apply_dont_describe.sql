-- =============================================================================
-- migrate_146_apply_dont_describe.sql
-- =============================================================================
-- Conv 825fb5b3 : code-runner a "réussi" 9 délégations (confidence=high) en DÉCRIVANT
-- les edits ("I've analyzed... the changes required are: 1,2,3") sans jamais appeler
-- repo_edit/repo_write → worktree diff VIDE, "✅ fait" mensonger. La garde déterministe
-- (orchestrator A1 : diff vide + success → downgrade low) attrape le cas ; ce paradigme
-- recadre le comportement à la source. Lié à code-runner (12) + code-runner-node (18).
-- Idempotent : INSERT OR IGNORE re-runnable.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO paradigms VALUES(
  154, 35, 'apply_dont_describe', 'Apply edits, never just describe them',
  '- You APPLY changes with repo_edit / repo_write / repo_exec. DESCRIBING the edits you "would" make ("the changes required are: 1, 2, 3") is NOT doing the task.
- NEVER report_back success for edits you did not actually apply via a tool call. An UNCHANGED repository = failure, not completion — the system verifies the diff and will send it back to you.
- If the brief is genuinely read-only (analyse / audit / "is X used?"), it is code-analyst''s job, not yours (cf. bounce_readonly_to_code_analyst).',
  'Conv 825fb5b3 : 9 délégations code-runner high, diff worktree vide, "fait" halluciné. La chaîne ne doit jamais rubber-stamper un repo inchangé.',
  0, 38, 1, '2026-06-14 00:00:00', '2026-06-14 00:00:00'
);
INSERT OR IGNORE INTO agent_paradigms VALUES(12, 154);  -- code-runner
INSERT OR IGNORE INTO agent_paradigms VALUES(18, 154);  -- code-runner-node

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code FROM paradigms WHERE id=154;                                   -- apply_dont_describe
-- SELECT agent_id FROM agent_paradigms WHERE paradigm_id=154 ORDER BY agent_id; -- 12, 18

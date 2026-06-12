-- =============================================================================
-- migrate_130_code_paradigms.sql
-- =============================================================================
-- P4: behavioural discipline for code-mode interventions. Two new paradigms,
-- BOTH gated to paradigm_modes='code' (anti-leak into chat/analyse/vocal) and
-- bound to BOTH coding workers (code-runner, code-runner-node) so they never
-- diverge. Plus: bind the existing graphify navigation paradigm to code-runner,
-- and grant read-only repo navigation to meta-analyst (spillover — only active
-- in code-mode delegations, where a worktree exists).
--
-- Idempotent: NOT EXISTS guards on the paradigm inserts; INSERT OR IGNORE on the
-- (PK-backed) paradigm_modes / agent_paradigms / agent_tools join tables.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- 1) repo_intervention_discipline (Code / anchoring) -------------------------
INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 14, 'repo_intervention_discipline', 'Repo intervention discipline',
'When intervening on the real codebase (code mode, repo_* tools): ORIENT first — locate the exact code and its callers with repo_grep / repo_glob, the graphify graph, and the reconstructed context, before editing; do not guess file paths. READ before edit — repo_read a file before repo_edit / repo_write on it (the edit is refused otherwise); build old_str verbatim from the read and never include the line-number prefix. SMALLEST diff that works — change only what the task requires; do not refactor, rename, reformat, or improve code you were not asked to touch; a fix does not need surrounding cleanup; do not add comments, docstrings, or type hints to code you did not change. TEST after editing — run repo_test before reporting the step done; if it fails, read the failures and fix them, never report success on a red suite. After a STRUCTURAL change (a new, renamed, or moved function, class, or module) call repo_graph_refresh so later graph lookups stay accurate. SECURITY — never introduce command injection, hardcoded secrets, or unsafe deserialization; if you notice insecure code you wrote, fix it immediately.',
'P4: deterministic code-intervention discipline (read-before-edit, minimal diff, test-after, graph-refresh) — the behavioural complement to the repo_* tools and their gates.',
0, 38, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'repo_intervention_discipline');

-- 2) prefer_repo_tools_over_bash (Process / tool_discipline) -----------------
INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT 29, 'prefer_repo_tools_over_bash', 'Prefer repo tools over bash',
'Prefer the dedicated repo_* tools over bash_sandbox for files: to READ use repo_read (not cat, head, or sed), to SEARCH use repo_grep (not grep or rg), to FIND files use repo_glob (not find or ls), to EDIT use repo_edit or repo_write (not sed or echo redirection). Use bash_sandbox for what it is for: RUNNING code and commands, not reading or rewriting files. Reference code locations as path:line so they are navigable.',
'P4: steer coding workers to the deterministic, auditable host tools instead of opaque bash file operations.',
0, 39, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'prefer_repo_tools_over_bash');

-- mode-gate both new paradigms to 'code' only (anti-leak) ---------------------
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'code' FROM paradigms
WHERE code IN ('repo_intervention_discipline', 'prefer_repo_tools_over_bash');

-- bind both new paradigms to BOTH coding workers -----------------------------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a
CROSS JOIN paradigms p
WHERE a.code IN ('code-runner', 'code-runner-node')
  AND p.code IN ('repo_intervention_discipline', 'prefer_repo_tools_over_bash');

-- the editor (code-runner) also gets the graphify navigation paradigm --------
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'code-runner'),
       (SELECT id FROM paradigms WHERE code = 'graphify_codebase_navigation');

-- spillover: read-only repo navigation for meta-analyst ----------------------
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, v.tool_code
FROM agents a
CROSS JOIN (
    SELECT 'repo_read' AS tool_code UNION ALL
    SELECT 'repo_grep' UNION ALL
    SELECT 'repo_glob'
) v
WHERE a.code = 'meta-analyst';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT COUNT(*) FROM paradigms WHERE active = 1;  -- 122
-- SELECT p.code FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id WHERE pm.mode='code';
--   -- includes repo_intervention_discipline, prefer_repo_tools_over_bash, pdca_decompose_delegate_revise

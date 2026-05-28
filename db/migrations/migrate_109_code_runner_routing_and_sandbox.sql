-- =============================================================================
-- migrate_109_code_runner_routing_and_sandbox.sql
-- =============================================================================
-- Two fixes following observation 2026-05-28 (user asked "écris-moi un script
-- qui parse un JSON ligne par ligne" — jean-michel répondit inline au lieu
-- de déléguer à code-runner) :
--
-- P1 — Routing : add a router-side paradigm telling jean-michel that ANY
--      "écris / implement / fix / make X work" brief must be delegated to
--      code-runner. Mirror of `news_first_for_news_briefs`. Without this
--      paradigm, the LLM treated the request as "explain how to do X"
--      instead of "produce a runnable script".
--
-- P2 — Code-runner mission rewritten so the truncated form (≤160 chars,
--      what the router sees) leads with the strongest signal :
--      "Writes to workspace files (NEVER inline) and exercises them in
--      the Docker sandbox to verify they actually run."
--
-- P3 — New code-runner paradigm `test_in_sandbox_when_runnable` : when
--      what you wrote is executable (Python / bash / node), launch it
--      in bash_sandbox at least once to confirm it runs. If a dependency
--      is missing, install it via pip inside the sandbox and re-run.
--      Only deliver an untested script with `report_back(confidence='medium',
--      low_confidence_reason='untested because X')` and an explicit
--      mention.
--
-- Idempotent : INSERT OR IGNORE / UPDATE.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- P1 — router-side paradigm : delegate code production briefs to code-runner
-- =============================================================================

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'planning'),
    'code_runner_for_code_production_briefs',
    'Code-runner is the default for code production briefs',
'- When the brief is about WRITING, IMPLEMENTING, FIXING, DEBUGGING, or
  RUNNING code ("écris un script", "implement X", "fix this bug", "fais
  marcher ce code", "make Y work", "run this", "test this approach"),
  your default delegation target is `code-runner`.
- Do NOT answer inline with a code block. Jean-michel is a router, not a
  code generator. A code block in your final response means you failed to
  delegate. code-runner writes scripts to the workspace AND tests them in
  the Docker sandbox — that''s strictly more valuable than an untested
  inline snippet.
- For research-only briefs ("trouve-moi des libs", "comment marche X")
  delegate to `code-fetcher` instead — that''s lookup, not production.
- For mixed briefs ("trouve-moi une lib ET écris un script qui s''en sert"),
  the chain is router → code-runner, and code-runner itself delegates to
  code-fetcher for the lookup half. You only emit one delegate_to.',
    'Migration 109 : observation 2026-05-28, le router a répondu inline au
lieu de déléguer "écris-moi un script…" à code-runner.',
    0, 35, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'code_runner_for_code_production_briefs');

-- =============================================================================
-- P2 — code-runner mission rewritten : "writes to workspace AND tests in
--      sandbox" must be visible in the first 160 chars (router's view).
-- =============================================================================

UPDATE agents
SET mission =
'Writes code files to the workspace (NEVER returns code inline) AND '
|| 'exercises them in the Docker sandbox to verify they actually run. '
|| 'Handles the full write-then-run-then-iterate cycle : create or edit '
|| 'Python / bash scripts via workspace tools, execute with bash_sandbox, '
|| 'iterate on errors. When stuck on an error you can''t diagnose, OR you '
|| 'need an API example, OR you need to pick a Python package, delegate '
|| 'to `code-fetcher` for a lookup rather than guessing.',
    modified_at = datetime('now')
WHERE code = 'code-runner';

-- =============================================================================
-- P3 — code-runner paradigm : test in sandbox when runnable
-- =============================================================================

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'execution'),
    'test_in_sandbox_when_runnable',
    'Test what you write in the sandbox before reporting back',
'- For any executable artefact (Python / bash / node / SQL), after
  writing the file to the workspace, RUN IT in `bash_sandbox` at least
  once with realistic inputs to confirm it executes without error.
- If a Python dependency is missing in the sandbox, install it via
  `pip install <package>` inside the same sandbox call, then re-run.
- If the test fails, fix the script in the workspace
  (`workspace_str_replace` or `workspace_create_file`) and re-test.
  Iterate up to 3 times before escalating. Beyond 3 unsuccessful
  iterations, conclude with `report_back(confidence="low",
  low_confidence_reason="3 sandbox runs failed: <last error>")`.
- Only deliver an UNTESTED script when the user explicitly asked for
  "a draft without running it" OR when the script genuinely can''t be
  tested in the sandbox (needs external network, GPU, secrets, …). In
  that case use `report_back(confidence="medium",
  low_confidence_reason="not tested because <reason>")` and mention
  this limitation in the final summary.
- The sandbox is fast and disposable — use it. A tested script that
  the user can immediately run is strictly more valuable than an
  unvalidated one.',
    'Migration 109 : encadre l''usage du sandbox comme étape de
validation systématique, pas optionnelle.',
    0, 40, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'code-runner'),
    (SELECT id FROM paradigms WHERE code = 'test_in_sandbox_when_runnable');

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT a.code FROM agent_paradigms ap
-- JOIN agents a ON a.id=ap.agent_id
-- JOIN paradigms p ON p.id=ap.paradigm_id
-- WHERE p.code IN (
--   'code_runner_for_code_production_briefs',
--   'test_in_sandbox_when_runnable'
-- ) ORDER BY p.code, a.code;
--   -- expected :
--     code_runner_for_code_production_briefs | jean-michel
--     test_in_sandbox_when_runnable          | code-runner
--
-- SELECT mission FROM agents WHERE code='code-runner';
--   -- expected : starts with "Writes code files to the workspace (NEVER…"

-- =============================================================================
-- migrate_110_syntax_check_before_run.sql
-- =============================================================================
-- Adds a syntax-check stage to the code-runner test cycle. Refines the
-- paradigm `test_in_sandbox_when_runnable` (migration 109) WITHOUT introducing
-- a new paradigm — it's the same workflow, with a fast pre-flight gate added.
--
-- Why : running an entire script in the sandbox to discover a missing
-- bracket or a typo'd indent is wasteful. Tools like `python -m py_compile`,
-- `bash -n`, `node --check` validate syntax in milliseconds, no side
-- effects, and are already available in the sandbox.
--
-- The iteration budget (3 max) covers BOTH syntax errors and runtime
-- errors combined : the LLM cannot loop forever, syntax-fix-loop included.
--
-- Idempotent : single UPDATE, replaying it is a no-op.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

UPDATE paradigms
SET content =
'For any executable artefact you write (Python / bash / node / JS / JSON
config / YAML config), validate in TWO stages before reporting back :

1. SYNTAX CHECK FIRST — fast, no side effects, catches typos, mismatched
   brackets, broken indentation, malformed JSON. Use `bash_sandbox` to run
   one of these (pick the one matching your file type) :
     - Python  : `python -m py_compile <file>`
     - Bash    : `bash -n <file>`
     - Node/JS : `node --check <file>`
     - JSON    : `python -m json.tool <file>`
     - YAML    : `python -c "import yaml, sys; yaml.safe_load(open(sys.argv[1]))" <file>`
   If the syntax check fails, fix the file in the workspace
   (`workspace_str_replace` or rewrite via `workspace_create_file`) and
   re-check. Do NOT proceed to run a syntactically invalid script.

2. RUN with realistic inputs — only AFTER syntax check passes. Execute
   the script in `bash_sandbox`. If a Python dependency is missing,
   install it via `pip install <package>` inside the same sandbox call,
   then re-run. If the run fails for a logic reason, fix in the
   workspace and loop back to step 1 (syntax check first, since your
   fix might have introduced a new syntax error).

ITERATION BUDGET : at most 3 failed iterations total, syntax errors
and runtime errors combined. Beyond that, conclude with
`report_back(confidence="low",
low_confidence_reason="3 iterations failed: <last error message>")` —
do not loop forever, escalate to the router.

Only deliver an UNTESTED script when the user explicitly asked for "a
draft without running it" OR when the script genuinely can''t be tested
in the sandbox (needs external network, GPU, secrets, …). In that case
use `report_back(confidence="medium",
low_confidence_reason="not tested because <reason>")` and mention this
limitation in the final summary.

The sandbox is fast and disposable — use it. A tested script that the
user can immediately run is strictly more valuable than an
unvalidated one.',
    modified_at = datetime('now')
WHERE code = 'test_in_sandbox_when_runnable';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT substr(content, 1, 80) FROM paradigms WHERE code='test_in_sandbox_when_runnable';
--   -- expected starts with : "For any executable artefact you write…"
-- SELECT content LIKE '%SYNTAX CHECK FIRST%' FROM paradigms WHERE code='test_in_sandbox_when_runnable';
--   -- expected : 1

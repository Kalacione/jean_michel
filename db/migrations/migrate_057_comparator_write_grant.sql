-- MIGRATION 057 — comparator-specialist write grant + output contract
-- ====================================================================
-- comparator-specialist (id=6) already had workspace_create_file,
-- workspace_str_replace and workspace_append in agent_tools (migration 024
-- restored them, migration 056 added append) but was missing from
-- agent_workspace_grants. Result: 100 % of write calls returned
-- {"error": "Write access not granted for this agent.", "error_code":
-- "no_write_grant"} at runtime.
--
-- Decision: grant write access (a comparator naturally produces a
-- comparative table as its deliverable) and tighten the output contract:
--   - file name must include the subject + a HHMMSS timestamp (artifact
--     filename style) to avoid collisions across parallel comparisons.
--   - after writing the file, the agent MUST call report_findings with the
--     produced file path in files_produced so downstream agents can pick
--     it up.

BEGIN;

-- 1. The missing grant.
INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'comparator-specialist';

-- 2. Idempotent guarantee on the write tools (in case some install drifted).
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_create_file' FROM agents WHERE code = 'comparator-specialist';
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_str_replace' FROM agents WHERE code = 'comparator-specialist';
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_append' FROM agents WHERE code = 'comparator-specialist';

-- 3. Output contract paradigm for the comparator (category 21 = comparison).
INSERT OR IGNORE INTO paradigms (
    id, category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    123,
    21,
    'comparator_output_contract',
    'Comparator output contract',
    '- Materialise the comparative table as a workspace file via workspace_create_file. Do not paste the table into report_findings or into ask_human.
- File name pattern: comparison_<subject_slug>_<HHMMSS>.md, where <subject_slug> is a short lowercase ASCII slug describing the comparison subject (use underscores, no spaces, no accents) and <HHMMSS> is the current UTC time as six digits. Example: comparison_ai_frameworks_142507.md. The timestamp is mandatory to avoid collisions when several comparisons run in parallel or in successive turns.
- After the file is written, call report_findings exactly once with:
    - files_produced = ["<the relative path you just wrote>"]
    - summary = one paragraph describing what the table contains and its main verdict
    - confidence reflecting how solid the underlying data is
  This is what makes the file discoverable by downstream agents (document-builder, critical-thinker, jean-michel). Skipping report_findings strands the artifact.
- If you cannot complete the comparison (missing data, blocked source), still call report_findings with files_produced=[] (or the partial file path if any) and an explicit blockers list. Do not silently abort.',
    'Comparator naturally produces a structured artifact (a table). Pasting it inline truncates badly in chat UIs and hides it from sibling agents. Forcing workspace_create_file + report_findings creates a clean handoff. Timestamped name avoids the obvious collision when the router re-asks the comparator on the same subject (each run gets its own file, prior runs remain inspectable). Codified after observing a 2026-05-25 conversation where the comparator hit no_write_grant and ended up asking the human for write permission.',
    0,
    20,
    1,
    strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT id, 123 FROM agents WHERE code = 'comparator-specialist';

COMMIT;

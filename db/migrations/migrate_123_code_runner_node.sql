-- =============================================================================
-- migrate_123_code_runner_node.sql
-- =============================================================================
-- R2 (cf. DevNotes/ORCHESTRATOR/04) — "one worker = one image". Add a Node.js
-- coding worker that mirrors code-runner but runs in the node-alpine sandbox
-- (node / npm / npx) instead of py-alpine. Same coder model (qwen3-coder, no
-- thinking), same workspace tools + coding paradigms (copied from code-runner).
--
-- Prerequisite: build the image once — `./jm.sh --build-docker node-alpine`.
-- Idempotent (INSERT OR IGNORE everywhere; guarded paradigm REPLACE).
-- =============================================================================

PRAGMA foreign_keys = ON;
BEGIN TRANSACTION;

-- 1. The agent (Node sandbox image, qwen3-coder, thinking off).
INSERT OR IGNORE INTO agents
    (code, name, role, mission, thinking_mode, temperature, active, sandbox_image, model_override, created_at, modified_at)
VALUES (
    'code-runner-node', 'Code Runner (Node)', 'specialist',
    'Like code-runner but for the Node.js / JavaScript / TypeScript stack: writes JS/TS files to the workspace (NEVER inline) and runs/tests them in the Node Docker sandbox (node, npm, npx). Same write-then-run-then-iterate cycle. When stuck on an error, need an API example, or need to pick an npm package, delegate to `code-fetcher` for a lookup rather than guessing.',
    0, 0.1, 1, 'jeanmichel-sandbox:node-alpine', 'qwen3-coder:latest', datetime('now'), datetime('now')
);

-- 2. Tool grants + coding paradigms : copy code-runner's (generic, language-agnostic).
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
    SELECT (SELECT id FROM agents WHERE code = 'code-runner-node'), tool_code
    FROM agent_tools WHERE agent_id = (SELECT id FROM agents WHERE code = 'code-runner');

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
    SELECT (SELECT id FROM agents WHERE code = 'code-runner-node'), paradigm_id
    FROM agent_paradigms WHERE agent_id = (SELECT id FROM agents WHERE code = 'code-runner');

-- 3. Workspace write grant (code-runner has one).
INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
    SELECT (SELECT id FROM agents WHERE code = 'code-runner-node');

-- 4. Node sandbox binaries (the first word of every command is checked against this).
INSERT OR IGNORE INTO agent_sandbox_grants (agent_id, command)
    SELECT (SELECT id FROM agents WHERE code = 'code-runner-node'), cmd
    FROM (SELECT 'bash' AS cmd UNION ALL SELECT 'cat' UNION ALL SELECT 'echo'
          UNION ALL SELECT 'ls' UNION ALL SELECT 'node' UNION ALL SELECT 'npm'
          UNION ALL SELECT 'npx');

-- 5. Delegation : code-runner-node may delegate lookups to code-fetcher;
--    jean-michel may delegate to code-runner-node.
INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
    SELECT (SELECT id FROM agents WHERE code = 'code-runner-node'), 'code-fetcher';
INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
    SELECT (SELECT id FROM agents WHERE code = 'jean-michel'), 'code-runner-node';

-- 6. Routing : extend the code-production paradigm so the router picks the Node
--    worker for JS/TS/Node briefs (guarded → idempotent).
UPDATE paradigms
SET content = REPLACE(
        content,
        'your default delegation target is `code-runner`.',
        'your default delegation target is `code-runner` (Python / bash). For JavaScript / TypeScript / Node code, delegate to `code-runner-node` (Node sandbox) instead.'
    )
WHERE code = 'code_runner_for_code_production_briefs'
  AND content NOT LIKE '%code-runner-node%';

COMMIT;

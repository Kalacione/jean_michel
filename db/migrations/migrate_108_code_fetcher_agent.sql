-- =============================================================================
-- migrate_108_code_fetcher_agent.sql
-- =============================================================================
-- Adds the `code-fetcher` specialist : the lookup half of the fetcher/runner
-- split for code-related questions. Mirrors the news-specialist / web-search
-- pattern : surface URLs from authoritative sources, follow up with web_fetch
-- on the most relevant ones to read content in depth.
--
-- Sources :
--   - GitHub (github_search_code + github_search_repos) : code + repos
--   - Stack Overflow (stackoverflow_search) : Q&A, troubleshooting recipes
--   - PyPI (pypi_lookup) : Python package metadata
--   - web_fetch (shared) : deep-read on URLs surfaced by the above
--
-- The fetcher/runner split :
--   - `code-runner` (existing) : production + execution (write code, run it,
--     debug). Delegates UP to code-fetcher when stuck on an error or unsure
--     about an API.
--   - `code-fetcher` (this migration) : pure lookup. Returns findings, never
--     executes code, never writes scripts.
--
-- Also includes (BONUS, requested) :
--   - Paradigm `cite_sources_in_user_facing_output` on jean-michel : when
--     the answer is based on findings from specialists that surfaced URLs
--     (news, web, code, encyclopedic), the router lists the top sources at
--     the bottom of the user-facing response.
--
-- Idempotent : INSERT OR IGNORE everywhere, UPDATE conditional.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- 1. Create the code-fetcher agent
-- =============================================================================

INSERT OR IGNORE INTO agents (
    code, name, role, mission,
    thinking_mode, temperature, active,
    model_override, sandbox_image,
    created_at, modified_at
) VALUES (
    'code-fetcher',
    'Code fetcher',
    'specialist',
    'Lookup specialist for code, developer documentation, troubleshooting and '
    || 'package metadata. Sources : GitHub (github_search_code, '
    || 'github_search_repos), Stack Overflow (stackoverflow_search) and PyPI '
    || '(pypi_lookup). Pattern : surface candidate URLs from the search '
    || 'endpoints, then web_fetch on the 1-3 most relevant ones to read full '
    || 'content (file body for GitHub raw URLs, question + answers for SO). '
    || 'Synthesize the findings into a workspace markdown file with one '
    || 'section per source (cite repo + path or question title + accepted '
    || 'answer). You do NOT write or execute code yourself — that is '
    || 'code-runner''s job. If the caller needs the code applied, they will '
    || 'call code-runner after reading your findings.',
    1,      -- thinking_mode ON
    0.1,    -- low temperature (factual lookup)
    1,      -- active
    NULL,   -- default subagent model (gemma4:latest)
    NULL,
    datetime('now'), datetime('now')
);

-- =============================================================================
-- 2. Tool grants for code-fetcher
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'code-fetcher'), v
FROM (SELECT 'github_search_code' AS v
      UNION SELECT 'github_search_repos'
      UNION SELECT 'stackoverflow_search'
      UNION SELECT 'pypi_lookup'
      UNION SELECT 'web_fetch'
      UNION SELECT 'workspace_view'
      UNION SELECT 'workspace_create_file'
      UNION SELECT 'workspace_append'
      UNION SELECT 'workspace_str_replace'
      UNION SELECT 'manage_user_memory');

-- =============================================================================
-- 3. Workspace write grant
-- =============================================================================

INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'code-fetcher';

-- =============================================================================
-- 4. Attach the standard research paradigms (same set as news-specialist
--     / web-search-specialist / wikipedia-specialist)
-- =============================================================================

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'code-fetcher'), p.id
FROM paradigms p
WHERE p.code IN (
    'research_return_format',
    'source_admission_criteria',
    'subresearch_inline',
    'nested_delegation_discipline',
    'report_back_format',
    'workspace_progressive_write',
    'document_workspace_output',
    'faithful_to_sources',
    'concise_output',
    'no_permission_for_obvious_tools'
);

-- =============================================================================
-- 5. Code-fetcher specific paradigm — multi-source discipline
-- =============================================================================

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'sources'),
    'code_fetcher_multi_source',
    'Multi-source discipline for code lookups',
'- The three sources are COMPLEMENTARY, not interchangeable. Default plan
  for a broad code question :
    1. `stackoverflow_search` — what is the community consensus / known
       solution for the error or "how to do X" ?
    2. `github_search_code` — concrete examples in real repositories
       (use `language:` filter to narrow).
    3. `pypi_lookup` — IF the question involves picking or vetting a
       Python package (version, maintenance, declared deps).
- For a narrow question (e.g. "version of package X", "fix for error
  message Y"), one source may suffice — don''t over-query.
- After the search endpoints return URLs, pick the 1-3 MOST relevant
  hits and call `web_fetch(url=<raw_url or link>)` on each to read full
  content. Synthesize from the fetched content, NOT the search snippets
  (which are too short to be reliable).
- For GitHub code hits, prefer the `raw_url` field over `html_url` when
  calling web_fetch (raw URLs return plain text, no markup).
- Workspace file structure : one section per consulted source
  (`## Stack Overflow — <title>`, `## GitHub — <repo>:<path>`,
  `## PyPI — <package> <version>`), each with the URL, a faithful
  quote/summary of the relevant excerpt, and your interpretation.',
    'Migration 108 : encadre l''usage des 3 sources comme un pipeline,
pas comme un choix exclusif.',
    0, 50, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'code-fetcher'),
    (SELECT id FROM paradigms WHERE code = 'code_fetcher_multi_source');

-- =============================================================================
-- 6. Delegation targets — both jean-michel (router) AND code-runner can
--     delegate to code-fetcher.
-- =============================================================================

INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'jean-michel'), 'code-fetcher';

INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'code-runner'), 'code-fetcher';

-- =============================================================================
-- 7. Update code-runner mission to acknowledge code-fetcher
-- =============================================================================
-- Make explicit that when stuck on an error or unsure about an API,
-- the right move is to delegate the lookup, not to guess.

UPDATE agents
SET mission =
'Production specialist for code : write code files to the conversation '
|| 'workspace and execute them inside the Docker sandbox. Handles the full '
|| 'write-then-run cycle (create or edit Python / bash scripts via workspace '
|| 'tools, execute with bash_sandbox, iterate on errors). When you hit an '
|| 'error you can''t resolve from your own knowledge, OR you need an example '
|| 'of an API pattern, OR you need to pick or vet a Python package, delegate '
|| 'to `code-fetcher` for a lookup rather than guessing. Never return code '
|| 'inline — always write to workspace files.',
    modified_at = datetime('now')
WHERE code = 'code-runner';

-- =============================================================================
-- 8. New paradigm for code-runner : delegate on doubt
-- =============================================================================

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'execution'),
    'delegate_to_code_fetcher_on_doubt',
    'Delegate to code-fetcher rather than guessing',
'- When you hit an error from the sandbox that you cannot diagnose with
  certainty, do NOT guess at fixes. Delegate to `code-fetcher` with the
  exact error message and ask for the canonical fix.
- When you need to use a library or framework you''re not 100 % sure
  about (correct method signature, idiomatic pattern, recent API
  change), delegate to `code-fetcher` for a Stack Overflow + GitHub
  lookup first.
- When picking or vetting a Python package, delegate to `code-fetcher`
  for a pypi_lookup + github_search_repos check (maintenance, stars,
  deps), don''t default to the first name that comes to mind.
- After the fetcher returns, apply its findings inside the sandbox and
  iterate. Do not delegate a second time for the same error — if the
  first lookup didn''t fix it, escalate via `report_back(confidence="low",
  low_confidence_reason="...")` and let the router decide.',
    'Migration 108 : structure le pattern fetcher/runner. Sans cette
discipline, le LLM de code-runner tente des "fixes" hallucinés au lieu
de vérifier sur SO/GitHub.',
    0, 45, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'code-runner'),
    (SELECT id FROM paradigms WHERE code = 'delegate_to_code_fetcher_on_doubt');

-- =============================================================================
-- 9. BONUS — cite_sources_in_user_facing_output (router-side)
-- =============================================================================
-- Observation : the workspace files produced by specialists list sources
-- (URLs, dates) but the user-facing answer synthesized by jean-michel often
-- drops them. For information that came from external sources (news, web,
-- code, encyclopedic), citations matter. This paradigm tells the router to
-- surface them at the bottom of the answer.

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'precision'),
    'cite_sources_in_user_facing_output',
    'Cite sources in the user-facing answer',
'- When your answer is based on findings that came from specialists which
  consulted external sources (news articles, web pages, GitHub repos,
  Stack Overflow questions, Wikipedia, PyPI), include the top sources
  in your final response under a short `## Sources` heading.
- Format : one bullet per source, with title (or short label) + URL.
  Cap at 5 sources — pick the most relevant, not all of them. The full
  list lives in the workspace files (which the user can open).
- DO NOT add a Sources section for answers that did not consult external
  sources (e.g. a clock lookup, a simple calculation, a workspace
  inspection). Sources only when there are actual external sources to
  cite.
- The publication date matters for news : prefix news sources with their
  pubDate when the specialist provided it (e.g. "- [2026-05-27] Reuters
  — Ferrari announces … (https://…)").',
    'Bonus migration 108 : observation 2026-05-28, news-specialist
listait les sources dans son fichier workspace mais le routeur les
omettait dans la réponse user-facing.',
    0, 25, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'cite_sources_in_user_facing_output');

COMMIT;

-- =============================================================================
-- VALIDATION post-migration
-- =============================================================================
-- SELECT code, role, model_override FROM agents WHERE code = 'code-fetcher';
--   -- expected : code-fetcher | specialist | NULL
--
-- SELECT tool_code FROM agent_tools WHERE agent_id =
--   (SELECT id FROM agents WHERE code = 'code-fetcher') ORDER BY tool_code;
--   -- expected : github_search_code, github_search_repos, manage_user_memory,
--                 pypi_lookup, stackoverflow_search, web_fetch,
--                 workspace_append, workspace_create_file,
--                 workspace_str_replace, workspace_view
--
-- SELECT target_code FROM agent_delegation_targets WHERE agent_id =
--   (SELECT id FROM agents WHERE code = 'code-runner');
--   -- expected : code-fetcher
--
-- SELECT a.code FROM agent_paradigms ap
-- JOIN agents a ON a.id = ap.agent_id
-- JOIN paradigms p ON p.id = ap.paradigm_id
-- WHERE p.code = 'cite_sources_in_user_facing_output';
--   -- expected : jean-michel

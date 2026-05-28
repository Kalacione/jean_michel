-- =============================================================================
-- migrate_106_news_specialist.sql
-- =============================================================================
-- Adds the `news-specialist` agent and the two news tools backed by
-- NewsData.io (https://newsdata.io) :
--
--   - news_latest  : breaking news from the past 48 h
--   - news_archive : search over a date range
--
-- API key sourced from env var `NEWSDATA_API_KEY` (free tier : 200 credits/day,
-- 12 h delay on `latest`, archives available within plan limits).
--
-- Mirrors the wikipedia-specialist / web-search-specialist pattern : a
-- dedicated agent owning the source-of-truth, with the standard research
-- paradigm set (source admission, return format, progressive write, …).
--
-- Idempotent : INSERT OR IGNORE everywhere.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- 1. Create the news-specialist agent
-- =============================================================================

INSERT OR IGNORE INTO agents (
    code, name, role, mission,
    thinking_mode, temperature, active,
    model_override, sandbox_image,
    created_at, modified_at
) VALUES (
    'news-specialist',
    'News specialist',
    'specialist',
    'Retrieve current news (news_latest, past 48 h) or historical news '
    || '(news_archive, by date range) from the NewsData.io API. Filter by '
    || 'keyword, language, country, category or source domain. Synthesize '
    || 'the findings into a workspace markdown file (one section per article, '
    || 'with title, source, date, link, and a faithful one-line summary). '
    || 'Never fabricate, never extrapolate beyond what the article descriptions '
    || 'contain. Free-tier latest endpoint has a ~12 h delay : for true '
    || 'real-time, the router should pick web-search-specialist instead.',
    1,      -- thinking_mode ON
    0.1,    -- low temperature (factual retrieval)
    1,      -- active
    NULL,   -- default subagent model (gemma4:latest)
    NULL,
    datetime('now'), datetime('now')
);

-- =============================================================================
-- 2. Tool grants
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'news-specialist'), v
FROM (SELECT 'news_latest' AS v
      UNION SELECT 'news_archive'
      UNION SELECT 'workspace_view'
      UNION SELECT 'workspace_create_file'
      UNION SELECT 'workspace_append'
      UNION SELECT 'workspace_str_replace'
      UNION SELECT 'manage_user_memory');

-- =============================================================================
-- 3. Workspace write grant
-- =============================================================================

INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'news-specialist';

-- =============================================================================
-- 4. Attach the standard research paradigms
-- =============================================================================
-- These are the same paradigms that wikipedia-specialist / web-search-specialist
-- carry : how to format the return, what counts as an admissible source,
-- when to nest delegations, how to write progressively to the workspace.

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'news-specialist'), p.id
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
-- 5. News-specific paradigm — freshness discipline
-- =============================================================================
-- Reminds the LLM about the 12 h free-tier delay, the 48 h latest window,
-- and the need to surface the publication date on every retrieved item.

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'sources'),
    'news_freshness_discipline',
    'News freshness discipline',
'- Always surface the `pubDate` of each article in your workspace output and
  in your `report_back` summary. News value is time-sensitive ; an undated
  finding is unusable.
- The `news_latest` endpoint covers the past 48 h. On the free tier, articles
  have a ~12 h delay — if the human is asking about something that JUST
  happened, prefer `web_search` (delegate back to web-search-specialist) and
  state the limitation explicitly.
- `news_archive` is for dated questions ("what was reported about X in
  March 2025"). Provide `from_date` AND/OR `to_date` (YYYY-MM-DD) to bound
  the search ; without dates the archive defaults to recent days.
- One credit = up to 10 articles. If totalResults exceeds 10 and the human
  asked for breadth, paginate via `page=<nextPage>` rather than re-querying
  with a narrower term.
- Never collapse multiple distinct articles into a single bullet — each
  retained article gets its own entry in the workspace file with title +
  source + date + link.',
    'Migration 106 : encadre l''usage des deux endpoints NewsData.io, le
delay du free tier, et la nécessité de dater les findings.',
    0, 50, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'news-specialist'),
    (SELECT id FROM paradigms WHERE code = 'news_freshness_discipline');

-- =============================================================================
-- 6. Delegation target — jean-michel can route to news-specialist
-- =============================================================================

INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'jean-michel'), 'news-specialist';

-- =============================================================================
-- 7. Update strategist's list of downstream specialists
-- =============================================================================
-- The strategist paradigm names example targets ; news-specialist is a
-- legitimate one for inventory briefs that span current events. We leave
-- the paradigm content as-is (it says "web-search-specialist,
-- wikipedia-specialist, …" — the trailing "…" already implies extension).
-- No DB update needed here.

COMMIT;

-- =============================================================================
-- VALIDATION post-migration
-- =============================================================================
-- SELECT code, role, model_override FROM agents WHERE code = 'news-specialist';
--   -- expected : news-specialist | specialist | NULL
--
-- SELECT tool_code FROM agent_tools WHERE agent_id =
--   (SELECT id FROM agents WHERE code = 'news-specialist') ORDER BY tool_code;
--   -- expected : manage_user_memory, news_archive, news_latest,
--                 workspace_append, workspace_create_file,
--                 workspace_str_replace, workspace_view
--
-- SELECT target_code FROM agent_delegation_targets
-- WHERE agent_id = (SELECT id FROM agents WHERE code = 'jean-michel')
--   AND target_code = 'news-specialist';
--   -- expected : 1 row

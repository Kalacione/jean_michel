-- =============================================================================
-- migrate_107_news_routing_and_web_fetch.sql
-- =============================================================================
-- Three-in-one fix following the routing observation of 2026-05-28 (user
-- asked "Quelles sont les actualites recentes pour Ferrari ?" — jean-michel
-- routed to web-search-specialist instead of news-specialist).
--
-- P1 — Fix routing toward news-specialist :
--   1. Remove the "news" mention from web-search-specialist mission so it
--      no longer competes with news-specialist on the same keyword.
--   2. Rewrite news-specialist mission to lead with its value (curated press,
--      structured metadata) instead of its constraints (12 h delay).
--   3. Add a router-side paradigm `news_first_for_news_briefs` telling
--      jean-michel that "actualités / news / latest news" briefs go to
--      news-specialist by default.
--   4. Soften `news_freshness_discipline` so it no longer pushes the news
--      specialist to abdicate to web-search ; the paradigm now describes
--      the news_latest + web_fetch pattern instead.
--
-- P2 — web_fetch tool grants :
--   - Grant `web_fetch` to news-specialist (read article body after
--     news_latest) and to web-search-specialist (deepen search hits).
--   - The Python tool itself is registered in src/jeanmichel/tools/__init__.py
--     and lives in src/jeanmichel/tools/web_fetch.py. readability-lxml is
--     a new pyproject dependency.
--
-- Idempotent : UPDATE, INSERT OR IGNORE.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- P1.1 — Mission web-search-specialist : retirer "news" pour éviter le
--         chevauchement sémantique avec news-specialist.
-- =============================================================================

UPDATE agents
SET mission =
'Search the web for current information and facts not covered by Wikipedia '
|| 'or by a domain-specific specialist. Use web_search to find candidate '
|| 'sources, then web_fetch on the most relevant 1-3 URLs to read the full '
|| 'article text rather than relying on snippets. Summarise findings clearly '
|| 'with source URLs. Never fabricate ; never invent quotes. For pure news / '
|| 'press articles, defer to news-specialist instead.',
    modified_at = datetime('now')
WHERE code = 'web-search-specialist';

-- =============================================================================
-- P1.2 — Mission news-specialist : mettre en avant la valeur ajoutée.
-- =============================================================================

UPDATE agents
SET mission =
'Primary owner of press coverage and news articles. Use `news_latest` for '
|| 'recent items (past 48 h window) or `news_archive` for dated questions '
|| '(date range). Filter by keyword, language, country, category or source '
|| 'domain. Each call returns up to 10 articles with title, source, date '
|| 'and URL. Follow up with `web_fetch` on the most relevant article links '
|| 'to read the full text — that lets one news-API credit cover multiple '
|| 'deep reads. Synthesize into a workspace markdown file with title + '
|| 'source + pubDate + URL per entry. Never fabricate, never extrapolate '
|| 'beyond what articles say.',
    modified_at = datetime('now')
WHERE code = 'news-specialist';

-- =============================================================================
-- P1.3 — New paradigm `news_first_for_news_briefs` côté jean-michel.
-- =============================================================================

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'planning'),
    'news_first_for_news_briefs',
    'News-specialist is the default for news briefs',
'- When the brief is about current events, press coverage, or recent
  developments ("actualités", "news", "dernières nouvelles", "what is
  happening with X", "que se passe-t-il", "what was reported about"),
  your default delegation target is `news-specialist`.
- Do NOT default to `web-search-specialist` for news questions. Web
  search returns snippet-level previews from a heterogeneous mix of
  sources (forums, blogs, marketing pages) ; news-specialist returns
  curated press articles with explicit dates, sources, and metadata —
  exactly what news questions need.
- Use `web-search-specialist` for news ONLY in the narrow case where
  the requested freshness is critical (events from the last hour) and
  news-specialist explicitly returned an empty result. For everything
  else, news-specialist first.',
    'Migration 107 : observation 2026-05-28, le router est allé sur
web-search au lieu de news-specialist pour "actualités Ferrari".',
    0, 35, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'news_first_for_news_briefs');

-- =============================================================================
-- P1.4 — Rewrite `news_freshness_discipline` to describe the
--         news_latest + web_fetch pattern instead of pushing to web-search.
-- =============================================================================

UPDATE paradigms
SET content =
'- Always surface the `pubDate` of each article in your workspace output and
  in your `report_back` summary. News value is time-sensitive ; an undated
  finding is unusable.
- `news_latest` covers the past 48 h ; `news_archive` covers dated ranges
  (provide `from_date` / `to_date` in YYYY-MM-DD).
- Each NewsData.io credit returns up to 10 articles with title + URL +
  short description. To read the FULL text of an article without burning
  another credit, call `web_fetch(url=<article.link>)` on the article URL —
  this is the canonical pattern for deep reading. Pick the 1-3 most
  relevant articles to fetch, not all 10.
- One workspace file entry per retained article : title, source, date,
  URL, and a faithful one-line summary drawn from the fetched content
  (or the description if web_fetch failed). Never collapse distinct
  articles into a single bullet.
- A free-tier ~12 h delay applies to `news_latest`. This is acceptable
  for "what is happening this week / today" briefs. Only escalate to a
  `delegate_to(web-search-specialist, …)` for sub-hour freshness and
  state the limitation explicitly to the human.',
    modified_at = datetime('now')
WHERE code = 'news_freshness_discipline';

-- =============================================================================
-- P2 — Grant web_fetch to news-specialist and web-search-specialist.
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'news-specialist'), 'web_fetch';

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'web-search-specialist'), 'web_fetch';

COMMIT;

-- =============================================================================
-- VALIDATION post-migration
-- =============================================================================
-- SELECT code, mission FROM agents WHERE code IN ('news-specialist','web-search-specialist');
--   -- check the new wording (no "news" in web-search, value-first in news)
--
-- SELECT a.code FROM agent_paradigms ap
-- JOIN agents a ON a.id=ap.agent_id
-- JOIN paradigms p ON p.id=ap.paradigm_id
-- WHERE p.code='news_first_for_news_briefs';
--   -- attendu : jean-michel
--
-- SELECT tool_code FROM agent_tools
-- WHERE agent_id IN (SELECT id FROM agents WHERE code IN ('news-specialist','web-search-specialist'))
--   AND tool_code='web_fetch';
--   -- attendu : 2 rows

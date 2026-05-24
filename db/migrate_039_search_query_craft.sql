-- MIGRATION 039 — searxng_query_craft: SearXNG syntax + query formulation rules
-- ==============================================================================
-- Decoupled from search_then_synthesize (which is engine-agnostic).
-- Assigned only to web-search-specialist (agent_id=13, category web_search id=33).
-- Root cause of 174-call sessions: no knowledge of SearXNG operators + synonym
-- reformulation loop instead of angle-change strategy.

-- Restore search_then_synthesize to its engine-agnostic state
UPDATE paradigms
SET content = '- Limit web_search calls to 5 per request maximum. After 5 searches, STOP and write what you have — even if incomplete.
  Do not keep searching to reach a quantity target. Accuracy over completeness.
  Never invent, guess, or fabricate sources, URLs, or facts to fill gaps.
  If you genuinely cannot find more after 4-5 searches, that absence is itself a valid result.
- Each search should cover a distinct sub-topic. Do not repeat similar queries.
- If a result URL points to a PDF or requires login, skip it and note it as inaccessible.
- After gathering enough results, write a compact structured summary to the workspace via workspace_create_file (file naming: web-search-specialist_<topic-slug>.md). Include source URLs inline. Do NOT dump raw search result JSON.
- Return the workspace file path in your return_to_user answer so the calling agent can reference it in subsequent briefings.',
    modified_at = datetime('now')
WHERE code = 'search_then_synthesize';

-- Insert searxng_query_craft (web-search-specialist only)
INSERT INTO paradigms (category_id, code, title, content, rationale,
                       is_global, order_priority, active, created_at, modified_at)
VALUES (33, 'searxng_query_craft', 'SearXNG Query Craft',
        '- The web_search tool uses SearXNG as its engine. Use SearXNG syntax to improve precision:
    !<engine>   select a specific engine or category
      !wp <query>        → Wikipedia only
      !ddg <query>       → DuckDuckGo
      !map <query>       → map category
      !images <query>    → image search
      Chainable: !wp !ddg <query> searches both simultaneously
    :<lang>     force result language
      :en <query>        → English results only
      :fr !wp <query>    → French Wikipedia
    !! <query>  redirect to first result (use only when the URL itself is the goal)

- Standard operators work via SearXNG''s underlying engines (Google, Bing, etc.):
    "phrase"             exact phrase match: "programmatic access" arXiv
    site:<domain>        restrict to a site: site:arxiv.org api
    -word                exclude a term: python API -tutorial

- Query formulation rules — violations cause loops:
    Keep queries short (2-5 words). Beyond 8 words, engines drop trailing terms.
      BAD:  ''programmatically accessible encyclopedic information sources API dump RSS''
      GOOD: ''encyclopedic data API''
    One query = one domain. Never try to cover multiple topics in a single query.
    Do not rephrase with synonyms. If a query fails, change the angle (different keyword,
    use site:, switch engine with !, change language) — never produce surface variants.
      BAD:  ''arXiv API programmatic access'' → ''arXiv API data dumps RSS'' → ''arXiv API automated retrieval''
      GOOD: ''arXiv API documentation'' → if insufficient → ''site:arxiv.org api'' or ''!wp arXiv''
    If 2 reformulations of the same topic yield nothing useful, that topic has no accessible
    web result — record the absence and move on.',
        'Teaches web-search-specialist SearXNG syntax and short-query discipline. Decoupled so engine can be swapped without touching search_then_synthesize.',
        0, 80, 1, datetime('now'), datetime('now'))
ON CONFLICT(code) DO UPDATE SET
    content = excluded.content,
    title   = excluded.title,
    modified_at = datetime('now');

-- Assign to web-search-specialist only
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT 13, id FROM paradigms WHERE code = 'searxng_query_craft';

-- MIGRATION 040 — searxng_query_craft: RECON-inspired stop conditions
-- ====================================================================
-- Adds 4 early-stop rules derived from the RECON prompt pattern:
--   keyword overlap guard, filter bubble detection, early completion,
--   dead angle pivot. Avoids the hallucination-prone IG score —
--   all rules are qualitative and self-verifiable by the LLM.
-- Also adds: mandatory thought structure before each search.

UPDATE paradigms
SET content = '- The web_search tool uses SearXNG as its engine. Use SearXNG syntax to improve precision:
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
    web result — record the absence and move on.
Stop conditions — stop early if any of these is true, do not wait for the budget of 5:
- Keyword overlap: your next query would reuse more than half the words of a previous query.
  If you cannot form a semantically different query, the angle is exhausted — mark it [DEAD END] and stop.
- Filter bubble: the same domain (e.g. arxiv.org, github.com) appears in more than 3 consecutive
  results. Break out: exclude it with -site:<domain> or switch engine with !ddg, !wp.
- Early completion: you have a clear, direct answer confirmed by 2 independent sources.
  STOP. Do not continue to 5 searches out of principle.
- Dead angle: 2 reformulations of the same sub-topic returned nothing new.
  Mark it [DEAD END] in your thought, pivot to a completely different angle — not a synonym.

Before each search, state in your thought:
  - What new angle this query covers (vs previous queries)
  - Whether any stop condition above applies',
    modified_at = datetime('now')
WHERE code = 'searxng_query_craft';

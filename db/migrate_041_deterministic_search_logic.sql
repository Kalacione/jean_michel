-- MIGRATION 041 — searxng_query_craft: revert RECON, replace with deterministic logic
-- =====================================================================================
-- Migration 040 imported RECON stop conditions (keyword overlap %, filter bubble, etc.).
-- These are still partially subjective. This migration replaces them with 3 fully
-- deterministic mechanisms that a LLM can apply without estimation or scoring:
--
--   1. Fact register (triplets): [Entity/Action/Value] — binary: new fact or FAILURE
--   2. Two-witness rule: a fact is CONFIRMED only when 2 different domains confirm it
--   3. Wall detection: same URLs or citation loop → immediate STOP
--
-- Rationale: a journalist or detective does not compute percentages.
-- They cross-check facts and stop when they hit a wall. These rules are checkable
-- against the conversation context (URLs visible in tool_responses) without hallucination.

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

- Deterministic stop logic — do not estimate or score, only check facts:

  1. Fact register (triplets method)
     After reading each result, extract concrete triplets: [Entity / Action / Value or Date].
     Example: [arXiv / exposes REST API / returns JSON metadata]
     A search that adds ZERO new triplets to your fact register is a FAILURE.
     Write the new triplets in your thought before the next search.

  2. Two-witness rule
     A fact is CONFIRMED only when it appears in results from 2 different domains
     (e.g. arxiv.org and docs.python.org — not arxiv.org cited twice).
     Once all required facts for this step are CONFIRMED, you MUST stop. No further searches.

  3. Wall detection (loop guard)
     You have hit a wall if either of these is true — STOP immediately and write what you have:
     - Same URLs appear in this result as in a previous result (index duplicate).
     - A source you found cites another source you already read (citation loop).
     Action: do not try to break through. Write the synthesis with what you have.

- Before each search, state in your thought:
    - New triplets extracted from the last result (or [NONE] → this was a FAILURE)
    - Which required facts are still unconfirmed
    - Wall detection check: any URL or source overlap with previous results?
    - Decision: CONTINUE or STOP (with reason)',
    modified_at = datetime('now')
WHERE code = 'searxng_query_craft';

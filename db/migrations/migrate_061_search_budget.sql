-- MIGRATION 061 — search budget: structural gate + paradigm update

-- search_then_synthesize: replace the text-only "5 searches max" directive with
-- a reference to the structural gate enforced by the orchestrator.
-- The structural gate restricts to report_findings once MAX_SEARCH_CALLS_PER_REQUEST
-- is reached (default 10), so the text limit is now advisory.
UPDATE paradigms SET
  content = '- Each search should target a distinct sub-topic or angle. Do not repeat similar queries — vary the keyword, the domain, or the tool.
- After 2-3 productive searches, persist findings to the workspace via workspace_create_file. Do NOT batch all writes to the end: your context can be cut if the step budget expires. Append progressively with workspace_append.
- A structural search budget is enforced by the orchestrator (default: 10 distinct search calls). Once the budget is reached, only report_findings is available — no further searches are possible. Plan your searches accordingly: 3-5 targeted queries is typically sufficient for a well-scoped briefing. Do not burn budget on reformulations of the same query.
- Each workspace entry must include: source URL, relevant claim, confidence.
- If a result URL points to a PDF or requires login, skip it and note it as inaccessible.
- Never invent, guess, or fabricate sources, URLs, or facts to fill gaps.
- When done (or budget nearly exhausted), call report_findings(summary, files_produced, confidence). The summary is 1-3 sentences pointing at what is in the workspace file.',
  modified_at = datetime('now')
WHERE code = 'search_then_synthesize';

-- wikipedia_search_strategy: add a note about the structural search budget.
UPDATE paradigms SET
  content = '- Wikipedia uses the English edition by default. ALL search queries MUST be in English,
  regardless of the detected human language or any language directive elsewhere in this
  prompt. This rule takes precedence over all other language instructions.
- If the entity name is not in English, translate it to its English equivalent before
  forming the search query (e.g. French "morse" → "walrus", "dauphin" → "dolphin",
  "rhinocéros" → "rhinoceros", "caleçon" → "boxer shorts", "slip" → "briefs").
- Start with the most specific search terms matching the question.
- From the search results, choose the most directly relevant article title.
- Prefer dedicated articles (e.g. "Leaning Tower of Pisa") over broad ones (e.g. "Pisa").
- If wikipedia_get_page returns a disambiguation error, pick the most relevant option from the list and retry.
- If the first search yields no useful results, reformulate with alternative keywords.
- A structural search budget is enforced by the orchestrator (default: 10 distinct search calls across wikipedia_search, wikipedia_get_page, wikipedia_fetch). Once reached, only report_findings is available. Plan accordingly: 4-6 targeted searches is typically sufficient.',
  modified_at = datetime('now')
WHERE code = 'wikipedia_search_strategy';

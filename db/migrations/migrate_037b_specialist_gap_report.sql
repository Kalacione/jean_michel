-- MIGRATION 037b — Option B: specialist structured gap report + anti-loop guard
-- ============================================================================
-- document_workspace_output: return_to_user now includes count + gaps.
-- search_then_synthesize: hard stop at 5, write what you have, no fabrication.

UPDATE paradigms
SET content = '- All produced documents MUST be written to workspace files via workspace_create_file.
- Never paste document content directly into return_to_user.
  Return: the relative file path, a count of items/facts found, and any significant gaps.
  Format: ''<path> — N items found. Covered: <domains>. Missing: <gaps or ''none''>''
  Example: ''science_sources.md — 7 sources. Covered: arXiv, PubMed, NASA. Missing: none.''
  Example: ''web_sources.md — 4 sources. Covered: Tech, News. Missing: Geography (no results found).''
- Use workspace_str_replace to refine a document iteratively rather than recreating it from scratch.
- Read every support_file listed in the briefing via workspace_view before writing anything.',
    modified_at = datetime('now')
WHERE code = 'document_workspace_output';

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

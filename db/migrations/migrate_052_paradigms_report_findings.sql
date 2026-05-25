-- Migration 052: aligner les paradigmes specialist sur report_findings
--
-- Cause: 5 paradigmes attachés à des specialists parlaient encore de
-- return_to_user comme verbe de sortie, alors que l'orchestrateur exige
-- report_findings pour ce rôle. Le LLM recevait un contrat contradictoire :
-- prompt système → "appelle return_to_user" ; orchestrateur → "interdit, appelle
-- report_findings". Résultat : non-convergence systématique, step_budget
-- exhausted, boucles.
--
-- Cette migration réécrit les paradigmes pour qu'ils référencent report_findings
-- partout, et renforce la discipline "workspace as memory" : écrire avant de
-- reporter.

BEGIN;

UPDATE paradigms SET
  content = '- All produced documents MUST be written to workspace files via workspace_create_file.
- Never paste document content directly into report_findings.
- Your report_findings call returns: summary (1-3 sentences, the headline finding), files_produced (the workspace files you wrote — the parent will read them), confidence.
- Use workspace_str_replace to refine a document iteratively rather than recreating it from scratch.
- Read every support_file listed in the briefing via workspace_view before writing anything.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'document_workspace_output';

UPDATE paradigms SET
  content = '- Before any write operation, state in your thought channel what will change: file path, operation type, expected outcome.
- Include the list of files written in report_findings.files_produced so the parent has a clear audit trail.
- If the operation affects multiple files, enumerate them all before proceeding.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'report_before_acting';

UPDATE paradigms SET
  content = '- After fetching and extracting Wikipedia content, write it to a workspace file (workspace_create_file) then call report_findings with files_produced pointing to that file. Do not delegate to summarizer or document-builder to re-process what you already extracted.
- You are the extraction specialist. Your workspace output IS the deliverable.
- Only delegate if explicitly asked to produce a formatted workspace document by another specialist.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'wikipedia_deliver_directly';

UPDATE paradigms SET
  content = '- Limit web_search calls to 5 per request maximum. After 5 searches, STOP and write what you have — even if incomplete. Do not keep searching to reach a quantity target. Accuracy over completeness.
- Each search should cover a distinct sub-topic. Do not repeat similar queries — vary the angle (different keyword, different domain).
- After 2-3 useful searches, persist what you have to the workspace via workspace_create_file (file naming: <agent-code>_<topic-slug>.md). DO NOT wait until the end — your context can be lost if the step budget runs out. Append progressively.
- Each workspace entry must include: the source URL, the relevant claim, your confidence in it.
- If a result URL points to a PDF or requires login, skip it and note it as inaccessible.
- Never invent, guess, or fabricate sources, URLs, or facts to fill gaps.
- When you are done, call report_findings(summary, files_produced, confidence). The summary is 1-3 sentences pointing at what is in the workspace file. The full findings are in the file, not in the summary.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'search_then_synthesize';

UPDATE paradigms SET
  content = '- The full findings (sources, quotes, claims, citations) go in a workspace file. report_findings is a thin pointer with: summary (headline finding, 1-3 sentences), files_produced (the workspace files), confidence (low | medium | high), optional sub_questions, optional blockers.
- The workspace file itself should be structured. Suggested sections:
  ## Established
    Bullet list: each confirmed fact with source URL.
  ## Not found / Contradicted
    What was searched but not confirmed; sources that disagree.
  ## Open questions
    Things worth a follow-up delegation.
- Never paste raw JSON, full article excerpts, or long passages into report_findings.summary — those belong in the workspace file.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'research_return_format';

COMMIT;

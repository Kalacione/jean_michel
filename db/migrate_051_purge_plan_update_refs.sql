-- Migration 051: purge toutes les références résiduelles à plan_update
-- Paradigmes affectés : plan_before_complex_action, research_phase_routing, subresearch_inline

BEGIN;

UPDATE paradigms SET
  content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, think through your research strategy before delegating: which agents cover which aspects, what each should deliver.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, reconsider before delegating.
- After each delegation completes, evaluate the result. If there is a gap: follow up with a targeted sub-delegation, or proceed to synthesis if the gap is acceptable.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'plan_before_complex_action';

UPDATE paradigms SET
  content = '- For deep_research tasks, delegate in a logical order: gather information first (web-search, wikipedia), then evaluate critically (critical-thinker), then build a document if needed (document-builder).
- This is a guideline, not a hard constraint. Adapt the order based on what each agent returns.
- Each specialist completes with report_findings. Read the report and decide: follow up with another delegation, or synthesize for the user.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'research_phase_routing';

UPDATE paradigms SET
  content = '- When a result reveals a disambiguation (Wikipedia disambiguation page, multiple homonyms, ambiguous link), DO NOT abort or escalate. Pick the most relevant candidate(s) and continue the search inline within the same request.
- Limit: at most 3 inline sub-searches per delegation. Beyond that, complete with report_findings and let the orchestrator route via a fresh delegation.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'subresearch_inline';

COMMIT;

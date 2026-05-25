-- Migration 056: generalize source_admission_criteria
--
-- 055 was too specific: it baked in domain-specific examples ("Scientific
-- Databases", "Public APIs") and a blacklist of brand names that risk
-- backfiring on legitimate tasks (e.g. when the user explicitly asks for
-- categories rather than instances).
--
-- This migration replaces the content with general principles that target the
-- underlying failure modes without coupling to a specific prompt or domain.

BEGIN;

UPDATE paradigms SET
  content = '- When the briefing asks for a list of items (sources, tools, papers, products…), each entry must be a SPECIFIC, NAMED instance that the user could identify and use directly. Do not use category labels as entries unless the briefing explicitly asks for categories.
- Each listed entry must be grounded in a tool_response from THIS research session. If you cannot point to the search result or page where the entry was surfaced, do not list it. Pre-existing knowledge about a name is not evidence that the name corresponds to what you claim about it.
- The description column for each entry must add information that distinguishes THIS entry from the others (its angle, format, access mode, license, scope). Generic descriptions that merely paraphrase the entry name are a red flag — they signal you cannot actually characterize what makes the entry relevant.
- When unsure whether an entry truly matches the brief''s constraints (e.g. public access, free tier, documented API, current availability), EXCLUDE it. A short, accurate list always beats a longer list padded with entries you cannot defend.
- Brand recognition is not verification. Many well-known brands no longer offer what they once did, or offer it only under commercial contract. Always check the tool_response evidence, not your memory of the brand.',
  modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'source_admission_criteria';

COMMIT;

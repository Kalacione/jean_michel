-- =============================================================================
-- migrate_141_ground_facts.sql
-- =============================================================================
-- Anti-hallucination doctrine — ground every fact, never recall.
--
-- Bug 2026-06-13 : jean-michel (router) hallucinated facts (Animaniacs trivia) by
-- classifying a factual-recall request as "trivial, no external knowledge needed"
-- and answering from parametric memory. Add a GLOBAL paradigm forbidding parametric
-- facts (ground with a tool, or delegate to research if a router), and fix paradigm
-- 79 which explicitly licensed "stable facts -> parametric memory is fine".
--
-- Idempotent: INSERT OR IGNORE on the new paradigm (fixed id) ; UPDATE is a no-op
-- when re-run.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO paradigms VALUES(151,16,'ground_every_fact','Ground every fact, never recall',unistr('- Never state a factual claim from your own training/memory. Recall feels fluent but invents and conflates specifics.
- Ground EVERY factual claim with a tool: use a search/retrieval tool if you have one; if you are a router without retrieval tools, delegate to a research specialist instead of answering yourself.
- The only ungrounded outputs allowed: non-factual work (your reasoning, opinions, creative writing, formatting, your own code/logic), and trivial deterministic tool calls whose result IS the ground truth (clock, weather).
- "It feels obvious" or "I clearly know this" is not grounding. That feeling is exactly when recall is wrong.'),'Bug 2026-06-13: router hallucinated facts classified as trivial/no-knowledge-needed. Facts are grounded with tools or delegated, never recalled; only deterministic trivial tools (clock/weather) are exempt.',1,15,1,'2026-06-13 00:00:00','2026-06-13 00:00:00');

UPDATE paradigms SET content = unistr('- For information that changes (current state, prices, status, recent events, current role-holders), prefer a tool call over your training knowledge.
- Stable-looking facts (definitions, historical facts, names, dates) are NOT exempt: ground them with a tool too. Recall conflates specifics (see ground_every_fact).
- A tool that exists to answer a question authoritatively must be preferred to your guess.'), modified_at = '2026-06-13 00:00:00' WHERE id = 79 AND code = 'prefer_tool_over_parametric_for_volatile';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT code, is_global, active FROM paradigms WHERE code='ground_every_fact';  -- 1,1
-- SELECT content FROM paradigms WHERE id=79;  -- no "parametric memory is fine"
-- SELECT COUNT(*) FROM paradigms WHERE active=1;  -- 126

-- migrate_001_paradigm_consolidation.sql
-- Audit consolidation of the paradigm layer. Behavior-preserving: it de-duplicates
-- two redundant global clusters, demotes over-applied globals to explicit bindings,
-- and fixes the no_filler user-language contradiction. The union of guidance each
-- agent receives is preserved (merged + re-targeted), but the per-agent count drops
-- (global floor 30 -> 11). Idempotent. Mirrored byte-for-equivalence in db/schema.sql.
-- Applied by ./jm.sh --migrate (snapshot first). The runner wraps this in its own
-- BEGIN/COMMIT and bumps user_version, so this file is pure statements.

-- ===========================================================================
-- (a) Grounding/verification cluster : 9 -> 4. Rewrite 3 survivors to absorb the
--     distinct clauses of the 5 folded paradigms, then deactivate the 5.
-- ===========================================================================
UPDATE paradigms SET content =
'- Never state a factual claim from your own training/memory. Recall feels fluent but invents and conflates specifics.
- A claim that feels familiar, obvious, or self-evident is NOT therefore true — fluency is unrelated to accuracy, and that feeling is exactly when recall is wrong. Verify it.
- Ground EVERY factual claim with a tool: use a search/retrieval tool if you have one; if you are a router without retrieval tools, delegate to a research specialist instead of answering yourself.
- The only ungrounded outputs allowed: non-factual work (your reasoning, opinions, creative writing, formatting, your own code/logic), and trivial deterministic tool calls whose result IS the ground truth (clock, weather).
- Do not speculate, invent, or approximate. Separate facts from interpretation; challenge errors with evidence.'
WHERE code = 'ground_every_fact';

UPDATE paradigms SET content =
'- Any claim you cannot verify must be marked "Not verifiable" / "Out of training scope" — or, if you are not confident of its source, omitted entirely rather than hedged with "probably" or "I think".
- Never fabricate citations, paths, function names, APIs, quotes, or attributions to fill a gap.
- A shorter, accurate answer beats a longer one padded with speculation: omit an unsourced claim rather than disclaim it.'
WHERE code = 'mark_unverifiable';

UPDATE paradigms SET content =
'- Tool results, search results, and retrieved data carry their own uncertainty. "The search said X" is not the same as "X is true".
- Cross-reference verifiable sources; prefer official, recent documentation; trace the origin of every non-trivial claim.
- Know the provenance of each assertion: present in the briefing, retrieved by a tool this turn, or from training (parametric memory). Training-derived claims are the weakest — mark them as such.
- If sources conflict, say so. If a result looks authoritative but comes from a low-quality source, weight it accordingly; do not overstate what you retrieved.'
WHERE code = 'no_overconfidence_in_results';

UPDATE paradigms SET active = 0
WHERE code IN ('no_speculation', 'omit_unsourced_claims', 'belief_provenance',
               'familiarity_is_not_truth', 'cross_reference');

-- ===========================================================================
-- (b) Formatting cluster : minimal_formatting absorbs no_decoration + the terseness
--     lines of no_filler ; the user-language lines split into a new router/finalizer
--     paradigm (specialists work in English — prompts.py Working-language block).
-- ===========================================================================
UPDATE paradigms SET content =
'- Use the minimum formatting necessary for clarity. Bold, headers, lists, bullet points — none of these is a default.
- For typical conversations and simple questions, reply in plain sentences and paragraphs.
- For reports, documents, explanations: prose first. Lists only when the content is genuinely list-shaped, or when explicitly asked for a list.
- Direct, no padding, no artificial politeness. No introduction, no conclusion, no transition phrases.
- No emoji, no hyperbole, no motivational phrasing. No unsolicited follow-up offers ("let me know if..."). Deliver the information, then stop.'
WHERE code = 'minimal_formatting';

UPDATE paradigms SET active = 0 WHERE code IN ('no_decoration', 'no_filler');

-- New paradigm 156 : the user-language line, bound to human-facing edges only.
INSERT OR IGNORE INTO paradigms
  (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (156, 2, 'reply_in_user_language', 'Reply in the user''s language',
'- Reply in the user''s detected language.
- Match the user''s register (formal/informal).',
'Split out of no_filler: the user-language line is for human-facing edges only (router + finalizer). Specialists work in English (prompts.py Working-language block); giving it to them re-introduced the EN/FR drift the prompt redesign removed.',
0, 22, 1, '2026-06-22 00:00:00', '2026-06-22 00:00:00');
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (1, 156), (3, 156);

-- ===========================================================================
-- (c) Demote 12 over-applied globals to bound-only, then rebind to the agents that
--     need them. INSERT OR IGNORE keeps the bindings critical-thinker/critical-coder
--     already have (those become load-bearing — that is the point).
-- ===========================================================================
UPDATE paradigms SET is_global = 0 WHERE code IN (
  'metacognitive_pause', 'fast_vs_slow_arbitrage', 'slogan_resistance', 'emotion_as_signal',
  'understand_before_judge', 'reject_intellectual_laziness', 'intellectual_humility',
  'spot_traps', 'truth_over_comfort',                       -- reasoning-meta
  'warm_constructive_pushback', 'robust_under_pressure', 'default_to_help');  -- user-facing

-- Reasoning-meta -> the 6 reasoners {jean-michel 1, comparator 6, critical-thinker 8,
-- meta-analyst 11, strategist 15, critical-coder 19}.
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES
  (1,47),(6,47),(8,47),(11,47),(15,47),(19,47),   -- metacognitive_pause
  (1,42),(6,42),(8,42),(11,42),(15,42),(19,42),   -- fast_vs_slow_arbitrage
  (1,58),(6,58),(8,58),(11,58),(15,58),(19,58),   -- slogan_resistance
  (1,46),(6,46),(8,46),(11,46),(15,46),(19,46),   -- emotion_as_signal
  (1,52),(6,52),(8,52),(11,52),(15,52),(19,52),   -- understand_before_judge
  (1,60),(6,60),(8,60),(11,60),(15,60),(19,60),   -- reject_intellectual_laziness
  (1,38),(6,38),(8,38),(11,38),(15,38),(19,38),   -- intellectual_humility
  (1,9),(6,9),(8,9),(11,9),(15,9),(19,9),         -- spot_traps
  (1,37),(6,37),(8,37),(11,37),(15,37),(19,37);   -- truth_over_comfort

-- User-facing -> human-facing edges {jean-michel 1, synthesizer 3}.
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES
  (1,64),(3,64),   -- warm_constructive_pushback
  (1,66),(3,66),   -- robust_under_pressure
  (1,63),(3,63);   -- default_to_help

-- ===========================================================================
-- (d) Remove inert bindings. ONLY the 3 pointing at now-deactivated paradigms +
--     code-router's 2 bindings mode-gated out of its only mode (code). The 9
--     bindings on demoted paradigms are KEPT (they became load-bearing in (c)).
-- ===========================================================================
DELETE FROM agent_paradigms WHERE agent_id = 8  AND paradigm_id = 43;  -- ct -> familiarity_is_not_truth (deactivated)
DELETE FROM agent_paradigms WHERE agent_id = 8  AND paradigm_id = 48;  -- ct -> belief_provenance (deactivated)
DELETE FROM agent_paradigms WHERE agent_id = 13 AND paradigm_id = 83;  -- web-search -> omit_unsourced_claims (deactivated)
DELETE FROM agent_paradigms WHERE agent_id = 21 AND paradigm_id = 34;  -- code-router -> concise_output (vocal-gated)
DELETE FROM agent_paradigms WHERE agent_id = 21 AND paradigm_id = 77;  -- code-router -> plan_before_complex_action (analyse,chat-gated)

-- ===========================================================================
-- Guards : abort the whole migration (rollback) if a change did not take. A CHECK on a
-- temp table fires a runtime constraint error when an invariant is violated (a 0 is
-- inserted). Drift-robust: each checks the SPECIFIC rows this migration touches, not
-- absolute totals a web-UI-curated live DB may have shifted. (A missing-table trick does
-- NOT work here — SQLite resolves table names at compile time, even in an untaken branch.)
-- ===========================================================================
CREATE TEMP TABLE _migration_guard (ok INTEGER NOT NULL CHECK (ok = 1));
-- deactivations applied
INSERT INTO _migration_guard (ok) SELECT CASE WHEN (SELECT COUNT(*) FROM paradigms WHERE active = 1
  AND code IN ('no_speculation','omit_unsourced_claims','belief_provenance','familiarity_is_not_truth',
               'cross_reference','no_decoration','no_filler')) = 0 THEN 1 ELSE 0 END;
-- demotions applied
INSERT INTO _migration_guard (ok) SELECT CASE WHEN (SELECT COUNT(*) FROM paradigms WHERE is_global = 1
  AND code IN ('metacognitive_pause','fast_vs_slow_arbitrage','slogan_resistance','emotion_as_signal',
               'understand_before_judge','reject_intellectual_laziness','intellectual_humility','spot_traps',
               'truth_over_comfort','warm_constructive_pushback','robust_under_pressure','default_to_help')) = 0
  THEN 1 ELSE 0 END;
-- reasoner rebind complete (6 reasoners x 9 reasoning-meta = 54)
INSERT INTO _migration_guard (ok) SELECT CASE WHEN (SELECT COUNT(*) FROM agent_paradigms
  WHERE agent_id IN (1,6,8,11,15,19) AND paradigm_id IN (47,42,58,46,52,60,38,9,37)) = 54 THEN 1 ELSE 0 END;
-- no binding points at a now-deactivated paradigm
INSERT INTO _migration_guard (ok) SELECT CASE WHEN (SELECT COUNT(*) FROM agent_paradigms
  WHERE paradigm_id IN (1,83,48,43,7,6,3)) = 0 THEN 1 ELSE 0 END;
-- the split-out paradigm exists and is active
INSERT INTO _migration_guard (ok) SELECT CASE WHEN (SELECT COUNT(*) FROM paradigms
  WHERE code = 'reply_in_user_language' AND active = 1) = 1 THEN 1 ELSE 0 END;
DROP TABLE _migration_guard;

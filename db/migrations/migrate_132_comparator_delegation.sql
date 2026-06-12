-- =============================================================================
-- migrate_132_comparator_delegation.sql
-- =============================================================================
-- P6 consolidation: fix the comparator-specialist mission <-> grants drift.
--
-- comparator-specialist's mission says it "gathers factual data for each entity
-- via parallel delegations to domain specialists", but it had NO rows in
-- agent_delegation_targets. Per the PreToolUse hook, an EMPTY delegation
-- whitelist means NO restriction (legacy behaviour) — so the agent could
-- delegate anywhere, and the synoptic could not show its real chains.
--
-- Give it the explicit whitelist its mission implies (least privilege + an
-- accurate synoptic): the read/data specialists it compares across.
-- Idempotent (INSERT OR IGNORE on the (agent_id, target_code) PK).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'comparator-specialist'), v
FROM (
    SELECT 'web-search-specialist' AS v UNION
    SELECT 'wikipedia-specialist'      UNION
    SELECT 'weather-specialist'        UNION
    SELECT 'news-specialist'
);

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT target_code FROM agent_delegation_targets
--   WHERE agent_id = (SELECT id FROM agents WHERE code='comparator-specialist');
--   -- news-specialist, weather-specialist, web-search-specialist, wikipedia-specialist

-- =============================================================================
-- migrate_155_meta_analyst_promotes.sql
-- =============================================================================
-- Étape 6 : replier la méta-analyse dans la boucle ancrée. Le meta-analyst (agent 11)
-- reçoit `propose_memory`, et sa doctrine `no_self_modification` route désormais les
-- améliorations vers des CANDIDATS-RÈGLE ancrés (propose_memory kind=rule) basés sur le
-- roster RÉEL (self_inspect_config) — fin des hallucinations de noms d'agents/outils. Il
-- PROPOSE, n'applique pas (revue humaine via /memo + admin `promotions`).
-- Idempotent (INSERT OR IGNORE ; UPDATE).
-- =============================================================================

PRAGMA foreign_keys = ON;

-- 1. Grant propose_memory to the meta-analyst.
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (11, 'propose_memory');

-- 2. Doctrine : improvements are proposed as grounded rules (no source files, no auto-apply).
UPDATE paradigms
   SET content = '- You produce proposals — you do not execute them.
- Never call workspace_create_file to write Python source files that would alter system behavior.
- Propose durable improvements (recurring failures, missing tool grants, methodologies) as paradigm RULES via propose_memory(kind=rule), grounded on the REAL roster from self_inspect_config — reference only agents and tools that exist ; never invent names.',
       rationale = 'Hard safety boundary: the meta-analyst observes and PROPOSES (via propose_memory) ; the human decides and applies. Grounding in real state kills invented agent/tool names.',
       modified_at = '2026-06-18T00:00:00Z'
 WHERE code = 'no_self_modification';

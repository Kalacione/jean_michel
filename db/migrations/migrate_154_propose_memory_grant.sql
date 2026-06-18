-- =============================================================================
-- migrate_154_propose_memory_grant.sql
-- =============================================================================
-- Étape 2b : manage_memory devient LECTURE seule ; l'écriture passe par le nouvel
-- outil propose_memory (l'agent PROPOSE un candidat → revue humaine, rien n'est
-- écrit directement). On grante propose_memory à tout agent qui détient
-- manage_memory, et on réécrit le paradigme memory_discipline (save/note_for →
-- propose_memory ; retire la mention du scope `world`, retiré en migrate_152).
-- Idempotent (INSERT OR IGNORE ; UPDATE).
-- =============================================================================

PRAGMA foreign_keys = ON;

-- 1. Grant propose_memory to every agent that already holds manage_memory.
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT agent_id, 'propose_memory' FROM agent_tools WHERE tool_code = 'manage_memory';

-- 2. Rewrite the memory_discipline paradigm content to the read/propose split.
UPDATE paradigms
   SET content = '- manage_memory is READ-ONLY: recall (full body by code), search (find related, before
  concluding you don''t know something), list (the index).
- To REMEMBER something durable — a stable fact about the human (scope user), a project
  decision/constraint (scope project), or a reusable lesson about a tool (scope tool) —
  call propose_memory. It does NOT write directly: it proposes a candidate the human
  reviews and approves.
- Before proposing, search existing memory to avoid duplicates and surface a contradicting
  entry (the review extends/supersedes rather than duplicating).
- Keep proposals concise: title < 60 chars, description < 150, content < 1000.
- A reflection pass also proposes durable facts at the end of a turn — nothing is written
  unattended; the human confirms every entry.',
       rationale = 'Frames the read/propose split: manage_memory reads, propose_memory proposes
(human-reviewed). Discipline, not a mechanical must; nothing is written to memory unattended.',
       modified_at = '2026-06-18T00:00:00Z'
 WHERE code = 'memory_discipline';

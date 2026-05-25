-- Migration 046 — Depth promotion (empowerment hybride)
-- Crée la table agent_delegation_targets (whitelist de délégation).
-- Insère les cibles autorisées pour jean-michel et critical-thinker.
-- Ajoute le paradigme subresearch_inline pour web/wikipedia specialists.

-- Table de whitelist
CREATE TABLE agent_delegation_targets (
  agent_id    INTEGER NOT NULL REFERENCES agents(id),
  target_code TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (agent_id, target_code)
);

-- Cibles autorisées pour jean-michel (tous les agents sauf archivist)
INSERT INTO agent_delegation_targets (agent_id, target_code) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'), 'web-search-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'wikipedia-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'critical-thinker'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'document-builder'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'workspace-manager'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'comparator-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'code-runner'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'meta-analyst'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'weather-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'summarizer');

-- Cibles autorisées pour critical-thinker (sous-recherche factuelle uniquement)
INSERT INTO agent_delegation_targets (agent_id, target_code) VALUES
  ((SELECT id FROM agents WHERE code='critical-thinker'), 'web-search-specialist'),
  ((SELECT id FROM agents WHERE code='critical-thinker'), 'wikipedia-specialist');

-- Paradigme subresearch_inline (web/wikipedia specialists)
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT
  (SELECT id FROM categories WHERE code='execution'),
  'subresearch_inline', 'Sub-research within a single delegation',
  '- When a result reveals a disambiguation (Wikipedia disambiguation page, multiple homonyms, ambiguous link), DO NOT abort or escalate. Pick the most relevant candidate(s) and continue the search inline within the same request.
- When following a sub-research path, call plan_update(action="add_substep", parent_step_id=..., title=..., reason="why this branch") BEFORE the new tool calls. This makes the depth-of-research visible in plan.md.
- Limit: at most 3 substeps per delegation. Beyond, signal completion with gather_done and let the orchestrator route via a fresh delegation.',
  'Avoid coupling the depth of investigation to the recursion depth of agents.',
  0, 100, 1, datetime('now'), datetime('now');

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, (SELECT id FROM paradigms WHERE code='subresearch_inline')
FROM agents a WHERE a.code IN ('web-search-specialist', 'wikipedia-specialist');

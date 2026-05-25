-- Migration 015 : découpage self_inspect en 3 outils scopés
--
-- self_inspect (monolithique) → remplacé par :
--   self_inspect_config      (agents + paradigms)
--   self_inspect_activity    (conversations + sandbox + recent_summaries)
--   self_inspect_architecture (README + schema.sql)
--
-- Grants :
--   meta-analyst : les 3 nouveaux outils (retire l'ancien self_inspect)
--   code-runner  : self_inspect_architecture uniquement
--   document-builder : self_inspect_architecture uniquement

-- meta-analyst : remplacer self_inspect par les 3 outils scopés
DELETE FROM agent_tools WHERE agent_id=11 AND tool_code='self_inspect';

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (11, 'self_inspect_config'),
  (11, 'self_inspect_activity'),
  (11, 'self_inspect_architecture');

-- code-runner (id=12) : architecture seulement (comprendre le projet avant d'écrire du code)
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (12, 'self_inspect_architecture');

-- document-builder (id=9) : architecture seulement (lire les docs existantes)
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (9, 'self_inspect_architecture');

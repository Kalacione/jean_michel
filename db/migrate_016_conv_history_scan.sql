-- Migration 016 : conv_history_scan — accès historique conversations pour meta-analyst
--
-- Permet au meta-analyst de scanner les conversations passées au-delà des 5
-- derniers summaries fournis par self_inspect_activity(scope='recent_summaries').
-- Conçu pour l'analyse de tendances et la formulation de propositions d'amélioration.

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES
  (11, 'conv_history_scan');

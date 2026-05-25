-- Migration 014 : workspace write grants — principe de moindre privilège
-- Retire workspace_create_file + workspace_str_replace + agent_workspace_grants
-- des agents sans vocation d'écriture : summarizer (2), synthesizer (3), comparator-specialist (6).
-- Ces agents conservent workspace_view + workspace_list (lecture seule).

DELETE FROM agent_tools
WHERE (agent_id, tool_code) IN (
  (2, 'workspace_create_file'),
  (2, 'workspace_str_replace'),
  (3, 'workspace_create_file'),
  (3, 'workspace_str_replace'),
  (6, 'workspace_create_file'),
  (6, 'workspace_str_replace')
);

DELETE FROM agent_workspace_grants WHERE agent_id IN (2, 3, 6);

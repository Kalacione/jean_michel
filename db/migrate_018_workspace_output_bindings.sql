-- Migration 018 : bindings document_workspace_output aux agents producteurs
--
-- Le paradigme 88 (document_workspace_output) était limité à document-builder
-- et meta-analyst. On l'étend à tous les agents qui peuvent produire des
-- fichiers livrables dans le workspace.

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, 88
FROM agents a
WHERE a.code IN (
  'wikipedia-specialist',
  'comparator-specialist',
  'archivist',
  'web-search-specialist'
);

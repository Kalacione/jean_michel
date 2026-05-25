-- MIGRATION 023: format de retour prescriptif pour agents de collecte + critical-thinker
--
-- Agents de collecte (web-search-specialist, wikipedia-specialist):
--   return_to_user en 4 sections courtes, tout le contenu dans workspace.
-- Critical-thinker:
--   ajout d'une 5e section "Orchestrator summary" — cartographie épistémique
--   (pas un verdict, juste l'état des claims après analyse).

-- PARADIGME 114 : format de retour pour les agents de collecte
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (114, 34, 'research_return_format', 'Research return format',
'- Structure return_to_user under exactly four sections:
  ## Established
    Bullet list: each confirmed fact with source URL. One line per fact maximum.
  ## Not found / Contradicted
    Bullet list: claims searched but not found, sources that contradict each other, or queries that returned nothing useful. State what was searched.
  ## Suggested next step
    One concrete action for the orchestrator. Examples:
    - "delegate to critical-thinker with workspace/findings_X.md"
    - "search again with query Y — current results were too shallow"
    - "findings sufficient — proceed to document-builder"
  ## Workspace file
    The relative path of the workspace file containing the full findings.
- Keep return_to_user under 30 lines. All detailed content (full text, sources, quotes) goes in the workspace file only.
- Do not paste raw JSON, full article excerpts, or long passages into return_to_user.',
'Forces research agents to return a compact, actionable summary to the orchestrator rather than raw findings. Keeps inter-agent payloads small.', 0, 15, 1, datetime('now'), datetime('now'));

-- Assigner aux agents de collecte
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, 114 FROM agents a
WHERE a.code IN ('web-search-specialist', 'wikipedia-specialist');

-- Mettre à jour critical_thinker_format pour ajouter la 5e section
UPDATE paradigms
SET content = '- Structure the critical analysis under exactly five sections:
  ## Claims identified
    Each main claim, stated in the strongest possible form (steelman).
  ## Assumptions surfaced
    Unstated premises the claims rest on.
  ## Biases and shortcuts detected
    Cognitive biases, manipulation patterns, framing effects observed.
  ## Evidence quality
    What is verifiable, what is not, what would be needed to verify.
  ## Orchestrator summary
    Not a verdict — a cartography of the epistemic state after analysis:
    - Supported: claims for which the available evidence is sufficient
    - Weakened / not supported: claims for which evidence is absent, contradicted, or low-quality
    - Suggested next step: one concrete action (e.g. "proceed to document-builder", "search for X to resolve open point Y")
- The first four sections end with observation, not position. The fifth section maps state only — no normative judgement.',
  modified_at = datetime('now')
WHERE code = 'critical_thinker_format';

-- =============================================================================
-- migrate_105_strategist_agent.sql
-- =============================================================================
-- Crée un nouveau specialist `strategist` dédié à la décomposition stratégique
-- des requêtes exploratoires (inventaire / listing / recherche multi-domaine).
--
-- Contexte :
--   La migration 103 avait placé `model_override='gemma4:26b'` sur jean-michel
--   pour qu'il génère 5-7 angles thématiques avant de déléguer. Diagnostic
--   ultérieur : c'était un cache-misère — le router ne doit que router, pas
--   raisonner. Le besoin de raisonnement intense (décomposition stratégique)
--   appartient à un agent dédié dont c'est le métier.
--
-- Cette migration :
--   1. Annule le model_override de jean-michel (retour à MAIN_MODEL = default).
--   2. Crée l'agent `strategist` (specialist, model_override=gemma4:26b — sa
--      raison d'être EST le raisonnement).
--   3. Met model_override=gemma4:26b sur les 3 autres "reasoners" légitimes :
--      critical-thinker, comparator-specialist, meta-analyst.
--   4. Déplace le paradigme `parallel_specialists_for_inventory` de jean-michel
--      vers strategist (et le réécrit pour refléter "tu produis un plan" et
--      non plus "tu spawn des délégations").
--   5. Ajoute un nouveau paradigme `strategist_first` côté jean-michel :
--      "pour requête exploratoire, ta première délégation va à strategist".
--   6. Ajoute un paradigme `strategist_output_contract` (format du plan).
--   7. Ajoute strategist comme delegation_target de jean-michel.
--   8. Accorde les tool grants minimaux à strategist (manage_user_memory,
--      workspace_view, workspace_create_file — il écrit le plan).
--
-- Idempotente : INSERT OR IGNORE, UPDATE conditionnel, DELETE explicite.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- 1. Retour de jean-michel sur MAIN_MODEL (annulation du cache-misère)
-- =============================================================================

UPDATE agents
SET model_override = NULL, modified_at = datetime('now')
WHERE code = 'jean-michel';

-- =============================================================================
-- 2. Création de l'agent strategist
-- =============================================================================

INSERT OR IGNORE INTO agents (
    code, name, role, mission,
    thinking_mode, temperature, active,
    model_override, sandbox_image,
    created_at, modified_at
) VALUES (
    'strategist',
    'Strategist',
    'specialist',
    'Decompose an open exploratory brief (inventory / listing / multi-domain '
    || 'search / "find me sources/tools/X across domains") into 3-7 disjoint '
    || 'thematic axes. For each axis, draft a focused briefing suitable for a '
    || 'downstream specialist (web-search-specialist, wikipedia-specialist, '
    || 'etc.). Return the decomposition as a structured plan. You do NOT '
    || 'execute the searches yourself — the router does, in parallel, based '
    || 'on your plan.',
    1,      -- thinking_mode ON (this agent IS reasoning)
    0.3,    -- slight creative bias for axis generation
    1,      -- active
    'gemma4:26b',  -- légitime : son métier EST le raisonnement
    NULL,
    datetime('now'), datetime('now')
);

-- =============================================================================
-- 3. model_override gemma4:26b sur les autres reasoners légitimes
-- =============================================================================
-- Ces 3 specialists ont pour métier le raisonnement (analyse de claims,
-- comparaison multi-source, analyse de patterns). Le slot par défaut
-- (gemma4:latest, ~9b) est sous-dimensionné pour leur tâche.

UPDATE agents
SET model_override = 'gemma4:26b', modified_at = datetime('now')
WHERE code IN ('critical-thinker', 'comparator-specialist', 'meta-analyst')
  AND (model_override IS NULL OR model_override <> 'gemma4:26b');

-- =============================================================================
-- 4. Déplacement + réécriture du paradigme parallel_specialists_for_inventory
-- =============================================================================
-- Le paradigme reste sur le même id (132) mais change de propriétaire
-- (jean-michel → strategist) et de contenu (router-side → strategist-side).

-- 4a. Détacher de jean-michel
DELETE FROM agent_paradigms
WHERE paradigm_id = (SELECT id FROM paradigms WHERE code = 'parallel_specialists_for_inventory')
  AND agent_id   = (SELECT id FROM agents WHERE code = 'jean-michel');

-- 4b. Réécrire le contenu (perspective strategist : "tu produis un plan")
UPDATE paradigms
SET content =
'- Your job is to RETURN A PLAN, not to execute searches. The router will
  parallelize the downstream delegations based on what you return.
- Identify 3-7 DISJOINT thematic axes covering the brief. Disjoint means : no
  two axes target the same kind of source. If your axes overlap, the parallel
  specialists will return overlapping results.
- For each axis, draft a focused briefing in English containing :
    - the axis name (e.g. "weather / environment")
    - the scope (what to look for, what to avoid)
    - the recommended downstream specialist (web-search-specialist,
      wikipedia-specialist, …)
- Write the plan as a workspace markdown file (e.g. `plan_decomposition.md`)
  with one section per axis. Include the file in your `report_back`
  `files_produced` so the router can read it.
- Do NOT delegate yourself. Do NOT call web_search or wikipedia_search. Your
  output is the plan, period.
- For requests that are NOT exploratory (a single targeted question, a known
  factual lookup), reply with `report_back(confidence="low",
  low_confidence_reason="not an exploratory brief — caller should delegate
  directly to the relevant specialist")`.',
    title = 'Plan decomposition discipline',
    rationale = 'Migration 105 : strategist owns this paradigm. It used to live
on jean-michel (router) but routing is not reasoning — moved to a dedicated
reasoner.',
    modified_at = datetime('now')
WHERE code = 'parallel_specialists_for_inventory';

-- 4c. Renommer le code pour qu'il reflète son nouveau scope
UPDATE paradigms
SET code = 'strategist_decomposition_discipline'
WHERE code = 'parallel_specialists_for_inventory';

-- 4d. Attacher à strategist
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'strategist'),
    (SELECT id FROM paradigms WHERE code = 'strategist_decomposition_discipline');

-- =============================================================================
-- 5. Nouveau paradigme strategist_first (côté jean-michel)
-- =============================================================================
-- Indique au router quand convoquer strategist (cas spécifique : exploratoire
-- ouvert). Pas universel — pour une question simple, on délègue direct.

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'planning'),
    'strategist_first',
    'Use the strategist for open exploratory briefs',
'- When the brief is OPEN and EXPLORATORY (inventory, listing, "find me sources
  / tools / candidates / references across domains"), do NOT delegate directly
  to a single web-search-specialist. Your first delegation goes to
  `strategist` for thematic decomposition.
- `strategist` returns a plan (a workspace markdown file listing 3-7 disjoint
  axes, each with a draft briefing and a recommended specialist). Read it,
  then spawn one delegation per axis IN PARALLEL with the briefings from the
  plan.
- This pattern applies ONLY to broad exploration. For a single targeted
  question ("what is the capital of Estonia", "current weather in Paris",
  "summarise this article"), delegate directly to the relevant specialist —
  no need for the strategist.
- After the parallel specialists return, hand the support_files to
  `document-builder` (or `synthesizer` for prose) for the final assembly.',
    'Migration 105 : confine la décomposition stratégique à l''agent dédié.
Le router ne raisonne pas, il route.',
    0, 40, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'strategist_first');

-- =============================================================================
-- 6. Tool grants pour strategist
-- =============================================================================
-- Minimum viable : il lit le workspace (au cas où d'autres fichiers existent),
-- écrit son plan, manipule sa memory utilisateur. Pas de web_search, pas de
-- wikipedia — il décompose à partir du brief, pas en recherchant.

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT (SELECT id FROM agents WHERE code = 'strategist'), v
FROM (SELECT 'workspace_view' AS v
      UNION SELECT 'workspace_create_file'
      UNION SELECT 'manage_user_memory');

-- =============================================================================
-- 7. Workspace write grant pour strategist
-- =============================================================================
-- Sans cette ligne, workspace_create_file échouera au runtime
-- (no_write_grant). strategist écrit son plan, c'est légitime.
-- La table agent_workspace_grants n'a qu'une colonne agent_id : présence
-- d'une row = write grant. Pas de notion de "scope".

INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'strategist';

-- =============================================================================
-- 8. strategist comme delegation_target de jean-michel
-- =============================================================================

INSERT OR IGNORE INTO agent_delegation_targets (agent_id, target_code)
SELECT (SELECT id FROM agents WHERE code = 'jean-michel'), 'strategist';

-- =============================================================================
-- 9. Paradigmes globaux applicables (report_back_format, briefing_contract...)
-- =============================================================================
-- strategist hérite naturellement des paradigmes is_global=1. Pour les
-- paradigmes spécifiques aux specialists (research_return_format,
-- source_admission_criteria, nested_delegation_discipline, report_back_format),
-- on attache explicitement ceux qui s'appliquent à un planner.

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'strategist'), p.id
FROM paradigms p
WHERE p.code IN ('report_back_format', 'nested_delegation_discipline');
-- NB : on n'attache PAS research_return_format (strategist ne fait pas de
-- research) ni source_admission_criteria (idem).

COMMIT;

-- =============================================================================
-- VALIDATION post-migration
-- =============================================================================
-- SELECT code, role, model_override FROM agents WHERE code = 'strategist';
--   -- attendu : strategist | specialist | gemma4:26b
--
-- SELECT code, model_override FROM agents
-- WHERE code IN ('jean-michel','strategist','critical-thinker',
--                'comparator-specialist','meta-analyst');
--   -- attendu : jean-michel=NULL, strategist=gemma4:26b,
--               critical-thinker=gemma4:26b, comparator-specialist=gemma4:26b,
--               meta-analyst=gemma4:26b
--
-- SELECT a.code FROM agent_paradigms ap
-- JOIN agents a ON a.id=ap.agent_id
-- JOIN paradigms p ON p.id=ap.paradigm_id
-- WHERE p.code='strategist_decomposition_discipline';
--   -- attendu : strategist
--
-- SELECT a.code FROM agent_paradigms ap
-- JOIN agents a ON a.id=ap.agent_id
-- JOIN paradigms p ON p.id=ap.paradigm_id
-- WHERE p.code='strategist_first';
--   -- attendu : jean-michel
--
-- SELECT target_code FROM agent_delegation_targets WHERE agent_id =
--   (SELECT id FROM agents WHERE code='jean-michel');
--   -- attendu : doit contenir strategist

-- MIGRATION 024: agent dispatcher (planification avant exécution)
--
-- Principe: jean-michel orchestre, le dispatcher pense.
-- Pour toute tâche deep_research, jean-michel délègue au dispatcher en premier.
-- Le dispatcher analyse la problématique, identifie les inconnues,
-- décompose en étapes séquencées, et écrit workspace/plan.md.
-- jean-michel suit le plan sans reformuler la stratégie lui-même.

-- Catégorie dédiée dans section process
INSERT OR IGNORE INTO categories (id, section_id, code, title, order_priority, active, created_at, modified_at)
VALUES (35, (SELECT id FROM sections WHERE code='process'), 'planning', 'Planning', 25, 1, datetime('now'), datetime('now'));

-- Agent dispatcher
INSERT OR IGNORE INTO agents (id, code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES (14, 'dispatcher', 'Dispatcher', 'specialist',
  'Analyse a complex request, surface unknowns and ambiguities, decompose it into a clear ordered sequence of steps, and write the resulting plan to workspace/plan.md. Return a concise summary of the plan. Do not execute the steps — plan only.',
  1, 0.3, 1, datetime('now'), datetime('now'));

-- Outils du dispatcher: créer/lire le plan dans workspace, clarifier avec l'utilisateur si besoin
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (14, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (14, 'workspace_view');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (14, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (14, 'ask_human');

-- PARADIGME 115 : format du plan produit par le dispatcher
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (115, 35, 'dispatcher_plan_format', 'Dispatcher plan format',
'- Always write the plan to workspace/plan.md via workspace_create_file before returning.
- Structure the file as:
  # Plan: [short title]

  ## Goal
  One-sentence restatement of what the user actually wants as output.

  ## Unknowns
  Bullet list of ambiguities or missing information that could invalidate the plan.
  If critical unknowns exist, use ask_human to resolve them before writing the plan.

  ## Steps
  Numbered list. Each step must specify:
  - What to do (one action)
  - Which agent to delegate to
  - What the expected deliverable is (a workspace file path or a concrete answer)
  - Whether it depends on a previous step

  ## Risks
  What could block or invalidate the plan. Be brief.

  ## Success criteria
  How the orchestrator will know the task is complete.
- Return to the orchestrator: the workspace/plan.md path + a one-paragraph plain-text summary of the steps.',
'Forces explicit task decomposition before any research or production work begins. The plan becomes the single source of truth for the orchestrator.', 0, 10, 1, datetime('now'), datetime('now'));

-- PARADIGME 116 : posture du dispatcher (planifier, pas exécuter)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (116, 35, 'plan_not_execute', 'Plan, do not execute',
'- Your role is to decompose and plan, not to perform research, write documents, or produce analysis.
- Do not call web_search, wikipedia, or any content-producing tool.
- If you lack information to plan (ambiguous goal, missing constraints), use ask_human once to clarify before planning.
- A good plan is specific enough that any agent reading a step knows exactly what to do and what to deliver.',
'Keeps the dispatcher focused on decomposition. Prevents scope creep into execution.', 0, 11, 1, datetime('now'), datetime('now'));

-- Lier les paradigmes au dispatcher
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT 14, id FROM paradigms
WHERE code IN (
  'dispatcher_plan_format', 'plan_not_execute',
  'assess_complexity_first', 'depth_over_speed',
  'assumption_surface', 'questioning_priority',
  'orchestrator_inquiry_loop', 'burden_of_proof'
);

-- Mettre à jour plan_before_complex_action pour jean-michel:
-- pour deep_research, déléguer au dispatcher plutôt que planifier soi-même
UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, do NOT plan yourself. Delegate to dispatcher first with the full user request. The dispatcher will produce workspace/plan.md — follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, delegate to dispatcher instead of guessing.',
  modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

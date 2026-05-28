-- =============================================================================
-- migrate_100_paradigm_realignment.sql
-- =============================================================================
-- Migration générée à partir de DevNotes/REVOLUCION/08_paradigm_audit_table.md
-- Phase 0 du plan d'implémentation (DevNotes/REVOLUCION/07_plan_implementation.md).
--
-- Statistique :
--   119 paradigmes audités → 84 keep + 8 edit + 7 rewrite + 2 merge + 18 delete
--   + 5 paradigmes nouveaux insérés.
--   Résultat post-migration : 105 paradigmes actifs.
--
-- Migration en une seule transaction. Idempotente : appliquer plusieurs fois
-- ne casse pas la base (DELETE par id, INSERT OR IGNORE).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- 1. SUPPRESSIONS — 18 paradigmes obsolètes + 2 paradigmes fusionnés
-- =============================================================================
-- IDs supprimés :
--   10, 11, 12 : audit_phase, sprint_phase, check_existing (orphelins, no binding)
--   15-18     : no_overengineering, centralize_duplication, logical_anchoring,
--               concise_comments (orphelins, code-tier paradigms)
--   27        : comparison_routing (anti-loop incantatoire, comparator visible
--               dans Delegation targets)
--   31, 32    : archivist_format, archivist_tone (archivist supprimé en v2)
--   75        : assess_complexity_first (set_task_class supprimé)
--   98        : code_execution_routing (anti-loop)
--   100       : convergence_gate (signal_convergence supprimé)
--   102       : research_phase_routing (anti-loop)
--   104       : meta_analysis_routing (anti-loop)
--   109       : orchestrator_inquiry_loop (completion_verb supprimé)
--   118       : metacog_live_monitor (modèle budget v1 obsolète)
--   124       : planning_with_todos (manage_todo_list supprimé)
--
-- IDs fusionnés (deviennent workspace_progressive_write) :
--   103       : workspace_as_shared_memory
--   106       : wikipedia_persist_before_delegate

-- Suppression explicite des bindings (FK ON DELETE CASCADE le ferait aussi,
-- mais explicite = audit lisible)
DELETE FROM agent_paradigms WHERE paradigm_id IN (
    10, 11, 12, 15, 16, 17, 18,
    27, 31, 32, 75, 98, 100, 102, 104, 109, 118, 124,
    103, 106
);

DELETE FROM paradigm_modes WHERE paradigm_id IN (
    10, 11, 12, 15, 16, 17, 18,
    27, 31, 32, 75, 98, 100, 102, 104, 109, 118, 124,
    103, 106
);

DELETE FROM paradigms WHERE id IN (
    10, 11, 12, 15, 16, 17, 18,
    27, 31, 32, 75, 98, 100, 102, 104, 109, 118, 124,
    103, 106
);

-- =============================================================================
-- 2. REWRITES — 7 paradigmes au contenu entièrement réécrit
-- =============================================================================

-- ID 14 — briefing_contract : version concise, mention step_budget_exhausted retirée
UPDATE paradigms SET content =
'- A `delegate_to` call must include: a clear mission, the expected outcome,
  and any relevant `support_files` paths (workspace files written this turn).
- Briefings between agents are written in **English**. Do NOT include language
  instructions in a briefing — the receiving agent handles output language
  automatically.
- Translate all non-English common nouns (clothing, animals, food, concepts)
  to English in the briefing, with the original term in parentheses for
  traceability. Example: "boxer shorts (caleçon)". Proper nouns and specialised
  technical terms with no English equivalent may stay in the original language.
- Independent subtasks may be emitted as multiple `delegate_to` calls in the
  same turn.',
modified_at = datetime('now')
WHERE id = 14;

-- ID 35 — no_context_recap : mention summary.md retirée
UPDATE paradigms SET content =
'- The conversation history is in your message context as previous turns,
  not a separate summary. Do not paraphrase or repeat what the user has
  already heard from you.
- Address the new turn directly.',
modified_at = datetime('now')
WHERE id = 35;

-- ID 77 — plan_before_complex_action : manage_todo_list retiré
UPDATE paradigms SET content =
'- For requests requiring multiple coordinated steps (research + critique + build,
  or multiple parallel delegations), draft a brief routing plan in your thought
  channel before acting: which agents, in what order, what each delivers.
- A plan you cannot articulate is a plan you do not have. If you cannot describe
  what each delegation adds, reconsider before delegating.
- After each delegation completes, evaluate the result. If there is a gap:
  follow up with a targeted sub-delegation, or proceed to synthesis if the gap
  is acceptable.',
modified_at = datetime('now')
WHERE id = 77;

-- ID 84 — memory_without_narration : référence à summary.md remplacée par messages context
UPDATE paradigms SET content =
'- The conversation history lives in your message context — previous turns are
  there as if you naturally remember them. Use them like a colleague recalling
  shared history.
- Never use phrases like "Looking at our previous turns…", "Earlier in our
  conversation…", "As you mentioned before…" — just surface the relevant fact.
- Surface the fact, not the mechanism that retrieved it.',
modified_at = datetime('now')
WHERE id = 84;

-- ID 85 — no_overfamiliarity_from_summary : alignement messages context
UPDATE paradigms SET content =
'- Having conversation history in context does not mean the user wants you to
  bring up everything you remember.
- Apply only the elements of past turns directly relevant to the current
  question.
- Do not lead with personal references the user has not just brought up —
  that pattern feels intrusive even when the information is technically
  available.',
modified_at = datetime('now')
WHERE id = 85;

-- ID 114 — research_return_format : report_findings → report_back + low_confidence_reason
UPDATE paradigms SET content =
'- The full findings (sources, quotes, claims, citations) go in a workspace file.
  Your final `report_back` is a thin pointer with:
  - summary (headline finding, 1-3 sentences)
  - files_produced (the workspace files you wrote)
  - confidence (low | medium | high)
  - low_confidence_reason (one sentence, REQUIRED if confidence is "low")
- Workspace file structure (suggested):
  ## Established
    Bullet list: each confirmed fact with source URL.
  ## Not found / Contradicted
    What was searched but not confirmed; sources that disagree.
  ## Open questions
    Things worth a follow-up delegation.
- Never paste raw JSON, full article excerpts, or long passages into the
  `summary` field — those belong in the workspace file.',
modified_at = datetime('now')
WHERE id = 114;

-- ID 121 — router_synthesis_discipline : report_findings → report_back, return_to_user implicit
UPDATE paradigms SET content =
'- After a specialist returns via `report_back`, decide explicitly: follow up
  with another delegation, or synthesize the answer for the user directly.
- If the report includes sub_questions you want to follow up on, delegate to
  the appropriate agent.
- When all necessary research is done, produce the answer as an assistant
  message without further tool calls. The orchestrator detects this as the
  conversation''s end point.
- Never re-delegate the same question without narrowing the scope.',
modified_at = datetime('now')
WHERE id = 121;

-- =============================================================================
-- 3. REWRITES profonds des paradigmes initialement marqués "edit" mais qui
--    nécessitent un nouveau contenu (sous-blocs détaillés en 08)
-- =============================================================================

-- ID 88 — document_workspace_output : report_findings → report_back
UPDATE paradigms SET content =
'- All produced documents MUST be written to workspace files via
  `workspace_create_file` (or `workspace_append` for progressive writes).
- Never paste document content directly into `report_back.summary`. The
  summary is 1-3 sentences pointing at the file; the file is the deliverable.
- Use `workspace_str_replace(relative_path, old_str, new_str)` to refine
  a document iteratively. Parameter names are EXACTLY `old_str` and `new_str`.
- Read every `support_file` listed in the briefing via `workspace_view`
  before writing anything.',
modified_at = datetime('now')
WHERE id = 88;

-- ID 92 — report_before_acting : report_findings → report_back
UPDATE paradigms SET content =
'- Before any write operation, state in your thought channel what will change:
  file path, operation type, expected outcome.
- Include the list of files written in `report_back.files_produced` so the
  parent has a clear audit trail.
- If the operation affects multiple files, enumerate them all before
  proceeding.',
modified_at = datetime('now')
WHERE id = 92;

-- ID 108 — search_then_synthesize : aligné avec MAX_SEARCH_CALLS_PER_TURN + report_back
UPDATE paradigms SET content =
'- Each search should target a distinct sub-topic or angle. Do not repeat
  similar queries — vary the keyword, the domain, or the tool.
- After 2-3 productive searches, persist findings to the workspace via
  `workspace_create_file` or `workspace_append`. Do NOT batch all writes to
  the end: your context can be compacted, and the workspace is the
  durable trace.
- A turn-wide search budget of `MAX_SEARCH_CALLS_PER_TURN` is enforced by
  the orchestrator. Plan accordingly: 3-5 targeted queries is typically
  sufficient. Do not burn budget on reformulations of the same query.
- Each workspace entry must include: source URL, relevant claim, confidence.
- If a result URL points to a PDF or requires login, skip it and note it as
  inaccessible.
- Never invent, guess, or fabricate sources, URLs, or facts to fill gaps.
- When done (or budget nearly exhausted), call `report_back(summary,
  files_produced, confidence)`.',
modified_at = datetime('now')
WHERE id = 108;

-- =============================================================================
-- 4. EDITS — paradigmes au contenu légèrement retouché (REPLACE ciblé)
-- =============================================================================

-- ID 21 — depth_aware : "Hard limit is 10" → "Hard limit is 5" (MAX_DEPTH v2)
UPDATE paradigms SET content =
'- Your current recursion depth is shown in CONTEXT. Hard limit is 5.
- If you reach the limit, you must conclude with the information at hand
  and explicitly state that the recursion limit was reached.',
modified_at = datetime('now')
WHERE id = 21;

-- ID 26 — wikipedia_search_strategy : "report_findings" → "report_back"
UPDATE paradigms SET
  content = REPLACE(content, 'report_findings', 'report_back'),
  modified_at = datetime('now')
WHERE id = 26 AND content LIKE '%report_findings%';

-- ID 120 — subresearch_inline : "report_findings" → "report_back"
UPDATE paradigms SET
  content = REPLACE(content, 'report_findings', 'report_back'),
  modified_at = datetime('now')
WHERE id = 120 AND content LIKE '%report_findings%';

-- ID 123 — comparator_output_contract : "report_findings" → "report_back"
UPDATE paradigms SET
  content = REPLACE(content, 'report_findings', 'report_back'),
  modified_at = datetime('now')
WHERE id = 123 AND content LIKE '%report_findings%';

-- ID 4 — one_question_at_a_time : contenu garde, binding restreint à jean-michel
-- (cf. analyse §5 doc 06 : ask_human devient main-agent-only)
DELETE FROM agent_paradigms
WHERE paradigm_id = 4
  AND agent_id <> (SELECT id FROM agents WHERE code = 'jean-michel');

-- =============================================================================
-- 5. NOUVEAUX PARADIGMES — 5 ajouts (cf. §11 bis doc 06)
-- =============================================================================

-- Nouveau paradigme : user_memory_discipline
INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'tool_discipline'),
    'user_memory_discipline',
    'User memory discipline',
'- Save a user_memory entry when the human reveals a durable fact about
  themselves, their preferences, their projects, or their workflows.
- Update an existing entry when a previously saved fact is contradicted
  or refined by the conversation.
- Delete an entry that has become irrelevant (e.g. mention of an abandoned
  project, a corrected preference).
- Recall the full content of an entry when the current conversation
  references something that might be in memory.
- Keep entries concise: title under 60 chars, description under 150 chars,
  content under 1000 chars.',
    'Encadre l''usage du tool manage_user_memory par jean-michel. Discipline,
pas obligation mécanique — le hook PostToolUse peut proposer un save si le
LLM oublie, sans forcer.',
    0, 60, 1, datetime('now'), datetime('now')
);

-- Nouveau paradigme : nested_delegation_discipline
INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'handoff'),
    'nested_delegation_discipline',
    'Nested delegation discipline',
'- The `delegate_to` tool descends the task tree — it never returns to a
  higher-level caller. If a sub-task you encounter exceeds your scope,
  delegate it yourself rather than passing it back up.
- The orchestrator enforces a maximum tree depth via `MAX_DEPTH`. Within
  that limit, descend freely if the sub-task warrants a dedicated specialist.
- Each subagent receives its own fresh context — it does not see your
  conversation history. Pass everything it needs in the briefing or via
  support_files.
- Do not delegate when you can solve the sub-task with a tool call. The
  cost of a delegation is a full new LLM context.',
    'Pose le principe de délégation imbriquée v2 : un subagent peut spawn un
sub-subagent sans repasser par le parent. Aligne avec §5 du doc 06.',
    0, 20, 1, datetime('now'), datetime('now')
);

-- Nouveau paradigme : report_back_format
INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'archival'),
    'report_back_format',
    'report_back format',
'- When concluding your work, call `report_back` with:
  - summary: 1-3 sentences naming the headline finding. Not "I did X" —
    the actual conclusion or the actual content of what you produced.
  - files_produced: the workspace files you wrote, relative to the
    workspace root.
  - confidence: "low" | "medium" | "high" — your self-assessment of how
    completely you delivered the briefing.
  - low_confidence_reason: REQUIRED if confidence is "low". One synthetic
    sentence explaining what is missing or uncertain. Not a recap of your
    reasoning — just the gap.
- Do not paste raw tool outputs into `summary`. Those belong in the
  workspace files.',
    'Décrit le contrat du tool report_back, équivalent v2 de report_findings.
low_confidence_reason est obligatoire si confidence=low (le hook
OnDelegateReturn rejette sinon).',
    0, 20, 1, datetime('now'), datetime('now')
);

-- Nouveau paradigme : workspace_progressive_write
-- Fusion de 103 (workspace_as_shared_memory) + 106 (wikipedia_persist_before_delegate)
INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'workspace_management'),
    'workspace_progressive_write',
    'Workspace progressive write',
'- Persist findings to the workspace as you go, not at the end. After
  every 3-4 information-gathering tool calls, write what you have so far:
  - First time: `workspace_create_file(relative_path, content)`.
  - Subsequent times: `workspace_append(relative_path, content)`.
- Before starting research, check if a relevant workspace file already
  exists via `workspace_list`. If yes, read it with `workspace_view` and
  build on it rather than re-doing the work.
- File naming convention: {agent-code}_{topic-slug}.{ext} — lowercase,
  hyphens for spaces. Example: `wikipedia-specialist_ai-alignment.md`.
- Never reference a workspace path in a briefing or `support_files`
  unless you called `workspace_create_file` for that exact path in this
  same execution. The file must physically exist.',
    'Fusion de workspace_as_shared_memory (id 103) et
wikipedia_persist_before_delegate (id 106). Discipline d''écriture
progressive du workspace, complétée par le hook PostToolUse
(force-persist après N research calls) côté orchestrateur.',
    0, 5, 1, datetime('now'), datetime('now')
);

-- Nouveau paradigme : output_contract_no_inline_dump
INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'execution'),
    'output_contract_no_inline_dump',
    'Output contract: no inline dump',
'- The reply to the human is prose, not a dump of tool results or
  delegation summaries.
- Quote sparingly from workspace files; prefer to construct an answer
  from the findings rather than paste them.
- If the deliverable is a document, reference the workspace file path
  in your reply and let the human read it directly — do not duplicate
  its content inline.',
    'Évite les réponses qui sont juste un copier-coller des tool_responses
ou des report_back. La réponse à l''humain est une synthèse prose.',
    0, 7, 1, datetime('now'), datetime('now')
);

-- =============================================================================
-- 6. BINDINGS — agent_paradigms pour les 5 nouveaux paradigmes
-- =============================================================================

-- user_memory_discipline → jean-michel uniquement
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'user_memory_discipline');

-- nested_delegation_discipline → tous les agents qui ont delegate_to
-- = router + tous les specialists (les finalizers n'ont pas delegate_to)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE p.code = 'nested_delegation_discipline'
  AND a.code IN (
    'jean-michel',
    'summarizer', 'weather-specialist', 'wikipedia-specialist',
    'comparator-specialist', 'critical-thinker', 'document-builder',
    'workspace-manager', 'meta-analyst', 'code-runner',
    'web-search-specialist'
  );

-- report_back_format → tous les specialists (= ceux qui peuvent émettre report_back)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE p.code = 'report_back_format'
  AND a.code IN (
    'summarizer', 'weather-specialist', 'wikipedia-specialist',
    'comparator-specialist', 'critical-thinker', 'document-builder',
    'workspace-manager', 'meta-analyst', 'code-runner',
    'web-search-specialist'
  );

-- workspace_progressive_write → tous les agents avec workspace_write grant
-- Récupération depuis agent_workspace_grants (sauf jean-michel qui ne fait
-- pas de recherche directe)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT g.agent_id, (SELECT id FROM paradigms WHERE code = 'workspace_progressive_write')
FROM agent_workspace_grants g
WHERE g.agent_id <> (SELECT id FROM agents WHERE code = 'jean-michel');

-- output_contract_no_inline_dump → jean-michel + synthesizer (le seul finalizer survivant)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE p.code = 'output_contract_no_inline_dump'
  AND a.code IN ('jean-michel', 'synthesizer');

-- =============================================================================
-- 7. AGENT TOOLS — grant manage_user_memory à jean-michel
-- =============================================================================

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
VALUES (
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    'manage_user_memory'
);

-- =============================================================================
-- 8. AGENT ARCHIVIST — désactivé (suppression dure en Phase 8)
-- =============================================================================
-- En v2, l'archivist devient inutile car messages.json natif porte l'historique.
-- On le désactive ici. La suppression DELETE viendra plus tard (Phase 8) pour
-- ne pas casser les éventuelles FK vers des conversations archivées.

UPDATE agents SET active = 0, modified_at = datetime('now')
WHERE code = 'archivist';

-- =============================================================================
-- 9. VALIDATION post-migration (sanity checks)
-- =============================================================================
-- Les SELECTs suivants doivent retourner les comptes attendus. À exécuter
-- manuellement après application pour vérifier.

-- Comptage paradigmes actifs : attendu 105
-- SELECT COUNT(*) FROM paradigms WHERE active = 1;

-- Paradigmes nouveaux bien insérés : attendu 5
-- SELECT code FROM paradigms WHERE code IN (
--     'user_memory_discipline',
--     'nested_delegation_discipline',
--     'report_back_format',
--     'workspace_progressive_write',
--     'output_contract_no_inline_dump'
-- ) ORDER BY code;

-- Paradigme one_question_at_a_time restreint à jean-michel : attendu 1 ligne
-- SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id = ap.agent_id
-- WHERE ap.paradigm_id = 4;

-- Aucune référence orpheline dans agent_paradigms : attendu 0
-- SELECT COUNT(*) FROM agent_paradigms ap
-- LEFT JOIN paradigms p ON p.id = ap.paradigm_id WHERE p.id IS NULL;

-- Aucune mention de tool supprimé dans les contents : attendu 0
-- SELECT id, code FROM paradigms WHERE active = 1 AND (
--     content LIKE '%set_task_class%' OR
--     content LIKE '%manage_todo_list%' OR
--     content LIKE '%signal_convergence%' OR
--     content LIKE '%planner_done%' OR
--     content LIKE '%gather_done%' OR
--     content LIKE '%critic_done%' OR
--     content LIKE '%build_done%'
-- );
-- (Note : "report_findings" peut survivre dans certains contents non remplacés ;
--  vérifier au cas par cas.)

COMMIT;

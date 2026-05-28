# 08 — Tableau d'audit paradigme-par-paradigme

> Livrable de la **Phase 0** du plan d'implémentation
> (`07_plan_implementation.md`). Audit complet des 119 paradigmes actifs
> en BDD au 2026-05-25, classés selon la grille A-F définie en
> `06_proposition_v2.md §11 bis`, avec décision keep / edit / rewrite /
> delete / merge et justification.
>
> Source des données : `sqlite3 jeanmichel.db` au 2026-05-25 + lecture
> manuelle du `content` et du `rationale` de chaque entrée.
>
> Ce doc est destiné à une **relecture critique humaine** avant écriture
> de la migration SQL `migrate_100_paradigm_realignment.sql`. Toute
> classification est révisable.

## Rappels — grille de classification

| Classe                       | Comportement attendu en v2                                    |
|------------------------------|---------------------------------------------------------------|
| **A. Métacognition métier**  | Garder. Pilier épistémique.                                   |
| **B. Épistémie + biais**     | Garder. Cœur de la qualité de pensée.                         |
| **C. Style + communication** | Garder, vérifier compatibilité multi-mode.                    |
| **D. Tool discipline**       | Garder si non couvert par un hook ; sinon supprimer.          |
| **E. Format de sortie**      | Garder, réécrire pour aligner sur la v2.                      |
| **F. Anti-loop incantatoire**| Supprimer, remplacé par un hook Python.                       |

## Rappels — 5 critères de qualité

1. Pas de référence à un outil supprimé.
2. Pas de MUST en cascade.
3. Indépendant de la mécanique orchestrateur.
4. Concis (3-6 bullets max).
5. Effet observable sur la qualité de la réponse.

## Légende des décisions

- **keep** : conservé tel quel.
- **edit** : conservé avec retouches mineures (suppression d'une mention
  d'outil mort, clarification, alignement de vocabulaire).
- **rewrite** : conservé mais le contenu est entièrement réécrit
  (sous-bloc dédié plus bas).
- **delete** : supprimé. Soit orphelin sans binding, soit contredit la
  v2 sans valeur de réécriture.
- **merge** : fusionné avec un autre paradigme (sous-bloc dédié plus bas).

---

## Section : `communication`

| ID  | code                                | classe | décision | justification                                                                                       |
|-----|-------------------------------------|--------|----------|-----------------------------------------------------------------------------------------------------|
| 1   | no_speculation                      | A      | keep     | Hard rule anti-hallucination. Global. Aucune référence v1.                                          |
| 2   | brutal_truth                        | C      | keep     | Style direct demandé par l'utilisateur. Bound jean-michel.                                          |
| 3   | no_filler                           | C      | keep     | Discipline globale anti-padding.                                                                    |
| 4   | one_question_at_a_time              | C      | edit     | Contenu garde. Mais binding restreint à `jean-michel` seul : `ask_human` devient main-agent-only en v2 (cf. analyse §5 du doc 06). Retirer les bindings sur les subagent-types. |
| 5   | trust_context_defaults              | C      | keep     | Anti ask_human inutile. Compatible v2.                                                              |
| 6   | no_decoration                       | C      | keep     | Discipline style globale.                                                                           |
| 33  | followup_proposals                  | C      | keep     | Bound jean-michel mode chat. Reste pertinent.                                                       |
| 34  | concise_output                      | C      | keep     | Bound 5 agents mode vocal. Sert au pipeline TTS (§12 doc 06).                                       |
| 35  | no_context_recap                    | C      | rewrite  | Mentionne "running summary" (archivist supprimé). Reformuler avec `messages[]` natif.               |
| 63  | default_to_help                     | C      | keep     | Posture fondatrice globale.                                                                         |
| 64  | warm_constructive_pushback          | C      | keep     | Nuance `brutal_truth`. Global.                                                                      |
| 65  | own_mistakes_without_collapse       | C      | keep     | Anti-inverse-sycophancy. Global.                                                                    |
| 66  | robust_under_pressure               | C      | keep     | Companion à 65. Global.                                                                             |
| 67  | respect_user_endings                | C      | keep     | Mode chat+vocal. Bound jean-michel.                                                                 |
| 68  | address_then_clarify                | C      | keep     | Anti-blocking. Bound 11 agents.                                                                     |
| 69  | refuse_simplistic_format            | C      | keep     | Refuse format yes/no sur question contestée.                                                        |
| 70  | minimal_formatting                  | C      | keep     | Global. Anti-over-formatting.                                                                       |
| 71  | no_bullets_when_softening           | C      | keep     | Bound jean-michel. Niche mais utile.                                                                |

## Section : `reasoning`

| ID  | code              | classe | décision | justification                                                                |
|-----|-------------------|--------|----------|------------------------------------------------------------------------------|
| 7   | cross_reference   | B      | keep     | Source-tracing global.                                                       |
| 101 | grounded_analysis | B      | keep     | Bound critical-thinker + meta-analyst. Anti-analyse-sans-source.             |

## Section : `critical_thinking`

| ID  | code                            | classe | décision | justification                                                                                              |
|-----|---------------------------------|--------|----------|------------------------------------------------------------------------------------------------------------|
| 8   | depth_over_speed                | A      | keep     | Bound 6 agents, modes analyse+chat.                                                                        |
| 9   | spot_traps                      | B      | keep     | Umbrella biais. Global.                                                                                    |
| 37  | truth_over_comfort              | A      | keep     | Posture racine. Global.                                                                                    |
| 38  | intellectual_humility           | A      | keep     | Anti-arrogance. Global.                                                                                    |
| 39  | questioning_priority            | A      | keep     | Réflexe d'examen avant réponse.                                                                            |
| 40  | consensus_is_not_evidence       | B      | keep     | Anti-tyrannie-de-la-majorité.                                                                              |
| 41  | confirmation_bias_check         | B      | keep     | Bound 5 agents.                                                                                            |
| 42  | fast_vs_slow_arbitrage          | B      | keep     | Kahneman. Global.                                                                                          |
| 43  | familiarity_is_not_truth        | B      | keep     | Illusory truth effect. Global.                                                                             |
| 44  | social_proof_skepticism         | B      | keep     | Bound 4 agents.                                                                                            |
| 45  | binary_resistance               | B      | keep     | Bound 4 agents.                                                                                            |
| 46  | emotion_as_signal               | B      | keep     | Global.                                                                                                    |
| 47  | metacognitive_pause             | A      | keep     | Global. Modes analyse+chat.                                                                                |
| 48  | belief_provenance               | A      | keep     | Global.                                                                                                    |
| 49  | assumption_surface              | A      | keep     | Bound 6 agents. Modes analyse+chat.                                                                        |
| 50  | steelman_first                  | A      | keep     | Bound 3 agents. Modes analyse+chat.                                                                        |
| 51  | hold_tension                    | A      | keep     | Bound 3 agents. Modes analyse+chat.                                                                        |
| 52  | understand_before_judge         | A      | keep     | Global.                                                                                                    |
| 53  | framing_awareness               | B      | keep     | Bound jean-michel + critical-thinker.                                                                      |
| 54  | narrative_immunity              | B      | keep     | Bound 5 agents.                                                                                            |
| 55  | urgency_check                   | B      | keep     | Anti-manipulation.                                                                                         |
| 56  | who_benefits                    | B      | keep     | Analyse de provenance.                                                                                     |
| 57  | sustained_attention             | A      | keep     | Bound 3 agents. Discipline focus.                                                                          |
| 58  | slogan_resistance               | A      | keep     | Anti-pensée-incantatoire. Global.                                                                          |
| 59  | slow_question_slow_answer       | A      | keep     | Modes analyse+chat.                                                                                        |
| 60  | reject_intellectual_laziness    | A      | keep     | Global. Posture.                                                                                           |
| 61  | dialogic_growth                 | A      | keep     | Réflexe ask_human positif.                                                                                 |
| 81  | no_overconfidence_in_results    | A      | keep     | Global. Métacog côté résultats externes.                                                                   |
| 82  | paraphrase_not_reword           | A      | keep     | Bound 5 agents. Discipline résumé.                                                                         |
| 83  | omit_unsourced_claims           | A      | keep     | Bound web-search. Discipline anti-hallu.                                                                   |
| 84  | memory_without_narration        | A      | rewrite  | Mentionne "summary.md" (archivist supprimé). Reformuler pour `messages[]` natif.                           |
| 85  | no_overfamiliarity_from_summary | A      | rewrite  | Idem 84. Reformuler.                                                                                       |
| 86  | seo_and_conspiracy_skepticism   | B      | keep     | Bound 4 agents.                                                                                            |
| 87  | resolve_source_conflicts        | B      | keep     | Bound 6 agents. Discipline cross-source.                                                                   |
| 109 | orchestrator_inquiry_loop       | F      | delete   | Mentionne `expected.completion_verb`, `validation_error` (concepts v2 absents). Anti-loop incantatoire.    |
| 110 | evidence_hierarchy              | B      | keep     | Hiérarchie scientifique des preuves.                                                                       |
| 111 | burden_of_proof                 | B      | keep     | Charge de la preuve.                                                                                       |
| 112 | occam_razor                     | B      | keep     | Parcimonie. Bound critical-thinker.                                                                        |
| 114 | research_return_format          | E      | rewrite  | Mentionne `report_findings` → devient `report_back` en v2. Reformuler.                                     |
| 118 | metacog_live_monitor            | F      | delete   | Mentionne `## Budget` block + SIGNAL formats v1. La v2 a son propre modèle d'events (§6 bis doc 06).       |
| 121 | router_synthesis_discipline     | E      | rewrite  | Mentionne `plan_update/return_to_user`. En v2, `return_to_user` est implicite + plan_update supprimé.      |

## Section : `process`

### Sous-catégorie : audit / sprint / execution

| ID  | code                                  | classe | décision  | justification                                                                                                  |
|-----|---------------------------------------|--------|-----------|----------------------------------------------------------------------------------------------------------------|
| 10  | audit_phase                           | E      | delete    | Orphelin (aucun binding). Code-tier paradigm sans usage observable.                                            |
| 11  | sprint_phase                          | E      | delete    | Orphelin.                                                                                                      |
| 12  | check_existing                        | E      | delete    | Orphelin.                                                                                                      |
| 13  | tool_error_retry                      | D      | keep      | Discipline retry sur erreur tool. Global. Compatible v2.                                                       |
| 36  | parse_briefing_first                  | D      | keep      | Bound 7 agents. Discipline initiale.                                                                           |
| 72  | substantive_response_first            | C      | keep      | Global. Anti-tergiversation.                                                                                   |
| 73  | answer_in_layers                      | C      | keep      | Bound 8 agents. Strategy de réponse.                                                                           |
| 74  | illustrate_with_examples              | C      | keep      | Bound 5 agents. Pédagogie.                                                                                     |
| 75  | assess_complexity_first               | F      | delete    | Mentionne `set_task_class` (supprimé v2). La classification est faite par le Tier 0 dispatcher.                |
| 120 | subresearch_inline                    | D      | edit      | Mentionne `report_findings` → `report_back`. Sinon discipline d'inlining d'ambigüité, utile.                  |

### Sous-catégorie : tool_discipline

| ID  | code                                       | classe | décision | justification                                                                                   |
|-----|--------------------------------------------|--------|----------|-------------------------------------------------------------------------------------------------|
| 76  | scale_tool_calls_to_complexity             | D      | keep     | Discipline généraliste. Compatible v2.                                                          |
| 77  | plan_before_complex_action                 | F      | rewrite  | Mentionne `manage_todo_list` (supprimé). Reformuler en "plan in thought + messages[] history". |
| 78  | fetch_referenced_resources                 | D      | keep     | Anti-hallu sur fichiers référencés. Global.                                                     |
| 79  | prefer_tool_over_parametric_for_volatile   | D      | keep     | Bound 5 agents. Généralise `weather_api_required`.                                              |
| 80  | no_permission_for_obvious_tools            | D      | keep     | Bound 7 agents. Anti-friction.                                                                  |
| 97  | delegate_not_direct_call                   | D      | keep     | Bound jean-michel. Pertinent v2 (delegate_to reste).                                            |

### Sous-catégorie : handoff

| ID  | code                          | classe | décision | justification                                                                                                                     |
|-----|-------------------------------|--------|----------|-----------------------------------------------------------------------------------------------------------------------------------|
| 14  | briefing_contract             | F      | rewrite  | Mentionne `step_budget_exhausted` (concept v2 supprimé) + bloc trop long. Garder uniquement la règle inter-agent en anglais.      |
| 98  | code_execution_routing        | F      | delete   | Force route vers code-runner. En v2, le main agent voit la `## Delegation targets` et décide. Anti-loop incantatoire.             |
| 102 | research_phase_routing        | F      | delete   | Définit l'ordre gather→critic→build. En v2 le main agent décide librement. Anti-loop incantatoire.                                |
| 103 | workspace_as_shared_memory    | D      | merge    | Discipline workspace + naming convention. **Merge into** nouveau `workspace_progressive_write` (§11 bis doc 06).                  |
| 104 | meta_analysis_routing         | F      | delete   | Force la délégation à meta-analyst. En v2 le main agent voit meta-analyst dans `## Delegation targets`.                           |

### Sous-catégorie : meteorology / encyclopedic / web_search

| ID  | code                                 | classe | décision | justification                                                                                             |
|-----|--------------------------------------|--------|----------|-----------------------------------------------------------------------------------------------------------|
| 22  | weather_api_required                 | D      | keep     | Bound weather-specialist.                                                                                 |
| 23  | weather_faithful_report              | D      | keep     | Bound weather-specialist.                                                                                 |
| 24  | wikipedia_source_only                | D      | keep     | Bound wikipedia-specialist.                                                                               |
| 25  | wikipedia_extract_focus              | D      | keep     | Bound wikipedia.                                                                                          |
| 26  | wikipedia_search_strategy            | D      | edit     | Mentionne "10 distinct search calls" — aligner avec `MAX_SEARCH_CALLS_PER_TURN` config v2 (=10, identique).|
| 106 | wikipedia_persist_before_delegate    | D      | merge    | **Merge into** `workspace_progressive_write` (nouveau, §11 bis). Discipline write-before-delegate.        |
| 107 | web_search_discipline                | D      | keep     | Bound web-search.                                                                                         |
| 108 | search_then_synthesize               | D      | edit     | Mentionne `report_findings` → `report_back`. Sinon discipline utile.                                      |
| 119 | searxng_query_craft                  | D      | keep     | Bound web-search. Technique mais nécessaire.                                                              |

### Sous-catégorie : comparison

| ID  | code                            | classe | décision | justification                                                                                                |
|-----|---------------------------------|--------|----------|--------------------------------------------------------------------------------------------------------------|
| 27  | comparison_routing              | F      | delete   | En v2, jean-michel voit `comparator-specialist` dans `## Delegation targets` et choisit librement. Le routing-of-routing (jean-michel décide où router puis comparator décide où router) est exactement le pattern v2 — pas besoin d'un paradigme qui le force. |
| 28  | comparison_research_first       | D      | keep     | Bound comparator-specialist.                                                                                 |
| 29  | comparison_data_only            | A      | keep     | Bound comparator. Anti-hallu.                                                                                |
| 30  | structured_verdict              | E      | keep     | Bound comparator. Format output.                                                                             |
| 123 | comparator_output_contract      | E      | edit     | Mentionne `report_findings` → `report_back`. Sinon contrat très précis (file naming + workspace), garder.    |

### Sous-catégorie : archival

| ID  | code                       | classe | décision | justification                                                |
|-----|----------------------------|--------|----------|--------------------------------------------------------------|
| 31  | archivist_format           | E      | delete   | Bound archivist. L'agent archivist disparaît en v2.          |
| 32  | archivist_tone             | E      | delete   | Bound archivist. Idem.                                       |
| 62  | critical_thinker_format    | E      | keep     | Bound critical-thinker. Critical-thinker reste un agent v2.  |

### Sous-catégorie : document_authoring / workspace_management / meta_analysis

| ID  | code                          | classe | décision | justification                                                                                                |
|-----|-------------------------------|--------|----------|--------------------------------------------------------------------------------------------------------------|
| 88  | document_workspace_output     | E      | edit     | Mentionne `report_findings`. Reformuler pour `report_back`.                                                  |
| 89  | structure_before_writing      | E      | keep     | Bound document-builder + meta-analyst.                                                                       |
| 90  | faithful_to_sources           | D      | keep     | Bound 3 agents.                                                                                              |
| 91  | workspace_tools_only          | D      | keep     | Bound 2 agents.                                                                                              |
| 92  | report_before_acting          | D      | edit     | Mentionne `report_findings.files_produced` → `report_back.files_produced`.                                   |
| 93  | disk_usage_precision          | D      | keep     | Bound workspace-manager.                                                                                     |
| 94  | inspect_before_proposing      | D      | keep     | Bound meta-analyst.                                                                                          |
| 95  | improvement_proposals_format  | E      | keep     | Bound meta-analyst.                                                                                          |
| 96  | no_self_modification          | D      | keep     | Bound meta-analyst. Safety boundary.                                                                         |
| 99  | verify_execution_output       | D      | keep     | Bound code-runner.                                                                                           |

### Sous-catégorie : planning

| ID  | code                  | classe | décision | justification                                                                                       |
|-----|-----------------------|--------|----------|-----------------------------------------------------------------------------------------------------|
| 124 | planning_with_todos   | F      | delete   | Tout entier basé sur `manage_todo_list` (supprimé v2). Mémoire native + délégation imbriquée le remplacent. |

## Section : `code`

| ID  | code                    | classe | décision | justification                                  |
|-----|-------------------------|--------|----------|------------------------------------------------|
| 15  | no_overengineering      | E      | delete   | Orphelin. Code paradigm sans binding.          |
| 16  | centralize_duplication  | E      | delete   | Orphelin.                                      |
| 17  | logical_anchoring       | E      | delete   | Orphelin.                                      |
| 18  | concise_comments        | E      | delete   | Orphelin.                                      |

## Section : `safety`

| ID  | code                       | classe | décision | justification                                                                                                       |
|-----|----------------------------|--------|----------|---------------------------------------------------------------------------------------------------------------------|
| 19  | mark_unverifiable          | A      | keep     | Hallucination guard. Global.                                                                                        |
| 20  | stay_in_role               | A      | keep     | Global. Aucune référence à un outil disparu.                                                                        |
| 21  | depth_aware                | A      | edit     | Mentionne "Hard limit is 10". Aligner sur `MAX_DEPTH=5` v2.                                                          |
| 100 | convergence_gate           | F      | delete   | Mentionne `signal_convergence` (supprimé v2). Anti-loop incantatoire. Remplacé par mécanique structurelle.          |
| 122 | source_admission_criteria  | A      | keep     | Anti-hallu sur listes d'entités. Bound 3 agents.                                                                    |

---

## Sous-blocs détaillés — paradigmes à `rewrite`

### ID 35 — `no_context_recap`

**Ancien content** (extrait) :
```
- A running summary is provided. Do not paraphrase or repeat what the user already knows.
- Address the new turn directly.
```

**Nouveau content proposé** :
```
- The conversation history is in your message context as previous turns,
  not a separate summary. Do not paraphrase or repeat what the user has
  already heard from you.
- Address the new turn directly.
```

### ID 77 — `plan_before_complex_action`

**Ancien content** (extrait) :
```
- For medium_task requests, draft a brief routing plan in your thought channel
  before acting [...]. Then externalise it with `manage_todo_list(operation="write")`...
- For deep_research requests, think through your research strategy before delegating
  [...]. Then call `manage_todo_list(operation="write")` to persist the plan...
```

**Nouveau content proposé** :
```
- For requests requiring multiple coordinated steps (research + critique + build,
  or multiple parallel delegations), draft a brief routing plan in your thought
  channel before acting: which agents, in what order, what each delivers.
- A plan you cannot articulate is a plan you do not have. If you cannot describe
  what each delegation adds, reconsider before delegating.
- After each delegation completes, evaluate the result. If there is a gap:
  follow up with a targeted sub-delegation, or proceed to synthesis if the gap
  is acceptable.
```

### ID 84 — `memory_without_narration`

**Ancien content** (extrait) :
```
- The conversation summary (summary.md) provides context from earlier turns.
  Use it as if you naturally remember it [...].
- Never use phrases like "I see in the summary…", "Looking at our previous turns…",
  "According to the running summary…".
```

**Nouveau content proposé** :
```
- The conversation history lives in your message context — previous turns are
  there as if you naturally remember them. Use them like a colleague recalling
  shared history.
- Never use phrases like "Looking at our previous turns…", "Earlier in our
  conversation…", "As you mentioned before…" — just surface the relevant fact.
- Surface the fact, not the mechanism that retrieved it.
```

### ID 85 — `no_overfamiliarity_from_summary`

**Ancien content** (extrait) :
```
- Having a conversation summary does not mean the user wants you to bring up
  everything in it.
```

**Nouveau content proposé** :
```
- Having conversation history in context does not mean the user wants you to
  bring up everything you remember.
- Apply only the elements of past turns directly relevant to the current
  question.
- Do not lead with personal references the user has not just brought up —
  that pattern feels intrusive even when the information is technically
  available.
```

### ID 114 — `research_return_format`

**Ancien content** (extrait) :
```
- The full findings [...] go in a workspace file. report_findings is a thin pointer
  with: summary [...], files_produced [...], confidence [...].
```

**Nouveau content proposé** :
```
- The full findings (sources, quotes, claims, citations) go in a workspace file.
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
  `summary` field — those belong in the workspace file.
```

### ID 121 — `router_synthesis_discipline`

**Ancien content** (extrait) :
```
- After a specialist returns via report_findings, decide: follow up with another
  delegation, or synthesize for the user.
- [...]
- When all necessary research is done, synthesize the results and call return_to_user.
- Never re-delegate the same question without narrowing the scope.
```

**Nouveau content proposé** :
```
- After a specialist returns via `report_back`, decide explicitly: follow up
  with another delegation, or synthesize the answer for the user directly.
- If the report includes sub_questions you want to follow up on, delegate to
  the appropriate agent.
- When all necessary research is done, produce the answer as an assistant
  message without further tool calls. The orchestrator detects this as the
  conversation's end point.
- Never re-delegate the same question without narrowing the scope.
```

### ID 14 — `briefing_contract`

**Ancien content** : long, ~18 lignes, mentionne `step_budget_exhausted` (v1).

**Nouveau content proposé** :
```
- A `delegate_to` call must include: a clear mission, the expected outcome,
  and any relevant `support_files` paths (workspace files written this turn).
- Briefings between agents are written in **English**. Do NOT include language
  instructions in a briefing — the receiving agent handles output language
  automatically.
- Translate all non-English common nouns (clothing, animals, food, concepts)
  to English in the briefing, with the original term in parentheses for
  traceability. Example: "boxer shorts (caleçon)". Proper nouns and specialised
  technical terms with no English equivalent may stay in the original language.
- Independent subtasks may be emitted as multiple `delegate_to` calls in the
  same turn.
```

### ID 88 — `document_workspace_output`

**Ancien content** : mentionne `report_findings`.

**Nouveau content proposé** :
```
- All produced documents MUST be written to workspace files via
  `workspace_create_file` (or `workspace_append` for progressive writes).
- Never paste document content directly into `report_back.summary`. The
  summary is 1-3 sentences pointing at the file; the file is the deliverable.
- Use `workspace_str_replace(relative_path, old_str, new_str)` to refine
  a document iteratively. Parameter names are EXACTLY `old_str` and `new_str`.
- Read every `support_file` listed in the briefing via `workspace_view`
  before writing anything.
```

### ID 92 — `report_before_acting`

**Ancien content** : mentionne `report_findings.files_produced`.

**Nouveau content proposé** :
```
- Before any write operation, state in your thought channel what will change:
  file path, operation type, expected outcome.
- Include the list of files written in `report_back.files_produced` so the
  parent has a clear audit trail.
- If the operation affects multiple files, enumerate them all before
  proceeding.
```

### ID 108 — `search_then_synthesize`

**Ancien content** : mentionne `report_findings`.

**Nouveau content proposé** :
```
- Each search should target a distinct sub-topic or angle. Do not repeat
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
  files_produced, confidence)`.
```

### ID 120 — `subresearch_inline` (edit, pas rewrite complet)

**Ancien content** : mention `report_findings` — remplacer par `report_back`.
Garder le reste tel quel.

### ID 26 — `wikipedia_search_strategy` (edit, pas rewrite complet)

**Ancien content** : mentionne "default: 10 distinct search calls across
wikipedia_search, wikipedia_get_page, wikipedia_fetch". Aligner avec
`MAX_SEARCH_CALLS_PER_TURN = 10` (identique mais expliciter que c'est
config-driven).

### ID 27 — `comparison_routing` (edit)

**Ancien content** : "do not delegate to a domain specialist directly" —
adoucir le MUST. La discipline reste utile en recommandation, mais le
main agent décide librement en v2.

**Nouveau content proposé** :
```
- When the human asks to compare, rank, or choose between two or more
  entities, prefer delegating to `comparator-specialist` rather than to
  domain specialists individually. The comparator handles the parallel
  data collection AND the synthesis.
- Delegate domain specialists directly only when the comparison is
  trivially aggregatable from their individual outputs (rare).
```

### ID 21 — `depth_aware` (edit)

**Ancien content** : "Hard limit is 10". Remplacer par "Hard limit is 5"
(cf. `MAX_DEPTH=5` config v2).

**Nouveau content proposé** :
```
- Your current recursion depth is shown in CONTEXT. Hard limit is 5.
- If you reach the limit, you must conclude with the information at hand
  and explicitly state that the recursion limit was reached.
```

### ID 123 — `comparator_output_contract` (edit)

**Ancien content** : mentionne `report_findings`. Remplacer par `report_back`.

---

## Sous-blocs détaillés — paradigmes à `merge`

### ID 103 + ID 106 → nouveau `workspace_progressive_write`

Les deux paradigmes existants couvrent des facettes complémentaires :

- 103 (`workspace_as_shared_memory`) : check before re-doing work, naming
  convention, file structure.
- 106 (`wikipedia_persist_before_delegate`) : write workspace before
  delegate.

Tous deux disparaissent au profit d'un seul paradigme positif et concis
détaillé dans la section "Paradigmes nouveaux à introduire" ci-dessous.

---

## Paradigmes nouveaux à introduire

Listés en `06_proposition_v2.md §11 bis`. Contenus rédigés :

### `user_memory_discipline` (cible : jean-michel)

```
- Save a user_memory entry when the human reveals a durable fact about
  themselves, their preferences, their projects, or their workflows.
- Update an existing entry when a previously saved fact is contradicted
  or refined by the conversation.
- Delete an entry that has become irrelevant (e.g. mention of an abandoned
  project, a corrected preference).
- Recall the full content of an entry when the current conversation
  references something that might be in memory.
- Keep entries concise: title under 60 chars, description under 150 chars,
  content under 1000 chars.
```

### `nested_delegation_discipline` (cible : tous agents avec `delegate_to`)

```
- The `delegate_to` tool descends the task tree — it never returns to a
  higher-level caller. If a sub-task you encounter exceeds your scope,
  delegate it yourself rather than passing it back up.
- The orchestrator enforces a maximum tree depth via `MAX_DEPTH`. Within
  that limit, descend freely if the sub-task warrants a dedicated specialist.
- Each subagent receives its own fresh context — it does not see your
  conversation history. Pass everything it needs in the briefing or via
  support_files.
- Do not delegate when you can solve the sub-task with a tool call. The
  cost of a delegation is a full new LLM context.
```

### `report_back_format` (cible : tous specialists)

```
- When concluding your work, call `report_back` with:
  - summary: 1-3 sentences naming the headline finding. Not "I did X" —
    the actual conclusion or the actual content of what you produced.
  - files_produced: the workspace files you wrote, relative to the
    workspace root.
  - confidence: "low" | "medium" | "high" — your self-assessment of how
    completely you delivered the briefing.
  - low_confidence_reason: REQUIRED if confidence is "low". One synthetic
    sentence explaining what's missing or uncertain. Not a recap of your
    reasoning — just the gap.
- Do not paste raw tool outputs into `summary`. Those belong in the
  workspace files.
```

### `workspace_progressive_write` (cible : tous specialists avec workspace write)

```
- Persist findings to the workspace as you go, not at the end. After
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
  same execution. The file must physically exist.
```

### `output_contract_no_inline_dump` (cible : jean-michel + finalizers)

```
- The reply to the human is prose, not a dump of tool results or
  delegation summaries.
- Quote sparingly from workspace files; prefer to construct an answer
  from the findings rather than paste them.
- If the deliverable is a document, reference the workspace file path
  in your reply and let the human read it directly — do not duplicate
  its content inline.
```

---

## Récapitulatif statistique

Recompté à partir des tableaux ci-dessus, après revue humaine du 2026-05-27 :

| Décision           | Compte | Pourcentage |
|--------------------|--------|-------------|
| `keep`             | 84     | 70.6 %      |
| `edit`             | 8      | 6.7 %       |
| `rewrite`          | 7      | 5.9 %       |
| `merge`            | 2      | 1.7 %       |
| `delete`           | 18     | 15.1 %      |
| **Total existant** | **119**| 100 %       |
| `new`              | +5     | (ajouts)    |
| **Total post-v2**  | **105**| 119 − 18 (delete) − 1 (merge net) + 5 (new) |

Liste exhaustive des 18 suppressions :

| ID  | code                       | Motif principal                                              |
|-----|----------------------------|--------------------------------------------------------------|
| 10  | audit_phase                | Orphelin (no binding)                                        |
| 11  | sprint_phase               | Orphelin                                                     |
| 12  | check_existing             | Orphelin                                                     |
| 15  | no_overengineering         | Orphelin (code paradigm)                                     |
| 16  | centralize_duplication     | Orphelin                                                     |
| 17  | logical_anchoring          | Orphelin                                                     |
| 18  | concise_comments           | Orphelin                                                     |
| 27  | comparison_routing         | Anti-loop incantatoire ; comparator visible dans `## Delegation targets`, le main agent choisit librement (validé revue humaine 2026-05-27) |
| 31  | archivist_format           | Agent archivist supprimé v2                                  |
| 32  | archivist_tone             | Agent archivist supprimé v2                                  |
| 75  | assess_complexity_first    | Mentionne `set_task_class` (outil mort) ; rôle pris par Tier 0 dispatcher |
| 98  | code_execution_routing     | Anti-loop incantatoire ; remplacé par `## Delegation targets` |
| 100 | convergence_gate           | Mentionne `signal_convergence` (outil mort)                  |
| 102 | research_phase_routing     | Anti-loop incantatoire ; main agent décide librement         |
| 104 | meta_analysis_routing      | Anti-loop incantatoire ; meta-analyst visible dans targets   |
| 109 | orchestrator_inquiry_loop  | Mentionne concepts v2 absents (`completion_verb`, `validation_error`) |
| 118 | metacog_live_monitor       | Modèle de budget v1 ; remplacé par events v2                 |
| 124 | planning_with_todos        | Tout entier basé sur `manage_todo_list` (outil mort)         |

Liste exhaustive des 2 merges :

| IDs source | code merged                       | Nouveau code cible                |
|-----------|-----------------------------------|-----------------------------------|
| 103       | workspace_as_shared_memory        | → `workspace_progressive_write`   |
| 106       | wikipedia_persist_before_delegate | → `workspace_progressive_write`   |

## Plan de la migration SQL

La migration `migrate_100_paradigm_realignment.sql` sera structurée comme
suit (une seule transaction, idempotente) :

```sql
BEGIN TRANSACTION;

-- 1. DELETE des paradigmes supprimés (25 entrées)
DELETE FROM agent_paradigms WHERE paradigm_id IN (
  10, 11, 12, 15, 16, 17, 18,         -- orphelins
  31, 32,                              -- archivist
  75, 98, 100, 102, 104, 109, 118, 124, -- anti-loop / outils morts
  -- + complément après revue : 27 ? 88 ? selon rewrites
  ...
);
DELETE FROM paradigms WHERE id IN (-- mêmes IDs)
  ...;

-- 2. UPDATE des paradigmes réécrits (8 entrées)
UPDATE paradigms SET content = '...' WHERE id = 14;  -- briefing_contract
UPDATE paradigms SET content = '...' WHERE id = 35;  -- no_context_recap
UPDATE paradigms SET content = '...' WHERE id = 77;  -- plan_before_complex_action
UPDATE paradigms SET content = '...' WHERE id = 84;  -- memory_without_narration
UPDATE paradigms SET content = '...' WHERE id = 85;  -- no_overfamiliarity_from_summary
UPDATE paradigms SET content = '...' WHERE id = 88;  -- document_workspace_output
UPDATE paradigms SET content = '...' WHERE id = 92;  -- report_before_acting
UPDATE paradigms SET content = '...' WHERE id = 108; -- search_then_synthesize
UPDATE paradigms SET content = '...' WHERE id = 114; -- research_return_format
UPDATE paradigms SET content = '...' WHERE id = 121; -- router_synthesis_discipline

-- 3. UPDATE des paradigmes à édit léger (6 entrées : 14 vu ci-dessus ; 21, 26, 27, 120, 123)
UPDATE paradigms SET content = '...' WHERE id = 21;  -- depth_aware (5 au lieu de 10)
UPDATE paradigms SET content = '...' WHERE id = 26;  -- wikipedia_search_strategy
UPDATE paradigms SET content = REPLACE(content, 'report_findings', 'report_back') WHERE id IN (120, 123);

-- 4. INSERT des nouveaux paradigmes (5 entrées + 1 nouvelle catégorie pour user_memory)
INSERT INTO categories (...) VALUES (..., 'user_memory', 'User memory', ...);
INSERT INTO paradigms (id, category_id, code, title, content, ...) VALUES
  (NEXT_ID,   ..., 'user_memory_discipline',           'User memory discipline', '...', ...),
  (NEXT_ID+1, ..., 'nested_delegation_discipline',      'Nested delegation discipline', '...', ...),
  (NEXT_ID+2, ..., 'report_back_format',                'report_back format', '...', ...),
  (NEXT_ID+3, ..., 'workspace_progressive_write',       'Workspace progressive write', '...', ...),
  (NEXT_ID+4, ..., 'output_contract_no_inline_dump',    'Output contract: no inline dump', '...', ...);

-- 5. INSERT INTO agent_paradigms pour binder les nouveaux paradigmes
INSERT INTO agent_paradigms (agent_id, paradigm_id) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'),   (SELECT id FROM paradigms WHERE code='user_memory_discipline')),
  ...
;

-- 6. INSERT pour grant manage_user_memory à jean-michel
INSERT INTO agent_tools (agent_id, tool_code) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'), 'manage_user_memory');

-- 7. Marquer l'agent archivist comme inactif (suppression au prochain cycle)
UPDATE agents SET active = 0 WHERE code = 'archivist';

COMMIT;
```

Notes :
- La migration est idempotente : chaque DELETE/UPDATE/INSERT est
  conditionnel (`WHERE id IN ...`, ou `INSERT OR IGNORE`).
- Les IDs des nouveaux paradigmes sont attribués dynamiquement (max+1) —
  pas hardcodés pour éviter les collisions inter-environnements.
- L'archivist est désactivé (`active=0`), pas supprimé : DELETE en
  cascade casserait les FKs vers `conversations.archivist_request_id`
  si elles existent. La purge dure sera faite en Phase 8.

## Points en attente de revue humaine — résolus

Réponses reçues le 2026-05-27 :

1. **ID 27 (`comparison_routing`)** : **DELETE**. En v2, jean-michel
   ne fait plus de routing-of-routing directement — il voit
   `comparator-specialist` dans `## Delegation targets` et choisit.
   Le paradigme n'a plus de valeur d'orientation forte.
2. **IDs 22-26 (weather + wikipedia)** : **garde en l'état**. Simplicité.
3. **ID 76 (`scale_tool_calls_to_complexity`)** : **garde en l'état**,
   *sauf* si on peut injecter les valeurs comme variables. Voir
   "Future enhancement: template variables dans paradigmes" ci-dessous.
4. **IDs 88, 92** : **edit OK** pour remplacer `report_findings` par
   `report_back` (l'outil v2). Pas d'autre changement.
5. **ID 121 (`router_synthesis_discipline`)** : la question soulève le
   comportement de `ask_human` et `return_to_user` en v2. Analyse
   dans la section suivante.

## Analyse — `ask_human` et `return_to_user` dans la v2

Question soulevée par la revue du point 5 (paradigme 121). Le doc 06
mentionne `ask_human` çà et là mais ne formalise pas son comportement,
et `return_to_user` est implicite sans que la rationale soit dans le doc.

### `return_to_user` — implicite, confirmé

Comportement v2 retenu : le LLM signale la fin en émettant un assistant
turn **sans tool_calls**. Le `content` de ce turn EST la réponse à
l'utilisateur. La boucle se termine.

Argumentaire :
- **Pro implicit (retenu)** : protocole plus simple, surface API plus
  petite, format flexible (markdown, JSON, prose), aligné avec Claude
  Code et OpenAI tool-calling.
- **Pro explicit (rejeté)** : aurait permis un format contrôlé via
  schéma JSON, symétrie avec `report_back`. Mais ces bénéfices sont
  obtenables par d'autres moyens (system prompt qui demande JSON, ou
  hook qui valide le format).

Asymétrie avec subagent : le main agent termine implicite, le subagent
termine explicite via `report_back`. Justifié par la différence
d'audience (humain prose vs caller structured).

### `ask_human` — main agent uniquement, confirmé

Comportement v2 retenu : `ask_human` est un tool **uniquement disponible
au main agent (jean-michel)**. Les subagents n'ont pas ce tool.

Mécanique :
- Main agent émet `ask_human(question, why)`.
- Le hook `PreToolUse` autorise (uniquement pour main agent).
- L'orchestrateur pause la boucle, surface la question via callback CLI.
- La réponse humaine est appendée au `messages[]` comme `role=user`
  (et non pas comme `role=tool` — la réponse humaine est une
  contribution naturelle au dialogue).
- La boucle reprend.

Pourquoi pas pour les subagents :
- Casse l'isolation subagent (cf. §5 doc 06).
- Mauvaise UX : l'humain reçoit des questions de specialists divers
  sans contexte clair sur quel agent demande.
- Solution alternative propre : un subagent qui a besoin de
  clarification fait `report_back(confidence="low",
  low_confidence_reason="missing X parameter Y")`. Le main agent voit
  le retour, décide d'appeler `ask_human` à son tour ou de procéder
  avec ce qu'il a.

Coût : un round-trip LLM supplémentaire dans le rare cas où un
subagent bloque sur clarification. Acceptable.

### Implications sur la migration des paradigmes

- **ID 4 (`one_question_at_a_time`)** : reclassé `edit`. Contenu garde,
  mais binding restreint à `jean-michel` seul. Les bindings sur les 8
  autres agents (summarizer, weather-specialist, wikipedia-specialist,
  comparator-specialist, document-builder, workspace-manager,
  meta-analyst, code-runner) sont retirés dans la migration.
- **ID 5 (`trust_context_defaults`)** : déjà bound jean-michel seul,
  pas d'impact.
- **ID 68 (`address_then_clarify`)** : bound à 11 agents incluant
  subagent-types. Le paradigme parle d'"asking for clarification on
  what remained unclear" — en v2, pour les subagents, ça devient
  `report_back(confidence=low)`. Le paradigme reste applicable
  mais le mot "clarification" doit être nuancé. À reclasser **edit** ?
  → décision : laisser `keep` pour ne pas multiplier les rewrites ;
  les subagents n'ayant plus `ask_human`, la consigne s'applique
  naturellement à leur output structure.

### Implications sur le doc 06

Mise à jour proposée :
- **§4 Tier 1 tools** : ajouter `ask_human` à la liste des tools du
  main agent (n'est pas mentionné aujourd'hui — oubli).
- **§5 Tier 2 subagent** : ajouter une phrase explicite que `ask_human`
  n'est PAS dans le payload du subagent. Si besoin de clarification,
  `report_back` avec `confidence=low`.

Ces deux ajouts dans 06 sont faits en parallèle de la finalisation
de cette migration.

## Future enhancement : template variables dans paradigmes

Suite à la question sur ID 76 : actuellement le `content` d'un paradigme
est injecté verbatim dans le system prompt. Pour permettre l'injection
de variables (`MAX_SEARCH_CALLS_PER_TURN`, `MAX_DEPTH`, etc.) sans
hardcoder, on pourrait introduire un mécanisme de templating simple :

- Marqueur `${CONFIG.MAX_SEARCH_CALLS_PER_TURN}` dans le `content`.
- Au render du system prompt, `prompts.py` substitue les marqueurs par
  les valeurs courantes de `config.py`.
- Compatible avec une grammar restreinte (juste des constantes config,
  pas d'expressions arbitraires).

Cette enhancement n'est pas Phase 0. À reprendre en phase post-v2 si
on observe que des paradigmes dérivent face aux changements de config.
Pour la migration 100, on garde les valeurs hardcodées telles quelles
dans les `content`, en sachant qu'on devra les retoucher si on change
les seuils.

## Prochaine étape

Une fois ce tableau relu et validé :

1. Génération du fichier SQL final
   `db/migrations/migrate_100_paradigm_realignment.sql` à partir des
   décisions ci-dessus (sans aucun `...` placeholder — version
   exécutable).
2. Tests de validation : appliquer la migration sur une copie de
   `jeanmichel.db`, vérifier que `SELECT * FROM agent_paradigms` n'a
   plus de référence orpheline.
3. Phase 1 démarre en parallèle (foundation LLMClient + events.py + persistence).

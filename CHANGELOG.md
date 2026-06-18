# Changelog

Historique des livraisons notables. Le [README](README.md) et les [docs](docs/) décrivent le système **au
présent** ; ici vit ce qui a été **fait et quand**. Détail commit-par-commit : `git log`. Détail exhaustif des
migrations DB : `db/migrations/`.

## Batch « référent & plan/todo » (branche `multi_stage_plan_todo`)
- **Référent organisationnel** — `state.json` devient le ledger autoritaire d'une conversation (tours, plans,
  todos, subagents, fichiers, `phase`, `plan_mode`) : rechargé en début de tour, filet anti-drift
  `rebuild_from_events`, poussé live à l'UI via l'event WS `ReferentSnapshot`.
- **Plan & todo découplés + multiplicité** — plan riche par-id (`workspace/plan_<id>.md`, lu à la demande, non
  réinjecté), todo terse (`todo.json`, re-surfacé `[TODO-RECAP]`) ; en mode plan, halte déterministe après
  écriture ; un re-plan supersède le précédent (historique `GET /plans` + dialog UI).
- **Lignée de fork** — `conversations.parent_conv_id` + `parent_commit` (migrate_151), exposée dans l'UI.
- **Stop interruptible** — ferme la connexion Ollama et annule pendant les tool calls ; garde-fou sans-progrès
  qui fait conclure la boucle seule.
- **Rendu LaTeX** du chat en KaTeX (`katex` direct).
- **Mémoire** — consolidation shadow découplée du tour (daemon de réflexion sleep-time), gating de fréquence,
  persistance reload des candidats (`pending-memory`).
- **Ménage docs** — tri de `docs/` (plans livrés & reliques v1 dégagés), sauvetage des specs vivants de
  `DevNotes/` vers `docs/` puis suppression du dossier, dump LLM global coupé par défaut.

## Refonte v2 (« revolucion »)
- **Bascule v1 → v2** en 8 phases : dispatcher Tier 0 (granite) / orchestrateur Tier 1 (cogito) / subagents
  Tier 2, boucle Python + hooks déterministes, `messages[]` natif Ollama, compaction 4 niveaux, budget
  partitionné system/working/output.
- **Paradigmes** : 119 (v1) → 104 (purge des incantatoires + outils morts, migrate_100) → 118 (enrichissement
  spécialistes, migrate_103→111). Strictement model-agnostic, en DB.
- **Frontal web** : API FastAPI multi-utilisateur, streaming WebSocket des events, SPA Vue 3 / Vuetify (chat,
  conversations, workspace, mémoire, profil, TTS navigateur) ; front conteneurisé (nginx), daemon sur l'hôte.
- **Capacités image** (migrate_115→119) : `image_search`, `analyze_image` → gemma4 multimodal (image ⇒ DEEP
  forcé, pas de base64 persisté), affichage front (grille + miniatures + lightbox).
- **Mode code / intervention repo** (migrate_120→123) : décomposition en TODO + boucle PDCA déléguant à des
  workers `qwen3-coder` (py / node) ; intervention sur un vrai repo en worktree git isolé ; renforts tirés du
  fork Claude Code (retry sans thinking, reaper de sandbox, tools fichiers workspace, worker node).
- **Mémoire scopée** (migrate_124→126) : scopes world/user/project/tool, FTS5 + BM25, projets, consolidation shadow.

## Migrations DB (détail v1 → v2)
> `db/schema.sql` = état v2 consolidé (fresh installs) ; `db/schema_v1_baseline.sql` = baseline rejoué par les
> tests de migration.

- `migrate_100_paradigm_realignment.sql` — purge des paradigmes obsolètes + 5 nouveaux + grant
  `manage_user_memory` à `jean-michel` + désactivation `archivist`.
- `migrate_101_user_memory.sql` — table `user_memory`.
- `migrate_102_drop_runtime_tables.sql` — drop `requests`/`artifacts`/`conversation_phases`/`sandbox_executions`
  + colonne `agents.model_override` + suppression définitive `archivist`.
- `migrate_103_search_quality.sql` — 4 paradigmes qualité de recherche (`breadth_before_depth`,
  `wikipedia_lateral_exploration`, `coverage_check`, `parallel_specialists_for_inventory`).
- `migrate_104_drop_conv_read_file.sql` — suppression des grants `conv_read_file` (redondant avec `workspace_view`).
- `migrate_105_strategist_agent.sql` — agent `strategist` (reasoner décomposition), model_override `gemma4:26b`
  sur les 4 reasoners, retour de jean-michel sur `MAIN_MODEL`.
- `migrate_106_news_specialist.sql` — agent `news-specialist`, tools `news_latest`/`news_archive`, paradigme
  `news_freshness_discipline`.
- `migrate_107_news_routing_and_web_fetch.sql` — fix routing news, paradigme `news_first_for_news_briefs`,
  grants `web_fetch` à news-specialist + web-search-specialist.
- `migrate_108_code_fetcher_agent.sql` — agent `code-fetcher` (GitHub + Stack Overflow + PyPI + web_fetch),
  paradigmes `delegate_to_code_fetcher_on_doubt` + `cite_sources_in_user_facing_output`.
- `migrate_109_code_runner_routing_and_sandbox.sql` — routing code-runner, paradigmes
  `code_runner_for_code_production_briefs` + `test_in_sandbox_when_runnable` (exécution sandbox avant report_back).
- `migrate_110_syntax_check_before_run.sql` — étape syntax check rapide avant l'exécution complète (par langage).
- `migrate_111_code_runner_to_reasoner.sql` — `code-runner` sur `gemma4:26b` (la production de code = raisonnement).
- `migrate_112_web_users.sql` — multi-utilisateur web : tables `web_users` + `conversation_users`.
- `migrate_113_user_memory_isolation.sql` — `user_memory.user_id`, CRUD filtré par utilisateur (plus de fuite cross-user).
- `migrate_114_conversation_cascade.sql` — `ON DELETE CASCADE` sur les FK (suppression propre d'une conversation).
- `migrate_115`→`119` — **capacités image** : `image_search` (115), `analyze_image` (116), routing affichage +
  DEEP forcé (117), paradigmes en anglais (118), cap résultats image (119).
- `migrate_120`→`123` — **orchestrateur codeur** : infra TODO + PDCA + `code-runner` re-routé (120), mode `code`
  + `CODE_MODEL` (121), workspace file ops (122), agent `code-runner-node` (node-alpine, 123).
- `migrate_124`→`126` — **mémoire scopée + projets + consolidation** : `projects` + `conversations.project_id`
  (124) ; `user_memory` → `memory` avec `scope`, FTS5 + BM25 (125) ; `manage_user_memory` → `manage_memory` +
  paradigmes `memory_discipline`/`tool_note_discipline` (126).
- `migrate_151_conversation_lineage.sql` — lignée de fork (`parent_conv_id` + `parent_commit`).

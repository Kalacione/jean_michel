# Changelog

Historique des livraisons notables. Le [README](README.md) et les [docs](docs/) décrivent le système **au
présent** ; ici vit ce qui a été **fait et quand**. Détail commit-par-commit : `git log`.

## Batch « référent & plan/todo » (branche `multi_stage_plan_todo`)
- **Référent organisationnel** — `state.json` devient le ledger autoritaire d'une conversation (tours, plans,
  todos, subagents, fichiers, `phase`, `plan_mode`) : rechargé en début de tour, filet anti-drift
  `rebuild_from_events`, poussé live à l'UI via l'event WS `ReferentSnapshot`.
- **Plan & todo découplés + multiplicité** — plan riche par-id (`workspace/plan_<id>.md`, lu à la demande, non
  réinjecté), todo terse (`todo.json`, re-surfacé `[TODO-RECAP]`) ; en mode plan, halte déterministe après
  écriture ; un re-plan supersède le précédent (historique `GET /plans` + dialog UI).
- **Lignée de fork** — `conversations.parent_conv_id` + `parent_commit`, exposée dans l'UI.
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
- **Paradigmes** : 119 (v1) → 104 (purge des incantatoires + outils morts) → 118 (enrichissement
  spécialistes). Strictement model-agnostic, en DB.
- **Frontal web** : API FastAPI multi-utilisateur, streaming WebSocket des events, SPA Vue 3 / Vuetify (chat,
  conversations, workspace, mémoire, profil, TTS navigateur) ; front conteneurisé (nginx), daemon sur l'hôte.
- **Capacités image** : `image_search`, `analyze_image` → gemma4 multimodal (image ⇒ DEEP
  forcé, pas de base64 persisté), affichage front (grille + miniatures + lightbox).
- **Mode code / intervention repo** : décomposition en TODO + boucle PDCA déléguant à des
  workers `qwen3-coder` (py / node) ; intervention sur un vrai repo en worktree git isolé ; renforts tirés du
  fork Claude Code (retry sans thinking, reaper de sandbox, tools fichiers workspace, worker node).
- **Mémoire scopée** : scopes world/user/project/tool, FTS5 + BM25, projets, consolidation shadow.

## Détail de la bascule v1 → v2
> Les étapes qui ont construit l'état v2 (résumé ci-dessus) ; `db/schema.sql` en est la baseline consolidée.

- **Réalignement des paradigmes** — purge des paradigmes obsolètes + 5 nouveaux + grant `manage_user_memory`
  à `jean-michel` + désactivation `archivist`.
- **Mémoire utilisateur** — table `user_memory`.
- **Drop des tables runtime v1** — `requests`/`artifacts`/`conversation_phases`/`sandbox_executions`
  + colonne `agents.model_override` + suppression définitive `archivist`.
- **Qualité de recherche** — 4 paradigmes (`breadth_before_depth`, `wikipedia_lateral_exploration`,
  `coverage_check`, `parallel_specialists_for_inventory`).
- **Retrait de `conv_read_file`** — grants supprimés (redondant avec `workspace_view`).
- **Agent `strategist`** — reasoner de décomposition, model_override `gemma4:26b` sur les 4 reasoners,
  retour de jean-michel sur `MAIN_MODEL`.
- **Agent `news-specialist`** — tools `news_latest`/`news_archive`, paradigme `news_freshness_discipline`.
- **Routing news + `web_fetch`** — fix routing news, paradigme `news_first_for_news_briefs`, grants `web_fetch`
  à news-specialist + web-search-specialist.
- **Agent `code-fetcher`** — GitHub + Stack Overflow + PyPI + web_fetch ; paradigmes
  `delegate_to_code_fetcher_on_doubt` + `cite_sources_in_user_facing_output`.
- **Routing code-runner + sandbox** — paradigmes `code_runner_for_code_production_briefs`
  + `test_in_sandbox_when_runnable` (exécution sandbox avant report_back).
- **Syntax check avant exécution** — étape rapide par langage avant l'exécution complète.
- **`code-runner` sur reasoner** — `gemma4:26b` (la production de code = raisonnement).
- **Multi-utilisateur web** — tables `web_users` + `conversation_users`.
- **Isolation mémoire par user** — `user_memory.user_id`, CRUD filtré par utilisateur (plus de fuite cross-user).
- **Cascade de suppression** — `ON DELETE CASCADE` sur les FK (suppression propre d'une conversation).
- **Capacités image** — `image_search`, `analyze_image`, routing affichage + DEEP forcé, paradigmes en anglais,
  cap résultats image.
- **Orchestrateur codeur** — infra TODO + PDCA + `code-runner` re-routé, mode `code` + `CODE_MODEL`, workspace
  file ops, agent `code-runner-node` (node-alpine).
- **Mémoire scopée + projets + consolidation** — `projects` + `conversations.project_id` ; `user_memory` →
  `memory` avec `scope`, FTS5 + BM25 ; `manage_user_memory` → `manage_memory` + paradigmes
  `memory_discipline`/`tool_note_discipline`.

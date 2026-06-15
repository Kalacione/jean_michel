# Jean-Michel

Assistant IA local à agents spécialisés, orchestration Python déterministe avec
hooks, et boucle multi-turn native Ollama. **Version 2** — CLI Rich pour
l'usage local mono-utilisateur, **API daemon FastAPI** + **frontal web Vue 3 /
Vuetify** pour l'usage multi-utilisateur. Les deux surfaces partagent le même
orchestrateur, les mêmes agents et la même BDD.

## Concept

Une requête humaine entre par le **Tier 0 — Dispatcher** (un petit LLM rapide,
`granite4.1:8b` par défaut, sans thinking, JSON forcé). Il classifie en
**ALEXA** (un seul tool suffit, exécution directe) ou **DEEP** (engagement du
Tier 1).

- **ALEXA** : un tool natif (clock / weather / wikipedia_search) est invoqué,
  le résultat est formaté en français/anglais selon la langue détectée, fin.
- **DEEP** : le **Tier 1 — Main agent** (rôle `main`, `cogito:32b` par défaut,
  multi-turn natif) prend la main avec son propre `messages[]` accumulé. Il
  peut appeler des tools natifs, ou déléguer à un spécialiste via
  `delegate_to` — qui spawne un **Tier 2 — Subagent** avec son propre
  `messages[]` frais, jusqu'à `MAX_DEPTH=5` niveaux de délégation imbriquée.

Quand un subagent conclut, il appelle `report_back(summary, files_produced,
confidence, low_confidence_reason?)`. Le main agent termine en émettant un
turn assistant **sans tool_calls** : le `content` EST la réponse.

## Architecture en 3 lignes

1. **L'orchestrateur Python est sans état interne pour la décision.** Une
   boucle minimale + 4 hooks (`PreLLMCall`, `PreToolUse`, `PostToolUse`,
   `OnDelegateReturn`). L'état conversationnel vit dans `messages[]`
   (côté LLM) + `state.json` (compteurs côté Python).
2. **Le LLM voit son histoire complète** via le format `messages[]` natif
   Ollama (system / user / assistant / tool). Pas de récap reconstruit.
3. **Toute exigence déterministe est un hook Python**, pas une consigne
   prompt. Les paradigmes en BDD restent pour le style et la métacognition,
   pas pour piloter le flux.

Voir `DevNotes/REVOLUCION/06_proposition_v2.md` pour le détail
architectural complet.

## Surfaces

Le même cœur (orchestrateur Python + agents + BDD SQLite) sert **deux
surfaces utilisateur** parallèles :

| Surface | Comment | Public | Mémoire utilisateur |
|---|---|---|---|
| **CLI** (`./jm.sh`) | terminal Rich, prompt_toolkit, modes `analyse`/`chat`/`vocal` | mono-utilisateur local | profil dans `cli_profile.toml`, user `cli` en BDD |
| **Web** (`./jm.sh --serve` + frontend Vite/Vuetify) | API REST + WebSocket → Vue 3, multi-comptes auth | multi-utilisateur LAN | un compte par user (`web_users` en BDD), `user_memory` isolée par user_id |

Aucune duplication : la couche **service** (`src/jeanmichel/service/`)
contient la logique métier (conversation lifecycle, user_memory CRUD,
workspace, turn execution streaming), appelée à la fois par le CLI et par
l'API. Le CLI reste 100 % fonctionnel pour les usages locaux où on ne
veut pas lancer un daemon.

## Agents v2 — le mille-feuille cognitif

16 agents actifs, organisés en **deux dimensions** :

**1. Dimension structurelle (place dans le task tree)** — exposée par
`agents.role` :

- **`router`** (1) : `jean-michel` — main agent Tier 1. Reçoit la requête,
  formalise, délègue, synthétise. **Ne raisonne pas, ne fait pas le boulot.**
- **`specialist`** (13) — exécutent les tâches concrètes, terminent par
  `report_back`. Peuvent eux-mêmes spawn d'autres specialists
  (`MAX_DEPTH=5`).
- **`finalizer`** (1) : `synthesizer` — fusion finale quand plusieurs
  specialists ont contribué. Termine par un turn assistant sans tool_calls.

**2. Dimension cognitive (intensité de raisonnement requise)** — pas une
colonne SQL formelle, mais une convention reflétée dans
`agents.model_override` :

| Tier cognitif | Agents | Modèle |
|---|---|---|
| **I/O & lookup** | `weather-specialist`, `wikipedia-specialist`, `web-search-specialist`, `news-specialist`, `code-fetcher`, `workspace-manager` | rôle `subagent` (`gemma4:26b`) |
| **Synthèse / format** | `summarizer`, `document-builder`, `synthesizer` (finalizer) | rôle `subagent` (`gemma4:26b`) |
| **Reasoners** | `strategist`, `critical-thinker`, `comparator-specialist`, `meta-analyst` | `gemma4:26b` via `model_override` |
| **Code (workers)** | `code-runner`, `code-runner-node` | `qwen3-coder:latest` via `model_override` |

**Pattern fetcher/runner pour le code** : `code-fetcher` fait du lookup
(GitHub, Stack Overflow, PyPI + web_fetch sur les URLs) ; `code-runner`
(sandbox `py-alpine`) et `code-runner-node` (sandbox `node-alpine`)
écrivent et exécutent du code dans le sandbox Docker. Quand un runner coince
sur une erreur ou doute d'une API, il délègue à code-fetcher avant de
deviner — pattern miroir de `news-specialist` + `web_fetch`. Les workers
code tournent sur `qwen3-coder` (thinking off — ce modèle n'a pas de canal
de réflexion), pilotés par l'orchestrateur `qwen3:14b` en mode `code`.

Les **reasoners** sont des specialists dont le métier *EST* le raisonnement —
ils sont sur un modèle plus capable parce que c'est leur raison d'être, pas
un workaround. `strategist` notamment décompose une requête exploratoire
ouverte en N axes thématiques disjoints et retourne un *plan* que le
router exécute en parallèle.

**Règle d'or** : si tu as envie de mettre du `model_override` sur le router
parce qu'il "doit faire X de plus", arrête — c'est un signal qu'il faut un
nouveau specialist dont c'est le métier.

## Modes

Choisi au démarrage via `--mode {analyse,chat,vocal,code}` (défaut `analyse`).

- **`analyse`** — CLI persistante entre questions, pas de follow-ups.
- **`chat`** — conversation continue, follow-ups proposés par jean-michel.
- **`code`** — mode orchestrateur codeur. Le main agent passe sur
  `qwen3:14b` (modèle plus robuste pour raisonner sur du code, via
  `MODE_ROUTER_MODEL`), décompose la demande en un **TODO plat** (`todo_write`,
  un seul item `in_progress` à la fois) et délègue aux workers code
  (`code-runner` / `code-runner-node` sur `qwen3-coder`, `code-fetcher` pour
  le lookup) selon une boucle **PDCA** : Plan (todo) → Do (délégation) →
  Check (`report_back`, dont `suggested_todo_updates` proposés par les
  workers) → Act (réécriture du TODO par l'orchestrateur). Le TODO est
  ré-injecté dans le prompt à chaque tour par `PreLLMCall`. Voir
  [DevNotes/ORCHESTRATOR/01_audit_decomposition_todo.md](DevNotes/ORCHESTRATOR/01_audit_decomposition_todo.md).
  Les autres modes utilisent le rôle `main` (`cogito:32b`).
- **`vocal`** — réponses concises (< 4 phrases courtes), paradigme
  `concise_output` activé. Le texte est aussi synthétisé via **Piper TTS**
  (modèle ONNX local) puis joué via `paplay` / `aplay` / `ffplay`. Voir
  [voice_models/README.md](voice_models/README.md) pour les modèles, les
  **prérequis système** (groupe `audio`, vérifié automatiquement par
  `./jm.sh --install`) et `JEANMICHEL_VOICE_MODEL` dans `.env.example`
  pour la config.
  Pendant que le LLM travaille, des **annonces vocales asynchrones**
  ("Je cherche sur internet", "Je consulte Wikipédia"…) sont émises à
  chaque délégation et à chaque appel d'outil de recherche par le
  router — sans bloquer l'orchestrateur (`subprocess.Popen`,
  skip-if-busy).

Les modes `chat` et `vocal` sont **continus** : le dispatcher Tier 0 voit
les 4 derniers tours user/assistant pour résoudre les follow-ups
("et pour demain ?" → toujours météo, location précédente conservée).
Le mode `analyse` reste standalone par défaut (chaque question est
traitée isolée par le dispatcher).

Le mode est porté par la conversation et apparaît dans le bloc
`## Conversation` du prompt système. Les paradigmes peuvent être restreints
à un mode via la table `paradigm_modes`.

**TTS sanitization** : avant d'être envoyée à Piper en mode vocal, la
réponse passe par un nettoyage Markdown (drop des blocs code, listes
remises à plat, liens → texte seul, ponctuations adoucies pour la
prosodie). La voix lit du texte naturel, pas du markdown brut.

## API daemon (`./jm.sh --serve`)

Daemon FastAPI lancé à la main sur l'hôte, point d'entrée
`jean-michel-serve`. Bind par défaut `0.0.0.0:8000` (override via
`JEANMICHEL_API_HOST` / `JEANMICHEL_API_PORT`). Auth par **bearer token
signé** (`itsdangerous`), mot de passe stocké en `argon2`. Le daemon
n'est PAS conteneurisé — il vit sur l'hôte pour avoir accès direct à
Ollama, au sandbox Docker et au modèle Piper.

Surface REST (sélection — détails dans `src/jeanmichel/api/app.py`) :

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/auth/login` | bearer token contre username + password |
| `GET`/`POST` | `/api/conversations` | liste / création (user-scoped) |
| `GET`/`PATCH`/`DELETE` | `/api/conversations/{id}` | détails / rename / suppression cascade |
| `GET` | `/api/conversations/{id}/{messages,events,state}` | lecture des artefacts |
| `GET`/`POST` | `/api/conversations/{id}/workspace[/file,upload,download,zip]` | inspection + upload + download (zip ou fichier unitaire) |
| `GET`/`POST`/`PATCH`/`DELETE` | `/api/memory[/{scope}/{code}]` + `/api/memory/search` | CRUD + FTS de la mémoire scopée |
| `GET`/`POST`/`PATCH`/`DELETE` | `/api/projects[/{id}]` | CRUD projets (par user) |
| `GET`/`PATCH` | `/api/profile` | profil du compte web courant |
| `GET` | `/api/tts` | synthèse Piper streamée (consommée comme blob côté front) |
| `WebSocket` | `/ws/conversations/{id}` | tour d'orchestration streamé live (events typés en JSON) |
| `WebSocket` | `/ws/notifications` | canal push par-user (ex. fin de build de l'image sandbox d'un projet → toast) |

Toutes les routes (sauf `/api/health` et `/api/auth/login`) passent par
`require_conversation_owner` ou `current_user` : un user ne peut JAMAIS
voir les conversations / la mémoire / le workspace d'un autre. Anti-traversal
via `safe_resolve` sur tous les paths workspace.

Voir [DevNotes/WEBUI/01_audit_api_async_webui.md](DevNotes/WEBUI/01_audit_api_async_webui.md)
pour le cadrage et [DevNotes/WEBUI/02_audit_user_memory_isolation.md](DevNotes/WEBUI/02_audit_user_memory_isolation.md)
pour le passage du `user_memory` global à l'isolation par utilisateur.

## Frontal web (Vue 3 + Vuetify)

Le dossier [web/](web/) est une SPA Vite + Vue 3 + Vuetify 4 + Pinia.
Buildée puis servie par nginx dans un container Docker — c'est la SEULE
brique conteneurisée (l'API reste sur l'hôte).

Composants principaux :

- `LoginView.vue` — auth.
- `MainLayout.vue` — drawer gauche (conversations) + barre du haut +
  zone de chat.
- `ConversationsDrawer.vue` — liste + reprise, badge utilisateur.
- `ChatPane.vue` — fenêtre de chat, **stream WebSocket** des events
  d'orchestration (réflexion / délégation / appels d'outils visibles
  en live), upload d'attachements (drag & drop), Markdown rendu via
  `markdown-it`.
- `EventTrace.vue` — déroulé chronologique des events typés (mêmes 11
  classes que le CLI : `RequestStarted`, `DelegationStarted/Completed`,
  `ToolCall*`, `HookFired`, `WorkingBudgetUpdate`…). Les étapes de réflexion
  rendent le Markdown et sont repliables : seule la dernière est dépliée,
  les précédentes restent en aperçu (puce triangulaire pour basculer).
- `AskHumanDialog.vue` — aller-retour humain en cours de turn (le
  router peut demander une clarification, le UI met le turn en pause).
- `MemoryDialog.vue` — CRUD de la mémoire scopée côté UI (filtre par scope,
  recherche full-text, save / recall / update / delete).
- `MemoryReviewDialog.vue` — revue des suggestions de consolidation (badge dans
  la barre du haut) : accepter / éditer / étendre / ignorer, citation source visible.
- `ProjectsDialog.vue` — gestion CRUD des projets (+ sélecteur à la création).
- `WorkspaceDialog.vue` — arbre du workspace, lecture, download
  individuel ou zip, upload.
- `ProfileDialog.vue` — édition du profil du compte web (nom, ville,
  pays, langue, intérêts, notes — mêmes champs que `cli_profile.toml`).
- `ConversationDetailsDialog.vue` — métadonnées + titre éditable + delete
  avec confirmation.

Le TTS est rendu côté navigateur via `/api/tts` : le frontend récupère
le WAV synthétisé par Piper en blob, et le joue via la Web Audio API.
Pas de dépendance audio sur le navigateur (ni `speechSynthesis`).

```bash
cd web
npm install
npm run dev        # http://localhost:5173 (HMR)
# OU container :
docker compose -f web/compose.yml up --build   # → http://localhost:3000
```

Voir [web/README.md](web/README.md) pour les détails de stack.

## Multi-utilisateur

Mode mono-utilisateur côté CLI, multi-comptes côté web. Concrètement :

- Table `web_users` (migrate_112) : `id`, `username`, `password_hash`
  (argon2), `created_at`, plus les champs profil (`display_name`,
  `city`, `country`, `language`, `interests`, `notes`) remplis à la
  création du compte.
- Table `conversation_users` : association `conversation_id ↔ user_id`,
  matérialise l'ownership. Le CLI utilise un user système `cli`
  (`user_id=1`), invisible du frontal.
- `user_memory.user_id` (migrate_113) : la mémoire long-terme est
  scopée par user. Les facts d'Alice n'apparaissent jamais dans les
  prompts de Bob, ni vice-versa. Le CLI charge le user `cli` par défaut
  et garde ses propres entrées.
- `migrate_114_conversation_cascade.sql` : suppression d'une
  conversation cascade ses messages / events / state / workspace et
  l'association `conversation_users`.

Création d'un compte web :

```bash
./jm.sh --create-user alice           # prompt pour le password
```

## Capacités image (livré)

Audit complet dans
[DevNotes/WEBUI/03_audit_image_capabilities.md](DevNotes/WEBUI/03_audit_image_capabilities.md).
Livré (migrate_115→119) :

1. **Affichage** : endpoint authed `GET …/workspace/image?path=…`
   (vrai MIME), pattern blob → objectURL côté front (même mécanique que
   le TTS), lightbox `v-dialog` pour le clic. Côté front, `ChatPane.vue`
   extrait les images Markdown distantes (`![](http…)`) en grille `v-img`
   et `WorkspaceImage.vue` rend les images du workspace en miniatures.
2. **Recherche** : outil `image_search` (catégorie SearXNG) qui retourne
   URLs + miniatures sans télécharger (zéro SSRF/quota), résultats cappés.
3. **Vision** : outil `analyze_image(path, question)` qui encode l'image
   du workspace en base64 **transitoire** vers gemma4 multimodal — JAMAIS
   de base64 persisté dans `messages.json`. Image ⇒ DEEP forcé (granite ne
   classifie pas d'image).
4. **Listing** : les dotfiles sont masqués dans le workspace.

## Modèles configurables — `models.toml`

Toute la config modèle vit dans **`models.toml`** (gitignored ; défauts committés dans
`models.example.toml` — copie-le et édite ce que tu veux changer). Résolution :
**env var (si présente) > `models.toml` > défaut intégré.**

`[roles]` — quel modèle joue chaque rôle de la chaîne :

| Rôle (`models.toml`) | Modèle par défaut    | Env override                 |
|----------------------|----------------------|------------------------------|
| `main` (orchestrateur / routeur analyse·chat·vocal) | `cogito:32b`    | `JEANMICHEL_MAIN_MODEL`      |
| `dispatch` (tier-0)  | `granite4.1:8b`      | `JEANMICHEL_DISPATCH_MODEL`  |
| `code` (code-router) | `qwen3:14b`          | `JEANMICHEL_CODE_MODEL`      |
| `compactor`          | `gemma4:26b`         | `JEANMICHEL_COMPACTOR_MODEL` |
| `subagent` (défaut specialists) | `gemma4:26b` | `JEANMICHEL_SUBAGENT_MODEL`  |
| `reasoner`           | `gemma4:26b`         | `JEANMICHEL_REASONER_MODEL`  |

Autres sections de `models.toml` :
- `[context_window]` — `num_ctx` épinglé par modèle (taille du KV cache) + `ceiling` (plafond dur, 128k) +
  `default` (modèle non listé). La valeur = le **besoin réel** du modèle dans son rôle, PAS son contexte natif
  max : un KV 128k sur un 8B ≈ 54 Go → OOM. Override env : `JEANMICHEL_CTX_WINDOW_<slug>`.
- `no_thinking` — liste des modèles **sans canal `think` Ollama** (ex. `cogito:32b`) : on ne leur demande jamais
  le thinking → pas de HTTP 400 + retry. Leur raisonnement reste interne.
- `[voice]` — modèle Piper (TTS) + lecteur audio (override `JEANMICHEL_VOICE_MODEL` / `JEANMICHEL_AUDIO_PLAYER`).

**Routage par mode** : `MODE_ROUTER_MODEL` mappe `code → code-router` (`qwen3:14b`) ; les autres modes utilisent
le rôle `main` (`cogito:32b`). Per-agent : `agents.model_override` (BDD) assigne un modèle à un subagent —
ex. les workers code → `qwen3-coder:latest`, les reasoners → `gemma4:26b`.

**Les paradigmes (contenu en BDD injecté dans les system prompts) sont
strictement model-agnostic** : aucun ne mentionne `gemma`, `qwen`, `granite`
ou un nom de slot. Le choix du modèle est une décision d'infrastructure
(via `model_override` ou `models.toml`), pas une instruction comportementale.

## Logs

Le daemon écrit dans **`logs/jean-michel.log`** (rotatif, + stderr) ; niveau via
`JEANMICHEL_LOG_LEVEL` (défaut `INFO`). Le chemin WS/turn est instrumenté (réception
d'un tour, `final`, consolidation, sentinel, déconnexion) — utile pour diagnostiquer
les hangs côté daemon. Les logs uvicorn/websockets y sont aussi captés.

> **Base de connaissance** (design, audits, plans par thème) : [docs/README.md](docs/README.md).

## Hooks Python

> **Référence générée (valeurs live + ancres code)** : `docs/orchestrator_determinism.md`
> (`./jm.sh --orchestrator-map`) liste tous les paramètres déterministes avec leur
> valeur courante et où chaque gate/hook est appliqué. Cette section-ci est le
> *narratif* (le pourquoi) ; les valeurs exactes vivent dans le doc généré pour ne
> jamais périmer. Synoptic des agents : `docs/agents_synoptic.md` (`./jm.sh --synoptic`).

Les 4 hooks remplacent les anciens MUST en cascade dans les prompts :

- **`PreLLMCall(messages, state)`** — escalade de compaction sur le `WORKING`
  budget à 4 niveaux (Snip / Microcompact / Context Collapse / Autocompact),
  cf. §7 doc 06. Seuils 70/80/90/95 %. Ré-injecte aussi le recap du TODO
  (`todo.json`) en tête de contexte quand un existe (mode code, main agent
  seulement — no-op sinon).
- **`PreToolUse(ctx, state, dedup_cache)`** — grant check + dédup contextualisée
  + `MAX_DEPTH` pour delegate_to + `MAX_SEARCH=10` turn-wide pour les
  research tools. Retourne `Decision(deny, reason)`.
- **`PostToolUse(call, result, messages, state, dedup_cache, agent_code)`** —
  incrémente compteurs, reset persist counter sur workspace_write, alimente
  le cache de dédup, injecte un nudge si > 3 research calls sans persist.
- **`OnDelegateReturn(parent_messages, sub_result, state)`** — pousse le
  retour structuré du subagent comme `role=tool` dans le caller. Rejette
  un `report_back(confidence=low)` sans `low_confidence_reason`.

## Budget de contexte partitionné

Chaque appel LLM (main ou subagent) a sa propre fenêtre de contexte
partitionnée en trois zones :

- `SYSTEM_RESERVE` = system prompt + tools payload (fixé au démarrage).
- `OUTPUT_RESERVE` = 15 % du contexte total (réservé pour la réponse finale).
- `WORKING` = le reste, où le `messages[]` accumule.

Quand le `WORKING` se sature (paliers 70/80/90/95 %), `PreLLMCall` déclenche
l'escalade de compaction :
1. **Snip** (déterministe) — drop des nudges orchestrateur honorés + turns
   assistant vides.
2. **Microcompact** (déterministe) — remplace les tool results > 1500 tokens
   par un stub si recomputable depuis disque.
3. **Context Collapse** (appel LLM) — résume la fenêtre du milieu, préserve
   les `report_back` returns.
4. **Autocompact** (appel LLM, dernier recours) — résume tout sauf les 2
   derniers turns.

Garantie : même si l'autocompact échoue, un message synthétique de
fallback permet au système de toujours produire une réponse.

## Mémoire long-terme (scopée)

Table `memory` (migrate_125, généralise l'ancienne `user_memory`). Une seule
dimension **`scope`** pilote l'inclusion — `world` (global), `user` (sur
l'humain), `project` (un projet), `tool` (note d'usage d'un outil) — avec un
`CHECK` qui impose exactement une cible par scope. Tool unique
`manage_memory(action, scope, …)` : `save` / `recall` / `search` / `list` /
`update` / `delete` + raccourcis `note_for_world|user|project|tool`. Granté à
`jean-michel` (et aux specialists qui en ont besoin).

**Recherche full-text déterministe** : table FTS5 `memory_fts` + triggers de
synchro, ranking **BM25** (`action='search'`). Sert le rappel *et* la détection
de doublon/contradiction au moment de la consolidation.

**Inclusion 100 % déterministe** (aucun LLM dans ce chemin) :
`prompts.render_memory_block`, appelé dans `load_agent_spec_v2`, injecte dans le
bloc `## Human` un *index* (`code : description`, jamais le contenu) de : world
(partout) + la mémoire de l'utilisateur + le projet de la conversation + les
notes des outils **grantés à cet agent** (router ET subagents). Caps par scope
(`config.MEMORY_*_CAP`), warning de capacité.

Bootstrap depuis `cli_profile.toml` au premier démarrage : crée une entrée
`scope='user'`, `code='personal-profile'` si l'utilisateur n'a aucune mémoire.

### Projets

Table `projects` (migrate_124) possédée par un user ; `conversations.project_id`
(nullable, `ON DELETE SET NULL`) rattache une conversation à **0 ou 1** projet
(1 projet → N conversations). CLI : `--project <code>` (créé à la volée). Web :
gestion CRUD + sélecteur à la création (`ProjectsDialog.vue`). La mémoire
`scope='project'` est injectée pour les conversations du projet.

### Consolidation en *shadow*

Après chaque tour **DEEP**, une passe LLM tourne **en arrière-plan, une fois la
réponse émise** (pendant que l'utilisateur lit) : elle propose des mémoires
candidates. Chaque proposition est **vérifiée déterministiquement** — une
citation source (`grounding_quote`) doit apparaître mot pour mot dans la
conversation, sinon la candidate est rejetée (anti-hallucination) — puis
confrontée à la mémoire existante par recherche FTS (doublon → *extend*,
similaire → *review*). **Rien n'est écrit sans confirmation humaine** : les
candidates s'accumulent dans `pending_memory.json` et sont revues en fin de tour
(CLI : commande `/memo` ; web : event `MemoryConsolidationProposed` → badge +
`MemoryReviewDialog.vue`). Moteur : `src/jeanmichel/service/consolidation.py`.

## Persistance v2

Une conversation = un dossier plat horodaté :

```
conversations/2026-05-28_03-12_{conv_uuid}/
├── messages.json                    # main agent's full messages[]
├── state.json                       # ConversationState scalars
├── events.jsonl                     # typed event log (append-only)
└── subagent_<request_id>.json       # one per subagent execution
```

Audit cross-conversation : `~/.jean-michel/sandbox_audit.jsonl` (toutes
les exécutions `bash_sandbox`, toutes conversations confondues).

**Reprise** : `--resume` recharge `messages.json` ; le system prompt est
re-rendu pour intégrer l'index user_memory à jour.

## Snapshots git par conversation (revert / fork)

**Opt-in**, désactivé par défaut. Activé via
`JEANMICHEL_CONVERSATION_SNAPSHOT_ENABLED=1` (ou `config.CONVERSATION_SNAPSHOT_ENABLED`),
chaque conversation devient un **repo git local** (jamais pushé) dont la
branche porte l'`id` de la conversation. À **chaque fin de tour** (point de
passage unique `turn_runner.run_turn`, partagé CLI + API), un **commit**
archive l'état complet du dossier (messages / state / events / workspace).
**Tous les tours sont capturés** : les tours ALEXA (tier-0) persistent
désormais leur échange dans `messages.json` — avant ils étaient éphémères
(perdus au reload) —, donc ils produisent eux aussi un snapshot et restent
alignés 1:1 avec les bulles du chat.

Deux opérations, exposées par un menu **⋮ inline sur chaque réponse** dans le
frontal web :

- **Revenir à ce point** — rembobinage destructif (`git reset --hard` +
  `git clean -fd`) ; les tours suivants sont jetés (récupérables via
  `git reflog`). Refusé (409) si un tour est en cours.
- **Créer une nouvelle conversation à partir d'ici** — fork : le contenu du
  dossier **à ce commit** (hors `.git`, via `git archive`) est extrait dans
  une nouvelle conversation possédée par le même utilisateur. L'originale
  reste intacte.

Routes API (owner-scoped) : `GET …/snapshots`, `POST …/revert`,
`POST …/fork`. Tout est **best-effort** : `git` absent ou flag à off ⇒ no-op,
jamais de crash de tour (cf. `src/jeanmichel/snapshot.py`). Les dossiers
`conversations/` sont déjà gitignorés à la racine du projet, donc ces repos
imbriqués ne polluent pas le repo principal.

## Événements typés

L'orchestrateur émet 11 types d'events (catalogue dans
`src/jeanmichel/events.py`) consommés par le CLI live et persistés dans
`events.jsonl`. L'arbre des délégations se reconstruit en filtrant les
`DelegationStarted` / `DelegationCompleted`.

| Event                  | Émis quand                                          |
|------------------------|-----------------------------------------------------|
| `RequestStarted`       | début d'un tour humain ou d'une délégation          |
| `LLMCallStarted/Completed` | autour de chaque appel LLM                     |
| `ToolCallStarted/Completed` | autour de chaque tool natif                    |
| `DelegationStarted/Completed` | autour de chaque subagent spawn              |
| `HookFired`            | hook prend une action visible (deny, compaction)    |
| `WorkingBudgetUpdate`  | franchissement d'un seuil de compaction             |
| `MemoryNearCapacity`   | user_memory atteint 90 entrées                      |
| `RequestCompleted`     | agent produit sa réponse finale                     |

## Workspace per-conversation

Inchangé sur le principe (sandboxing, quota 256 Mo). En v2, un hook
`PostToolUse` force l'écriture progressive après plus de 3 research calls
consécutifs sans persist.

Tools : `workspace_create_file`, `workspace_append`, `workspace_str_replace`,
`workspace_view`, `workspace_list`, `workspace_create_dir`,
`workspace_delete_file`, `workspace_delete_dir` (les trois derniers grantés
aux workers code + workspace-manager).

## Sandbox Docker

Inchangé : `bash_sandbox` exécute des commandes dans un container isolé
(`--network=none`, `--cap-drop=ALL`, non-root, limites mémoire 512 Mo /
CPU 1 vCPU). Audit dans `~/.jean-michel/sandbox_audit.jsonl`.

Grants en BDD : `agent_sandbox_grants` (une ligne par binaire autorisé).
Images : `jeanmichel-sandbox:py-alpine` (défaut), `jeanmichel-sandbox:node-alpine`. La **sandbox projet**
(`repo_exec`, mode code) tourne dans l'image du projet — buildée depuis le Dockerfile des paramètres du projet
(`projects.dockerfile`), sinon `jeanmichel-sandbox:repo-default` (alpine + bash + git).

## Serveurs MCP (client)

**Opt-in.** Jean-Michel peut se comporter en **client MCP** (Model Context
Protocol) : il se connecte à des **serveurs MCP hébergés** (Streamable HTTP, par
URL — ex. Vuetify, GitHub) et expose leurs outils aux agents **comme des outils
natifs** (`src/jeanmichel/mcp_client.py`). Le paquet `mcp` (extra `[mcp]`) est
installé par `./jm.sh --install`.

Activation : copier `mcp_servers.example.toml` → `mcp_servers.toml` (le fichier
live est gitignoré ; **aucun secret** dedans — l'auth est une *référence* à une
variable d'env). Sans ce fichier → no-op total. Kill-switch :
`JEANMICHEL_MCP_DISABLED=1`.

```toml
[servers.vuetify]
url = "https://mcp.vuetifyjs.com/mcp"
category = "docs"

[servers.github]
url = "https://api.githubcopilot.com/mcp/"
category = "code"
auth_env = "GITHUB_MCP_TOKEN"        # token lu de l'env, jamais loggé

[categories]                          # catégorie → agents (association optimale)
docs = ["jean-michel", "code-fetcher"]
code = ["jean-michel", "code-fetcher"]
```

- **Catégorisation** : chaque serveur a une `category` ; `[categories]` associe
  catégorie→agents. Ajouter un serveur dans une catégorie existante l'accorde
  automatiquement aux bons agents (pas d'édition par serveur).
- Les outils sont exposés sous `mcp__<serveur>__<outil>` (namespacés, pas de
  collision) ; le `inputSchema` JSON Schema part tel quel vers Ollama. Les grants
  par agent (fusionnés dans `tool_grants`) pilotent la visibilité ET
  l'autorisation (gate `PreToolUse`).
- **Best-effort** : désactivé / `mcp` absent / serveur injoignable → no-op ;
  un appel en échec/timeout → `tool_error` (le LLM continue). Un cap
  (`JEANMICHEL_MCP_MAX_TOOLS_PER_SERVER`, défaut 30) protège le budget de
  contexte. Aujourd'hui : serveurs **HTTP hébergés** uniquement (pas de stdio local).

## Paradigmes en BDD

Le système de paradigmes survit en v2, mais purgé puis enrichi : **118
paradigmes actifs** au total. Trajectoire (extraits — détail complet dans
`db/migrations/`) :

- Phase 6 (migrate_100) : passage de 119 v1 → 104 v2 (anti-loop
  incantatoires retirés, outils morts purgés, 5 nouveaux paradigmes :
  `user_memory_discipline`, `nested_delegation_discipline`,
  `report_back_format`, `workspace_progressive_write`,
  `output_contract_no_inline_dump`).
- Migration 103 (search quality) : +4 paradigmes
  (`breadth_before_depth`, `wikipedia_lateral_exploration`,
  `coverage_check`, `strategist_decomposition_discipline` — ce dernier
  initialement nommé `parallel_specialists_for_inventory`).
- Migration 105 (strategist) : +1 paradigme (`strategist_first`) côté
  router.
- Migration 106 (news-specialist) : +1 paradigme
  (`news_freshness_discipline`) côté nouveau specialist.
- Migration 107 (news routing + web_fetch) : +1 paradigme
  (`news_first_for_news_briefs`) côté jean-michel, missions de
  web-search-specialist et news-specialist réécrites pour lever le
  chevauchement sémantique sur "news", tool `web_fetch` granté aux
  deux specialists.
- Migration 108 (code-fetcher) : +3 paradigmes
  (`code_fetcher_multi_source` côté code-fetcher,
  `delegate_to_code_fetcher_on_doubt` côté code-runner,
  `cite_sources_in_user_facing_output` côté jean-michel — bonus pour
  surfacer les sources dans la réponse user-facing).
- Migration 109 (code-runner routing + sandbox testing) : +2
  paradigmes (`code_runner_for_code_production_briefs` côté
  jean-michel, `test_in_sandbox_when_runnable` côté code-runner),
  mission de code-runner réécrite pour mettre "writes to workspace
  AND tests in sandbox" en tête des 160 premiers chars vus par le
  router.
- Migration 110 (syntax check before run) : raffine
  `test_in_sandbox_when_runnable` pour ajouter une étape de syntax
  check rapide AVANT l'exécution complète (`python -m py_compile`,
  `bash -n`, `node --check`, `python -m json.tool`, parser YAML).
  Le budget de 3 itérations couvre désormais syntax + runtime
  combinés.
- Migration 111 (code-runner → reasoner) : `code-runner` passe sur
  `gemma4:26b` via model_override. La production de code est du
  raisonnement intense, pas du lookup — le 9b par défaut était
  insuffisant pour produire du code de qualité.

Voir `DevNotes/REVOLUCION/08_paradigm_audit_table.md` pour le détail
de la purge initiale.

## Stack

**Cœur (toujours requis)** :
- Python 3.14 dans un venv local.
- SQLite (config + `user_memory` user-scoped + ownership conversations).
- Ollama 0.21+ (thinking + multi-turn natif, multimodal pour gemma4).
- `langdetect` côté dispatcher pour la détection de langue.

**CLI** :
- `rich`, `prompt_toolkit`.
- Docker (optionnel) pour `bash_sandbox` + le méta-moteur SearXNG local.
- `piper-tts` (mode vocal), `paplay`/`aplay`/`ffplay` (lecture).

**Surface web** (extra `[web]`) :
- `fastapi` + `uvicorn[standard]` — daemon REST + WebSocket.
- `argon2-cffi` + `itsdangerous` — hash + bearer signé.
- `python-multipart` — uploads workspace.
- Côté front : Vue 3.5, Vuetify 4, Pinia 3, markdown-it, Vite 8.

**Client MCP** (extra `[mcp]`) :
- `mcp` — SDK officiel (client Streamable HTTP). Installé par `./jm.sh --install` ;
  inerte tant qu'aucun `mcp_servers.toml` n'est présent.

## Installation

```bash
./jm.sh --install       # venv + schéma v2 (db/schema.sql) + build des images sandbox
./jm.sh                 # lance le CLI (mode analyse, nouvelle conversation)
./jm.sh --mode chat     # conversation continue
./jm.sh --mode code     # orchestrateur codeur (qwen3:14b + workers qwen3-coder, PDCA + TODO)
./jm.sh --mode vocal    # réponses courtes + Piper TTS (voir voice_models/README.md)
./jm.sh --resume        # reprend la dernière conversation active
./jm.sh --resume <id>   # reprend une conversation spécifique (id ou préfixe)
./jm.sh --list-conv     # liste les conversations actives et exit
./jm.sh --reap-sandboxes            # tue les containers sandbox orphelins
./jm.sh --build-docker              # builde l'image Python Alpine
./jm.sh --build-docker node-alpine  # builde l'image Node Alpine
./jm.sh --build-docker all          # builde toutes les images sandbox
```

### Surface web

```bash
./jm.sh --create-user alice          # crée un compte web (prompt password)
./jm.sh --serve                      # lance l'API daemon sur 0.0.0.0:8000
# dans un autre terminal — choisir UN des deux :
cd web && npm install && npm run dev                      # dev (HMR), :5173
docker compose -f web/compose.yml up --build              # prod nginx, :3000
```

L'API et le frontend partagent la même BDD que le CLI. Les conversations
créées via le CLI restent invisibles du web (associées au user système
`cli`), celles créées via le web restent invisibles au CLI sauf si tu
forces son `cli_profile.toml`.

Override modèles :

```bash
./jm.sh --main-model gemma4:26b           # un main agent plus capable
./jm.sh --dispatch-model qwen3:14b        # un dispatcher différent
```

## Clés API externes et fichier `.env`

Certains tools dépendent d'APIs tierces. Configure-les soit via un export
shell, soit via un fichier `.env` à la racine du repo (ignoré par git) :

```bash
cp .env.example .env
$EDITOR .env          # remplir NEWSDATA_API_KEY=...
./jm.sh               # le loader lit .env au démarrage
```

Quand une clé est manquante, le tool concerné renvoie un
`tool_error("api_key_missing", …)` clair — pas de dégradation silencieuse,
le LLM voit l'erreur et bascule sur un autre outil.

| Env var                    | Tool(s) / Mode                                                    | Notes                              |
|----------------------------|-------------------------------------------------------------------|------------------------------------|
| `NEWSDATA_API_KEY`         | `news_latest`, `news_archive`                                     | newsdata.io, free 200 req/jour     |
| `GITHUB_TOKEN`             | `github_search_code` (requis), `github_search_repos` (5000 req/h) | fine-grained PAT read-only public  |
| `STACKEXCHANGE_KEY`        | `stackoverflow_search` (optionnel)                                | 300 req/j sans, 10 000 avec        |
| `JEANMICHEL_VOICE_MODEL`   | mode `--mode vocal` (Piper TTS)                                   | path vers un `.onnx`, cf. [voice_models/README.md](voice_models/README.md) |
| `JEANMICHEL_AUDIO_PLAYER`  | mode `--mode vocal` (optionnel)                                   | force `paplay`/`aplay`/`ffplay`    |

Les tools `clock`, `weather` (open-meteo), `wikipedia_*`, `web_search`
(SearXNG local), `web_fetch` (readability-lxml), `pypi_lookup` ne
nécessitent pas de clé.

**Pattern de recherche en profondeur** : `news_latest` ou `web_search`
renvoient des URLs + previews courts (snippet ou description). Pour
lire le texte complet de 1-3 articles intéressants sans consommer de
crédit supplémentaire, l'agent fait suivre d'un ou plusieurs
`web_fetch(url=…)` qui extrait l'article via l'algo readability
(strip nav/footer/ads, plain text). Une seule requête news = jusqu'à
10 URLs candidates + N lectures profondes.

**Précédence** : un export shell prend le pas sur la valeur du `.env`.
Le `.env` sert de défaut persistant ; tu peux overrider un run avec
`KEY=value ./jm.sh`. Format du `.env` : `KEY=value` une par ligne,
commentaires `#`, quotes optionnelles, pas d'interpolation `$VAR`
(loader maison de 20 lignes dans `config.py`, sans dépendance externe).

## Migrations BDD

`db/schema.sql` est l'état v2 consolidé (fresh installs partent de là).

`db/schema_v1_baseline.sql` est conservé pour valider les migrations
v1 → v2 dans les tests.

Migrations v2 sous `db/migrations/` :

- `migrate_100_paradigm_realignment.sql` — purge des paradigmes obsolètes
  + 5 nouveaux paradigmes + grant `manage_user_memory` à `jean-michel`
  + désactivation `archivist`.
- `migrate_101_user_memory.sql` — création de la table `user_memory`.
- `migrate_102_drop_runtime_tables.sql` — drop `requests`/`artifacts`/
  `conversation_phases`/`sandbox_executions` + colonne `agents.model_override`
  + suppression définitive `archivist`.
- `migrate_103_search_quality.sql` — 4 paradigmes ciblés sur la qualité
  des recherches multi-domaine (`breadth_before_depth` côté web-search,
  `wikipedia_lateral_exploration`, `coverage_check` côté document-builder,
  `parallel_specialists_for_inventory` initialement côté router).
- `migrate_104_drop_conv_read_file.sql` — suppression des grants
  `conv_read_file` (outil redondant avec `workspace_view`, retiré du code).
- `migrate_105_strategist_agent.sql` — création de l'agent `strategist`
  (reasoner dédié à la décomposition stratégique), déplacement du
  paradigme inventory de jean-michel vers strategist, model_override
  `gemma4:26b` sur les 4 reasoners (strategist + critical-thinker +
  comparator-specialist + meta-analyst), retour de jean-michel sur
  MAIN_MODEL.
- `migrate_106_news_specialist.sql` — création de l'agent `news-specialist`
  (lookup-tier, default model), grants des nouveaux tools `news_latest`
  + `news_archive`, paradigme `news_freshness_discipline`, ajout aux
  delegation_targets de jean-michel.
- `migrate_107_news_routing_and_web_fetch.sql` — fix routing
  news-specialist (mission web-search-specialist sans "news", mission
  news-specialist value-first, paradigme `news_first_for_news_briefs`
  côté router, `news_freshness_discipline` réécrit autour du pattern
  news_latest + web_fetch), grants `web_fetch` à news-specialist +
  web-search-specialist.
- `migrate_108_code_fetcher_agent.sql` — création de l'agent
  `code-fetcher` (lookup-tier : GitHub + Stack Overflow + PyPI +
  web_fetch), mise à jour mission de `code-runner` pour acknowledger
  la délégation, paradigme `delegate_to_code_fetcher_on_doubt` côté
  code-runner, ajout aux delegation_targets de jean-michel ET de
  code-runner. Bonus : paradigme `cite_sources_in_user_facing_output`
  côté jean-michel pour que la réponse au user inclue les sources
  consultées (URLs + dates).
- `migrate_109_code_runner_routing_and_sandbox.sql` — fix routing
  code-runner : paradigme `code_runner_for_code_production_briefs`
  côté jean-michel ("pour écrire/débugger du code → code-runner, pas
  de code inline"), mission de code-runner réécrite pour exposer
  "writes to workspace AND tests in sandbox" dès les 160 premiers
  chars, paradigme `test_in_sandbox_when_runnable` qui force
  l'exécution dans bash_sandbox avant report_back (3 itérations max).
- `migrate_110_syntax_check_before_run.sql` — raffine
  `test_in_sandbox_when_runnable` pour ajouter une étape syntax check
  rapide AVANT l'exécution complète (recipes par langage : Python /
  Bash / Node / JSON / YAML). Évite de consommer un run sandbox pour
  des erreurs triviales (typos, brackets, indentation).
- `migrate_111_code_runner_to_reasoner.sql` — `code-runner` rejoint
  les reasoners (`model_override='gemma4:26b'`). L'écriture de code
  est du raisonnement, pas du lookup.
- `migrate_112_web_users.sql` — support multi-utilisateur du frontal web
  (additif) : tables `web_users` + `conversation_users` (association
  user ↔ conversation). Le CLI ne crée pas d'association ; ses conversations
  restent invisibles au frontal web. Cf. `DevNotes/WEBUI/01_audit_api_async_webui.md`.
- `migrate_113_user_memory_isolation.sql` — `user_memory.user_id` ajouté,
  toutes les rows existantes reattribuées au user système `cli`. Lecture
  et CRUD désormais filtrés par `user_id`. Plus de fuite cross-user.
  Cf. `DevNotes/WEBUI/02_audit_user_memory_isolation.md`.
- `migrate_114_conversation_cascade.sql` — `ON DELETE CASCADE` sur les
  FK pour que la suppression d'une conversation (depuis le web) emporte
  proprement ses messages, events, state et association
  `conversation_users`. Le workspace sur disque est nettoyé côté service.
- `migrate_115`→`119` — **capacités image** : outil `image_search`
  (115), outils vision `analyze_image` (116), routing affichage image +
  DEEP forcé sur image (117), paradigmes ré-écrits en anglais (118), cap
  sur le nombre de résultats image (119).
- `migrate_120`→`123` — **orchestrateur codeur** : infra de décomposition
  TODO (`todo_write`, paradigme PDCA) + agent `code-runner` re-routé
  (120), mode `code` + extension des CHECK `mode IN (…,'code')` sur
  `paradigm_modes` et `conversations` + `CODE_MODEL` (121), workspace
  file ops (`workspace_create_dir`/`delete_file`/`delete_dir`, 122), agent
  `code-runner-node` (sandbox `node-alpine`, modèle `qwen3-coder`, 123).
  Cf. [DevNotes/ORCHESTRATOR/](DevNotes/ORCHESTRATOR/).
- `migrate_124`→`126` — **mémoire scopée + projets + consolidation** : table
  `projects` + `conversations.project_id` (124) ; `user_memory` → `memory` avec
  dimension `scope` (world/user/project/tool), index partiels d'unicité, FTS5 +
  BM25, migration des rows existantes en `scope='user'` (125) ; renommage tool
  `manage_user_memory` → `manage_memory`, paradigmes `memory_discipline` +
  `tool_note_discipline` (126).

Pour migrer une instance v1 existante :

```bash
for m in $(seq 100 126); do
  sqlite3 jeanmichel.db < db/migrations/migrate_${m}_*.sql
done
```

## Profil utilisateur

Édite `cli_profile.toml` à la racine. Description libre injectée dans
le bloc `## Human` du prompt + ingérée dans `user_memory` au premier
démarrage. Exemple :

```toml
name = "Jeremy"
city = "Montréal"
country = "Canada"
language = "fr"
notes = "Dev senior, préfère les réponses directes sans préambule."
```

## Arborescence du repo

```
jeanmichel/
├── README.md
├── jm.sh                     # point d'entrée unifié (CLI + --serve + --create-user)
├── pyproject.toml            # extras [dev], [web], [mcp]
├── cli_profile.toml          # profil du user système 'cli'
├── cli_profile.example.toml
├── .env / .env.example       # clés d'API tools externes
├── db/
│   ├── schema.sql            # schéma v2 consolidé (123 migrations appliquées)
│   ├── schema_v1_baseline.sql # baseline v1 (tests migration)
│   └── migrations/           # migrate_NNN_*.sql
├── debug/
│   ├── inspect_conv.py       # lit messages.json + events.jsonl + state.json
│   ├── export_db.py
│   ├── admin.py
│   ├── clean_convs.py
│   └── paradigm_matrix.py
├── docker/sandbox/           # Dockerfiles sandbox (py-alpine, node-alpine, repo-default)
├── docker/searxng/           # compose.yml du méta-moteur local
├── voice_models/             # Piper .onnx + README (gitignored sauf README)
├── docs/
│   ├── PROMPT_SKELETON.md
│   ├── GEMMA4.md
│   └── HOWTO_ADD_SPECIALIST_OR_TOOL.md
├── DevNotes/
│   ├── REVOLUCION/           # plans, audits, propositions v2 (01→09)
│   ├── WEBUI/                # audits du frontal web
│   │   ├── 01_audit_api_async_webui.md
│   │   ├── 02_audit_user_memory_isolation.md
│   │   └── 03_audit_image_capabilities.md
│   ├── ORCHESTRATOR/         # mode code : décomposition PDCA, patterns Claude Code (01→04)
│   ├── claude_4.7_extraction/
│   ├── the_toolbox/
│   └── todo.md
├── src/jeanmichel/
│   ├── cli.py                # CLI Rich (Tier 0 + Tier 1)
│   ├── orchestrator_v2.py    # main loop + spawn_subagent
│   ├── dispatcher.py         # Tier 0 (granite)
│   ├── hooks.py              # 4 hooks Python
│   ├── compaction.py         # 4-level escalade
│   ├── events.py             # 11 dataclasses typées
│   ├── tokens.py             # estimation contexte
│   ├── llm.py                # OllamaClient + MockClient (chat_messages)
│   ├── persistence.py        # messages.json + state.json + events.jsonl
│   ├── snapshot.py           # git par conversation (revert / fork), opt-in
│   ├── mcp_client.py         # client MCP (serveurs hébergés → outils natifs), opt-in
│   ├── bootstrap.py          # toml → user_memory
│   ├── prompts.py            # render_system_prompt_v2 + index user_memory
│   ├── config.py             # paramètres v2 + loader .env
│   ├── db.py                 # accès SQLite + helpers users / ownership
│   ├── models.py             # dataclasses + ConversationState
│   ├── voice.py              # Piper TTS streaming + Markdown cleanup
│   ├── service/              # logique métier partagée CLI ↔ API
│   │   ├── conversation.py   # create / delete (cascade)
│   │   ├── memory.py         # user_memory CRUD (user-scoped)
│   │   ├── workspace.py      # list_tree / read / upload / zip
│   │   └── turn_runner.py    # run_turn(text, conv, user_id, …) streaming
│   ├── api/                  # FastAPI daemon
│   │   ├── app.py            # create_app(), routes REST + WebSocket
│   │   ├── auth.py           # argon2 + bearer signé, create-user CLI
│   │   └── executor.py       # run_turn_streaming (event → WS)
│   └── tools/
│       ├── delegate_to.py    # schema (control verb)
│       ├── report_back.py    # schema + validation (+ suggested_todo_updates)
│       ├── todo_write.py      # TODO plat (mode code, PDCA)
│       ├── manage_memory.py
│       ├── clock.py, weather.py, wikipedia.py, web_search.py, web_fetch.py
│       ├── news.py, github.py, stackoverflow.py, pypi.py
│       ├── image_search.py, image_fetch.py, analyze_image.py  # capacités image
│       ├── workspace_*.py, self_inspect_*.py
│       └── bash_sandbox.py
├── web/                      # frontal SPA Vue 3 + Vuetify
│   ├── Dockerfile + compose.yml + nginx/   # build → image nginx
│   ├── package.json + vite.config.mjs
│   └── src/
│       ├── main.js + App.vue
│       ├── api.js + ws.js + download.js
│       ├── stores/{auth,conversations}.js  # Pinia
│       ├── components/
│       │   ├── LoginView.vue
│       │   ├── MainLayout.vue
│       │   ├── ChatPane.vue           # chat + WS event stream
│       │   ├── ConversationsDrawer.vue
│       │   ├── ConversationDetailsDialog.vue
│       │   ├── EventTrace.vue         # déroulé des events typés
│       │   ├── AskHumanDialog.vue
│       │   ├── MemoryDialog.vue       # CRUD user_memory
│       │   ├── ProfileDialog.vue
│       │   └── WorkspaceDialog.vue    # arbre, upload, download zip
│       └── plugins/vuetify.js + styles/
└── tests/v2/                 # ~550 tests pytest
    ├── conftest.py
    ├── test_orchestrator_v2.py
    ├── test_dispatcher.py
    ├── test_hooks.py
    ├── test_compaction.py
    ├── test_events.py
    ├── test_persistence.py
    ├── test_llm_client.py
    ├── test_tokens.py
    ├── test_user_memory.py
    ├── test_cli_rendering.py
    ├── test_migration_idempotence.py
    ├── test_no_orphan_paradigms.py
    ├── test_schema_v2.py
    └── test_smoke_e2e.py     # skipped sans Ollama (JEANMICHEL_SMOKE_E2E=1)
```

## État

Bascule v2 complétée (8 phases, cf. `DevNotes/REVOLUCION/07_plan_implementation.md`),
mergée sur `main`. Config modèle via `models.toml` : orchestrateur (`main`) sur
**cogito:32b**, dispatcher sur granite4.1:8b, code-router sur qwen3:14b, 4 reasoners +
compactor/subagent sur gemma4:26b, 2 workers code sur qwen3-coder (pattern
fetcher/runner). **835 tests v2 verts.**

**Cœur stabilisé** : CLI multi-tour en tous modes, `--resume`, `--list-conv`,
dispatcher Tier 0 opérationnel via granite, main loop Tier 1 multi-turn
natif, subagents Tier 2 avec délégation imbriquée jusqu'à `MAX_DEPTH=5`,
mémoire long-terme utilisateur (scopée par user), compaction 4 niveaux,
sandbox audit JSONL, Markdown cleanup pour TTS, configuration tunable
sans recompile via `config.py` + `.env` + CLI flags.

**Frontal web livré** (sprints S0→S7 + M2→M3, cf. branche
`voice_out` historique) : API FastAPI multi-utilisateur, WebSocket
streaming des events orchestrateur, SPA Vue 3 / Vuetify avec
auth + chat + drawer conversations + workspace UI + memory CRUD +
profile + TTS navigateur. Conteneurisé côté front uniquement (nginx),
daemon Python à la main côté hôte.

**Capacités image livrées** (migrate_115→119) : endpoint authed
`/workspace/image` (vrai MIME + blob pattern), outil `image_search`
(SearXNG), outil `analyze_image(path, question)` vers gemma4 multimodal
(image ⇒ DEEP forcé, jamais de base64 persisté dans `messages.json`),
affichage front (grille `v-img` + miniatures workspace + lightbox).

**Orchestrateur codeur livré** (mode `code`, migrate_120→123, cf.
[DevNotes/ORCHESTRATOR/](DevNotes/ORCHESTRATOR/)) : main agent `qwen3:14b`
qui décompose en TODO plat (`todo_write`) et pilote une boucle PDCA sur des
workers `qwen3-coder` (code-runner py-alpine + code-runner-node node-alpine,
code-fetcher pour le lookup). Sprints S1 (infra TODO) + S2 (wiring modèles +
paradigme PDCA) + S2.5 (mode code), plus les renforts tirés du fork Claude
Code : retry sans thinking (R5), reaper de sandbox (R1, `--reap-sandboxes`),
tools fichiers workspace (R4), worker node (R2).

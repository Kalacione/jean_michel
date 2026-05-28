# Jean-Michel

Assistant IA local à agents spécialisés, orchestration Python déterministe avec
hooks, et boucle multi-turn native Ollama. **Version 2** (branche `revolucion`).

## Concept

Une requête humaine entre par le **Tier 0 — Dispatcher** (un petit LLM rapide,
`granite4.1:8b` par défaut, sans thinking, JSON forcé). Il classifie en
**ALEXA** (un seul tool suffit, exécution directe) ou **DEEP** (engagement du
Tier 1).

- **ALEXA** : un tool natif (clock / weather / wikipedia_search) est invoqué,
  le résultat est formaté en français/anglais selon la langue détectée, fin.
- **DEEP** : le **Tier 1 — Main agent** (`gemma4:latest` par défaut, multi-turn
  natif, thinking ON) prend la main avec son propre `messages[]` accumulé. Il
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

## Agents v2 — le mille-feuille cognitif

14 agents actifs, organisés en **deux dimensions** :

**1. Dimension structurelle (place dans le task tree)** — exposée par
`agents.role` :

- **`router`** (1) : `jean-michel` — main agent Tier 1. Reçoit la requête,
  formalise, délègue, synthétise. **Ne raisonne pas, ne fait pas le boulot.**
- **`specialist`** (12) — exécutent les tâches concrètes, terminent par
  `report_back`. Peuvent eux-mêmes spawn d'autres specialists
  (`MAX_DEPTH=5`).
- **`finalizer`** (1) : `synthesizer` — fusion finale quand plusieurs
  specialists ont contribué. Termine par un turn assistant sans tool_calls.

**2. Dimension cognitive (intensité de raisonnement requise)** — pas une
colonne SQL formelle, mais une convention reflétée dans
`agents.model_override` :

| Tier cognitif | Agents | Modèle |
|---|---|---|
| **I/O & lookup** | `weather-specialist`, `wikipedia-specialist`, `web-search-specialist`, `news-specialist`, `workspace-manager`, `code-runner` | `gemma4:latest` (default) |
| **Synthèse / format** | `summarizer`, `document-builder`, `synthesizer` (finalizer) | `gemma4:latest` (default) |
| **Reasoners** | `strategist`, `critical-thinker`, `comparator-specialist`, `meta-analyst` | `gemma4:26b` via `model_override` |

Les **reasoners** sont des specialists dont le métier *EST* le raisonnement —
ils sont sur un modèle plus capable parce que c'est leur raison d'être, pas
un workaround. `strategist` notamment décompose une requête exploratoire
ouverte en N axes thématiques disjoints et retourne un *plan* que le
router exécute en parallèle.

**Règle d'or** : si tu as envie de mettre du `model_override` sur le router
parce qu'il "doit faire X de plus", arrête — c'est un signal qu'il faut un
nouveau specialist dont c'est le métier.

## Modes

Choisi au démarrage via `--mode {analyse,chat,vocal}` (défaut `analyse`).

- **`analyse`** — CLI persistante entre questions, pas de follow-ups.
- **`chat`** — conversation continue, follow-ups proposés par jean-michel.
- **`vocal`** — réponses concises (< 4 phrases courtes), paradigme
  `concise_output` activé pour brancher une synthèse vocale en sortie.

Le mode est porté par la conversation et apparaît dans le bloc
`## Conversation` du prompt système. Les paradigmes peuvent être restreints
à un mode via la table `paradigm_modes`.

## Modèles configurables

5 slots dans `config.py`, chacun overridable par env var et CLI flag :

| Slot                     | Défaut             | Env var                           | CLI flag           |
|--------------------------|--------------------|-----------------------------------|--------------------|
| `DISPATCH_MODEL`         | `granite4.1:8b`    | `JEANMICHEL_DISPATCH_MODEL`       | `--dispatch-model` |
| `MAIN_MODEL`             | `gemma4:latest`    | `JEANMICHEL_MAIN_MODEL`           | `--main-model`     |
| `COMPACTOR_MODEL`        | `gemma4:latest`    | `JEANMICHEL_COMPACTOR_MODEL`      | —                  |
| `SUBAGENT_DEFAULT_MODEL` | `gemma4:latest`    | `JEANMICHEL_SUBAGENT_MODEL`       | —                  |
| `REASONER_MODEL`         | `gemma4:26b`       | `JEANMICHEL_REASONER_MODEL`       | —                  |

Per-agent override : la colonne `agents.model_override` permet d'assigner
un modèle spécifique à un subagent (ex. `strategist → gemma4:26b`).

**Convention** : aujourd'hui, les 4 reasoners (strategist, critical-thinker,
comparator-specialist, meta-analyst) ont chacun `model_override='gemma4:26b'`
directement en BDD. Le slot `REASONER_MODEL` existe en Python comme point
d'extension stable — un futur switch global (changer de modèle de raisonnement
pour tous les reasoners) pourra se faire par env var sans migration DB, une
fois qu'on aura introduit un flag d'agent (genre `cognitive_tier='high'`)
lu par le résolveur.

**Les paradigmes (contenu en BDD injecté dans les system prompts) sont
strictement model-agnostic** : aucun ne mentionne `gemma`, `qwen`, `granite`
ou un nom de slot. Le choix du modèle est une décision d'infrastructure
(via `model_override` ou config Python), pas une instruction comportementale.

## Hooks Python

Les 4 hooks remplacent les anciens MUST en cascade dans les prompts :

- **`PreLLMCall(messages, state)`** — escalade de compaction sur le `WORKING`
  budget à 4 niveaux (Snip / Microcompact / Context Collapse / Autocompact),
  cf. §7 doc 06. Seuils 70/80/90/95 %.
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

## Mémoire long-terme utilisateur

Table `user_memory` (cf. §10 doc 06). Quatre types : `user`, `feedback`,
`project`, `reference`. Tool unique `manage_user_memory(action, …)` avec
5 actions : `save`, `recall`, `list`, `update`, `delete`. Granté
uniquement à `jean-michel`.

L'index (type + code + description) est injecté automatiquement dans le
bloc `## Human` du system prompt — le LLM voit ce dont il se souvient
sans charger les contenus complets. Limite 100 entrées affichées,
warning à 90.

Bootstrap depuis `user_profile.toml` au premier démarrage : crée une
entrée `user/personal-profile` si la table est vide.

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
`workspace_view`, `workspace_list`.

## Sandbox Docker

Inchangé : `bash_sandbox` exécute des commandes dans un container isolé
(`--network=none`, `--cap-drop=ALL`, non-root, limites mémoire 512 Mo /
CPU 1 vCPU). Audit dans `~/.jean-michel/sandbox_audit.jsonl`.

Grants en BDD : `agent_sandbox_grants` (une ligne par binaire autorisé).
Images : `jeanmichel-sandbox:py-alpine` (défaut), `jeanmichel-sandbox:node-alpine`.

## Paradigmes en BDD

Le système de paradigmes survit en v2, mais purgé puis enrichi : **109
paradigmes actifs** au total. Trajectoire :

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

Voir `DevNotes/REVOLUCION/08_paradigm_audit_table.md` pour le détail
de la purge initiale.

## Stack

- Python 3.14 dans un venv local.
- SQLite (configuration + user_memory cross-conv ; **plus de tables runtime**).
- Ollama 0.21+ (thinking + multi-turn natif).
- CLI : `rich`, `prompt_toolkit`.
- Docker (optionnel, sandbox).
- `langdetect` pour la détection de langue côté dispatcher.

## Installation

```bash
./jm.sh --install       # crée le venv + charge le schéma v2 (db/schema.sql)
./jm.sh                 # lance le CLI (mode analyse, nouvelle conversation)
./jm.sh --mode chat     # conversation continue
./jm.sh --mode vocal    # pipeline TTS friendly (concision)
./jm.sh --resume        # reprend la dernière conversation active
./jm.sh --resume <id>   # reprend une conversation spécifique (id ou préfixe)
./jm.sh --list-conv     # liste les conversations actives et exit
./jm.sh --build-docker              # builde l'image Python Alpine
./jm.sh --build-docker node-alpine  # builde l'image Node Alpine
./jm.sh --build-docker all          # builde toutes les images sandbox
```

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

| Env var              | Tool(s)                       | Notes                              |
|----------------------|-------------------------------|------------------------------------|
| `NEWSDATA_API_KEY`   | `news_latest`, `news_archive` | newsdata.io, free 200 req/jour     |

Les tools `clock`, `weather` (open-meteo), `wikipedia_*`, `web_search`
(SearXNG local) ne nécessitent pas de clé.

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

Pour migrer une instance v1 existante :

```bash
for m in 100 101 102 103 104 105 106; do
  sqlite3 jeanmichel.db < db/migrations/migrate_${m}_*.sql
done
```

## Profil utilisateur

Édite `user_profile.toml` à la racine. Description libre injectée dans
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
├── jm.sh                     # point d'entrée unifié
├── pyproject.toml
├── user_profile.toml
├── db/
│   ├── schema.sql            # schéma v2 consolidé
│   ├── schema_v1_baseline.sql # baseline v1 (tests migration)
│   └── migrations/           # migrate_NNN_*.sql
├── debug/
│   ├── inspect_conv.py       # v2 (lit messages.json + events.jsonl + state.json)
│   ├── export_db.py
│   ├── admin.py
│   ├── clean_convs.py
│   └── paradigm_matrix.py
├── docker/sandbox/           # Dockerfiles bash_sandbox
├── docs/
│   ├── PROMPT_SKELETON.md
│   ├── GEMMA4.md
│   └── HOWTO_ADD_SPECIALIST_OR_TOOL.md
├── DevNotes/REVOLUCION/      # plans, audits, propositions v2
│   ├── 01_audit_orchestrateur.md
│   ├── 02_architecture_cible.md
│   ├── 03_retour_sur_architecture_cible.md
│   ├── 04_audit_complementaire.md
│   ├── 05_inspiration_claude_copilot.md
│   ├── 06_proposition_v2.md         # référence architecturale v2
│   ├── 07_plan_implementation.md
│   ├── 08_paradigm_audit_table.md
│   └── 09_phase8_completion.md
├── src/jeanmichel/
│   ├── cli.py                # v2 CLI (Tier 0 + Tier 1)
│   ├── orchestrator_v2.py    # main loop + spawn_subagent
│   ├── dispatcher.py         # Tier 0
│   ├── hooks.py              # 4 hooks Python
│   ├── compaction.py         # 4-level escalade
│   ├── events.py             # 11 dataclasses typées
│   ├── tokens.py             # estimation contexte
│   ├── llm.py                # OllamaClient + MockClient (chat_messages)
│   ├── persistence.py        # messages.json + state.json + events.jsonl
│   ├── bootstrap.py          # toml → user_memory
│   ├── prompts.py            # render_system_prompt_v2 + index user_memory
│   ├── config.py             # paramètres v2 (configurables)
│   ├── db.py                 # accès SQLite (helpers v1+v2)
│   ├── models.py             # dataclasses + ConversationState
│   └── tools/
│       ├── delegate_to.py    # schema (control verb)
│       ├── report_back.py    # schema + validation
│       ├── manage_user_memory.py
│       ├── clock.py, weather.py, wikipedia.py, web_search.py
│       ├── workspace_*.py, conv_*.py, self_inspect_*.py
│       └── bash_sandbox.py
└── tests/v2/                 # ~300 tests pytest contre la v2
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

Bascule v2 complétée (8 phases, cf. `DevNotes/REVOLUCION/07_plan_implementation.md`).
**14 agents actifs** (dont 4 reasoners sur gemma4:26b). ~320 tests v2 verts.
CLI multi-tour en tous modes, `--resume`, `--list-conv`. Dispatcher Tier 0
opérationnel via granite. Main loop Tier 1 multi-turn natif. Subagents Tier 2
avec délégation imbriquée jusqu'à `MAX_DEPTH=5`. Mémoire long-terme
utilisateur active. Compaction 4 niveaux. Configuration tunable sans
recompile via `config.py` + env vars + CLI flags.

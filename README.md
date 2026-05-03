# Jean-Michel

Assistant IA local à agents spécialisés, prompts dynamiques et orchestration Python.

## Concept

Une requête humaine arrive à **Jean-Michel** (agent routeur). Il la formalise, la classe, puis :

- répond directement si la tâche est triviale (ex : heure courante via un outil),
- délègue à un ou plusieurs **agents spécialistes**.

Quand plusieurs spécialistes contribuent, l'agent **synthesizer** fusionne les sorties en une seule réponse cohérente pour l'humain, dans la langue détectée.

Agents actifs :
- **jean-michel** (router) — reçoit la requête, classe, route ou répond directement
- **summarizer** (specialist) — résumé de texte
- **weather-specialist** (specialist) — météo via open-meteo
- **wikipedia-specialist** (specialist) — recherche et extraction d'articles Wikipedia
- **comparator-specialist** (specialist) — orchestre des recherches en plusieurs entités et produit un verdict comparatif structuré
- **critical-thinker** (specialist) — examine la solidité d'un raisonnement, surface assumptions et biais, produit une analyse structurée sans verdict
- **document-builder** (specialist) — produit des documents structurés (rapports, synthèses, specs) écrits dans le workspace de la conversation
- **workspace-manager** (specialist) — inspecte et gère le workspace : liste, usage disque, lecture/écriture de fichiers
- **synthesizer** (finalizer) — fusionne plusieurs réponses de spécialistes en une seule réponse cohérente
- **archivist** (finalizer) — invoqué uniquement par l'orchestrateur en modes `chat`/`vocal`, met à jour le `summary.md` de la conversation après chaque tour

L'**orchestrateur** (code Python pur, pas un LLM) construit les prompts à la volée, dépile les requêtes, gère les statuts et persiste tout sur disque.

## Modes

Le mode est choisi au démarrage via `--mode {analyse,chat,vocal}` (default `analyse`). Il est fixé pour toute la durée de la conversation.

- **`analyse`** — comportement one-shot. Une requête, une réponse, fin. Pas de continuité, pas de summary, pas d'archivist. Mode par défaut.
- **`chat`** — conversation continue. Après chaque réponse, la CLI redonne la main à l'humain. L'archivist met à jour `summary.md` après chaque tour, qui est ré-injecté en préfixe du tour suivant. Jean-Michel propose 2-3 axes de creusage en fin de réponse.
- **`vocal`** — dérivé de `chat` mais avec réponses concises (< 4 phrases courtes), prêt pour synthèse vocale future. Certains paradigmes incompatibles avec la concision (steelman, hold_tension, depth_over_speed, etc.) sont automatiquement filtrés.

Le mode est porté par la conversation et apparaît dans le bloc `# CONTEXT` du prompt système. Les paradigmes peuvent être restreints à un ou plusieurs modes via la table `paradigm_modes` (absence de ligne = applicable à tous les modes).

## Principes

- Modèles **100 % locaux** via Ollama.
- **Gemma 4** (mono-modèle au démarrage), tokens et thinking mode natifs.
- **DB = source de vérité** (SQLite). Les fichiers sur disque sont des artefacts dérivés, lisibles à l'humain.
- **Prompts générés dynamiquement** à partir d'un squelette commun et de paradigmes catégorisés en BDD.
- **Tous les prompts et briefings inter-agents sont en anglais.** La réponse à l'humain est rendue dans sa langue (détectée via `langdetect`).
- **Aucune invention** : si une info n'est pas vérifiable, elle est étiquetée comme telle.

## Paradigmes

Un paradigme est un fragment de prompt réutilisable, classé sous une section (`#`) et une catégorie (`##`). L'orchestrateur sélectionne ceux à injecter pour chaque agent et les rend dans le bloc `# DIRECTIVES` du prompt.

Sélection effective d'un paradigme pour un agent dans un mode donné :
1. Le paradigme est `is_global=1`, **ou** explicitement bound à l'agent via `agent_paradigms`.
2. **Et** soit aucune restriction de mode dans `paradigm_modes`, soit le mode courant figure dans la restriction.

Sections actuelles : `communication`, `reasoning`, `critical_thinking`, `process`, `code`, `safety`.

Voir `db/schema.sql` pour les seeds.

**Nota** : en plus des paradigmes en BDD, une partie des règles de comportement est hardcodée directement dans `prompts.py` :
- La description des outils de contrôle (`delegate_to`, `ask_human`, `return_to_user`) — notamment l'usage de `support_files` et le format JSON structuré du retour de `delegate_to`.
- Le bloc `# OUTPUT CONTRACT` injecté en fin de prompt système, **adapté au rôle** : un `finalizer` (synthesizer, archivist) ne voit que `return_to_user` ; un `router` ou `specialist` voit le set complet.

Ces zones sont intentionnellement hors BDD car elles définissent le protocole structurel du système, pas le comportement métier ou le style. Toute modification de ce protocole nécessite une intervention dans `prompts.py`.

## Squelette de prompt

Bloc unique `system` consolidé, contenant identité, contexte, directives, déclarations d'outils et contrat de sortie. Le mode pensée (`<|think|>`) est activé par défaut, désactivé pour les agents triviaux.

Détails et rationale dans `docs/PROMPT_SKELETON.md`.

## Échanges entre agents — le `briefing`

Les agents communiquent par **tool calls natifs Gemma 4**, jamais par texte libre :

- `delegate_to(agent_code, briefing, support_files, expected)` — passation à un autre spécialiste. Plusieurs `delegate_to` dans un même tour modèle sont traités séquentiellement par l'orchestrateur. Le retour est un objet structuré `{agent, artifact, answer}` — l'agent appelant utilise le champ `artifact` (filename relatif au dossier de conversation) pour le passer en `support_files` au prochain delegate, sans recopier le contenu.
- `ask_human(question, why)` — pause la requête, demande à l'humain. `why` obligatoire. Une seule question par requête.
- `return_to_user(answer)` — réponse finale.

Parsing structuré gratuit, zéro regex.

L'**archivist** est le seul agent non délégable : il est invoqué uniquement par l'orchestrateur, jamais par un autre agent.

## Récursion & garde-fous

- **Profondeur max** : `MAX_RECURSION_DEPTH = 5`. Compteur incrémenté uniquement par `delegate_to`. `ask_human`, l'appel au `synthesizer`, et l'appel à l'`archivist` n'incrémentent pas.
- Au-delà de 5, l'orchestrateur rejette les nouvelles délégations avec un `tool_response` d'erreur explicite et force l'agent à conclure.
- **Step budget** : `MAX_STEPS_PER_REQUEST = 8` itérations tool-call/tool-response par requête. Filet anti-tool-loop. Configurables dans `config.py`.
- **`ask_human`** : une seule par requête. Toute tentative supplémentaire reçoit un `tool_response` d'erreur.

## Persistance

Une conversation = un dossier plat horodaté :

```
conversations/2026-04-27_14-32_{conv_uuid}/
├── conversation.md                     # journal append-only humain-lisible
├── summary.md                          # mis à jour par l'archivist (modes chat/vocal uniquement)
├── HHMMSS_{agent_code}_prompt.md       # prompt système rendu
├── HHMMSS_{agent_code}_thought.md      # canal pensée capturé
├── HHMMSS_{agent_code}_briefing.md     # briefing émis vers un autre agent
├── HHMMSS_{agent_code}_tool_call.md
├── HHMMSS_{agent_code}_tool_response.md
├── HHMMSS_{agent_code}_ask_human.md
├── HHMMSS_{agent_code}_human_answer.md
└── HHMMSS_{agent_code}_response.md
```

Chaque fichier porte un frontmatter YAML minimal (`conversation_id`, `request_id`, `agent`, `kind`, `utc`).

Tri lexicographique = tri chronologique.

## Workspace agents

Les agents `document-builder` et `workspace-manager` peuvent manipuler des fichiers dans un sous-dossier `workspace/` de leur conversation. Ce dossier est sandboxé : impossible d'écrire dans les artefacts root ni d'en sortir par path traversal.

Outils disponibles : `workspace_create_file`, `workspace_str_replace`, `workspace_view`, `workspace_list`.

L'accès est opt-in par agent via deux grants BDD :
- `agent_tools` — liste les outils accordés
- `agent_workspace_grants` — active l'écriture (sans cette ligne, l'agent est read-only)

Quota : 256 Mo par workspace. Les fichiers workspace ne sont **pas** tracés dans la table `artifacts` — le filesystem est l'inventaire.

## Sandbox

L'outil `bash_sandbox` exécute des commandes shell dans un container Docker isolé, monté sur le workspace de la conversation.

Prérequis : `./jm.sh --build-docker` (une seule fois, ou après modification du Dockerfile).

Grants requis en BDD par agent :
- `agent_tools` — ligne avec `tool_code='bash_sandbox'`
- `agent_sandbox_grants` — une ligne par binaire autorisé (ex. `python3`, `jq`, `cat`)

Garanties matérielles :
- `--network=none` — pas d'accès internet
- `--cap-drop=ALL` — aucune capability Linux
- Utilisateur non-root
- Limites mémoire (512 Mo) et CPU (1 vCPU)

Audit : chaque tentative d'exécution (y compris les refus) est enregistrée dans la table `sandbox_executions` (queryable a posteriori). Le `tool_response` artifact dans le flux conversationnel capture les mêmes données en contexte.

## Stack

- Python 3.14 dans un venv local.
- SQLite (source de vérité, `jeanmichel.db`).
- Ollama 0.21+ (thinking natif depuis 0.9).
- CLI dynamique (`rich`, `prompt_toolkit`).
- Docker (optionnel, pour la sandbox d'exécution de code).
- API web prévue ultérieurement (FastAPI).

## Installation

```bash
./jm.sh --install       # crée le venv + initialise la BDD
./jm.sh                 # lance le CLI en interactif (mode analyse par défaut)
./jm.sh --mode chat     # conversation continue
./jm.sh --mode vocal    # réponses concises
./jm.sh --build-docker  # (optionnel) builde l'image sandbox Docker
```

Override du Python : `PYTHON_BIN=/path/to/python3.14 ./jm.sh --install`.

Override du modèle Ollama : `JEANMICHEL_MODEL=gemma4:4b ./jm.sh` ou `./jm.sh --model gemma4:4b`.

## Profil utilisateur

Édite `user_profile.toml` à la racine. Description libre injectée dans le bloc `# CONTEXT > Human` de chaque prompt. Exemple :

```toml
description = "L'humain auquel tu réponds est un mâle franco-canadien, la quarantaine, localisé à Montréal."
```

## Migrations DB

Les évolutions du schéma sont versionnées sous `db/migrate_NNN_*.sql`. Chaque migration est idempotente. Le `schema.sql` consolidé reflète l'état cible (toutes migrations appliquées) et peut être utilisé pour repartir à plat.

```bash
# Migration ciblée sur instance existante
sqlite3 jeanmichel.db < db/migrate_004_consolidate_critical_thinking.sql

# Repartir à plat
rm jeanmichel.db && sqlite3 jeanmichel.db < db/schema.sql
```

## Arborescence du repo

```
jeanmichel/
├── README.md
├── jm.sh                     # point d'entrée unifié (CLI, --install, --export-db, --clean, --inspect-conv, --paradigm-matrix, --admin)
├── pyproject.toml
├── user_profile.toml         # description libre de l'humain (édité localement)
├── db/
│   └── schema.sql            # schéma SQLite + paradigmes, agents, tool grants (seed consolidé)
├── debug/
│   ├── inspect_conv.py       # inspection des artefacts d'une conversation
│   ├── export_db.py          # dump SQL de la base de données
│   ├── admin.py              # administration DB
│   ├── clean_convs.py        # purge des anciennes conversations
│   └── paradigm_matrix.py    # visualisation matrice agents × paradigmes × modes
├── docker/
│   └── sandbox/
│       └── Dockerfile        # image Ubuntu 24.04 pour la sandbox d'exécution
├── docs/
│   ├── PROMPT_SKELETON.md    # squelette de prompt commenté
│   ├── GEMMA4.md             # référence des tokens et comportements Gemma 4
│   └── HOWTO_ADD_SPECIALIST_OR_TOOL.md  # notice d'implémentation pour agents IA
├── src/jeanmichel/
│   ├── cli.py                # interface rich (multi-ligne Alt+Enter, --mode)
│   ├── orchestrator.py       # boucle principale (générateur d'events, archivist post-loop)
│   ├── llm.py                # client Ollama + MockClient
│   ├── db.py                 # accès SQLite
│   ├── prompts.py            # rendu du squelette système (output contract adapté au rôle)
│   ├── tools/                # sous-package outils natifs Python
│   │   ├── __init__.py       # build_registry(conv_folder) → dict[str, ToolSpec]
│   │   ├── _base.py          # dataclass ToolSpec
│   │   ├── _workspace.py     # primitives partagées (path validation, quota)
│   │   ├── clock.py          # heure courante (stateless)
│   │   ├── conv_read_file.py # lecture fichier sandboxée (context-bound)
│   │   ├── weather.py        # météo via open-meteo (stateless)
│   │   ├── wikipedia.py      # recherche + lecture Wikipedia (stateless)
│   │   ├── workspace_create_file.py  # création fichier dans workspace (context-bound)
│   │   ├── workspace_str_replace.py  # édition atomique (context-bound)
│   │   ├── workspace_view.py         # lecture fichier/dossier (context-bound)
│   │   ├── workspace_list.py         # arbre workspace 2 niveaux (context-bound)
│   │   └── bash_sandbox.py           # exécution Docker sandboxée (context-bound)
│   ├── persistence.py        # écriture artefacts disque + frontmatter
│   ├── models.py             # dataclasses
│   └── config.py             # paths, constantes, user_profile loading
├── tests/
│   ├── conftest.py
│   ├── smoke.py              # smoke test du flow complet (MockClient)
│   └── demo_cli.py           # démo visuelle du rendu CLI
└── conversations/            # créé au runtime, un sous-dossier par conversation
```

## État

10 agents actifs : jean-michel, summarizer, weather-specialist, wikipedia-specialist, comparator-specialist, critical-thinker, document-builder, workspace-manager, synthesizer, archivist. Outils natifs : clock, conv_read_file, weather, wikipedia (search + get_page), workspace (create_file, str_replace, view, list), bash_sandbox (Docker). API web : non démarrée.

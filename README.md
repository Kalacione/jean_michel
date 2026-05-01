# Jean-Michel

Assistant IA local à agents spécialisés, prompts dynamiques et orchestration Python.

## Concept

Une requête humaine arrive à **Jean-Michel** (agent routeur). Il la formalise, la classe, puis :

- répond directement si la tâche est triviale (ex : heure courante via un outil),
- délègue à un ou plusieurs **agents spécialistes**.

Quand plusieurs spécialistes contribuent, l'agent **synthesizer** fusionne les sorties en une réponse unique pour l'humain, dans la langue détectée.

Agents actifs :
- **jean-michel** (router) — reçoit la requête, classe, route ou répond directement
- **summarizer** (specialist) — résumé de texte
- **weather-specialist** (specialist) — météo via open-meteo
- **wikipedia-specialist** (specialist) — recherche et extraction d'articles Wikipedia
- **comparator-specialist** (specialist) — orchestre des recherches parallèles sur plusieurs entités et produit un verdict comparatif structuré
- **synthesizer** (finalizer) — fusionne plusieurs réponses de spécialistes en une seule réponse cohérente

L'**orchestrateur** (code Python pur, pas un LLM) construit les prompts à la volée, dépile les requêtes, gère les statuts et persiste tout sur disque.

## Principes

- Modèles **100 % locaux** via Ollama.
- **Gemma 4** (mono-modèle au démarrage), tokens et thinking mode natifs.
- **DB = source de vérité** (SQLite). Les fichiers sur disque sont des artefacts dérivés, lisibles à l'humain.
- **Prompts générés dynamiquement** à partir d'un squelette commun et de paradigmes catégorisés en BDD.
- **Tous les prompts et briefings inter-agents sont en anglais.** La réponse à l'humain est rendue dans sa langue (détectée via `langdetect`).
- **Aucune invention** : si une info n'est pas vérifiable, elle est étiquetée comme telle.

## Paradigmes

Un paradigme est un fragment de prompt réutilisable, classé sous une section (`#`) et une catégorie (`##`). L'orchestrateur sélectionne ceux à injecter pour chaque agent (globaux + bindings explicites) et les rend dans le bloc `# DIRECTIVES` du prompt.

Sections actuelles : `communication`, `reasoning`, `process`, `code`, `safety`.

Voir `schema.sql` pour les seeds.

**Nota** : en plus des paradigmes en BDD, une partie des règles de comportement est hardcodée directement dans `prompts.py` :
- La description des outils de contrôle (`delegate_to`, `ask_human`, `return_to_user`) dans `CONTROL_TOOLS_SCHEMA` — notamment les instructions sur l'utilisation de `support_files` et `conv_read_file`.
- Le bloc `# OUTPUT CONTRACT` injecté en fin de chaque prompt système — qui précise les conditions d'appel de chaque outil de contrôle.

Ces deux zones sont intentionnellement hors BDD car elles définissent le protocole structurel du système (quelles fonctions appeler et dans quelles conditions), pas le comportement métier ou le style. Toute modification de ce protocole nécessite une intervention dans `prompts.py`.

## Squelette de prompt

Bloc unique `system` consolidé, contenant identité, contexte, directives, déclarations d'outils et contrat de sortie. Le mode pensée (`<|think|>`) est activé par défaut, désactivé pour les agents triviaux.

Détails et rationale dans `PROMPT_SKELETON.md`.

## Échanges entre agents — le `briefing`

Les agents communiquent par **tool calls natifs Gemma 4**, jamais par texte libre :

- `delegate_to(agent_code, briefing, support_files, expected)` — passation à un autre spécialiste. Plusieurs `delegate_to` dans un même tour modèle sont traités séquentiellement par l'orchestrateur.
- `ask_human(question, why)` — pause la requête, demande à l'humain. `why` obligatoire. Une seule question par appel.
- `return_to_user(answer)` — réponse finale.

Parsing structuré gratuit, zéro regex.

## Récursion & garde-fous

- Profondeur max : **5** (logique des « 5 pourquois »). Compteur incrémenté uniquement par `delegate_to`. `ask_human` et l'appel au `synthesizer` n'incrémentent pas.
- Au-delà de 5, l'orchestrateur rejette les nouvelles délégations et force l'agent à conclure en signalant explicitement la limite atteinte.
- Une seule `ask_human` par tour modèle. Une seconde est rejetée par l'orchestrateur.

## Persistance

Une conversation = un dossier plat horodaté :

```
conversations/2026-04-27_14-32_{conv_uuid}/
├── conversation.md                     # journal append-only
├── HHMMSS_{agent_code}_prompt.md       # prompt rendu
├── HHMMSS_{agent_code}_thought.md      # canal pensée capturé
├── HHMMSS_{agent_code}_briefing.md     # briefing émis
├── HHMMSS_{agent_code}_tool_call.md
├── HHMMSS_{agent_code}_tool_response.md
├── HHMMSS_{agent_code}_ask_human.md
├── HHMMSS_{agent_code}_human_answer.md
└── HHMMSS_{agent_code}_response.md
```

Chaque fichier porte un frontmatter YAML minimal (`conversation_id`, `request_id`, `agent`, `kind`, `utc`).

Tri lexicographique = tri chronologique.

## Stack

- Python 3.14 dans un venv local.
- SQLite (source de vérité, `jeanmichel.db`).
- Ollama 0.21+ (thinking natif depuis 0.9).
- CLI dynamique (`rich`, `prompt_toolkit`).
- API web prévue ultérieurement (FastAPI).

## Installation

```bash
./jm.sh --install   # crée le venv + initialise la BDD
./jm.sh             # lance le CLI en interactif
```

Override du Python : `PYTHON_BIN=/path/to/python3.14 ./jm.sh --install`.

Override du modèle Ollama : `JEANMICHEL_MODEL=gemma4:4b ./jm.sh` ou `./jm.sh --model gemma4:4b`.

## Profil utilisateur

Édite `user_profile.toml` à la racine. Description libre injectée dans le bloc `# CONTEXT > Human` de chaque prompt. Exemple :

```toml
description = "L'humain auquel tu réponds est un mâle franco-canadien, la quarantaine, localisé à Montréal."
```

## Arborescence du repo

```
jeanmichel/
├── README.md
├── jm.sh                     # point d'entrée unifié (CLI, --install, --export-db, --clean, --inspect-conv)
├── pyproject.toml
├── user_profile.toml         # description libre de l'humain (édité localement)
├── db/
│   └── schema.sql            # schéma SQLite + paradigmes, agents, tool grants (seed)
├── debug/
│   ├── inspect_conv.py       # inspection des artefacts d'une conversation
│   ├── export_db.py          # dump SQL de la base de données
│   ├── admin.py              # administration DB
│   └── clean_convs.py        # purge des anciennes conversations
├── docs/
│   ├── PROMPT_SKELETON.md    # squelette de prompt commenté
│   ├── GEMMA4.md             # référence des tokens et comportements Gemma 4
│   └── HOWTO_ADD_SPECIALIST_OR_TOOL.md  # notice d'implémentation pour agents IA
├── src/jeanmichel/
│   ├── cli.py                # interface rich (multi-ligne Alt+Enter)
│   ├── orchestrator.py       # boucle principale (générateur d'events)
│   ├── llm.py                # client Ollama + MockClient
│   ├── db.py                 # accès SQLite
│   ├── prompts.py            # rendu du squelette système
│   ├── tools/                # sous-package outils natifs Python
│   │   ├── __init__.py       # build_registry(conv_folder) → dict[str, ToolSpec]
│   │   ├── _base.py          # dataclass ToolSpec
│   │   ├── clock.py          # heure courante (stateless)
│   │   ├── conv_read_file.py # lecture fichier sandboxée (context-bound)
│   │   ├── weather.py        # météo via open-meteo (stateless)
│   │   └── wikipedia.py      # recherche + lecture Wikipedia (stateless)
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

MVP fonctionnel. Agents actifs : jean-michel, summarizer, weather-specialist, wikipedia-specialist, comparator-specialist, synthesizer. Outils natifs : clock, conv_read_file, weather, wikipedia (search + get_page). API web non démarrée.

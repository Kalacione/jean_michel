# graphify — évaluation étape 1 (essai dev-only, code-only local)

**Date** : 2026-06-08 · **Version testée** : graphify `0.8.35` · **Verdict : GO comme outil dev
local** (extraction code-only déterministe + nommage des communautés 100 % local via Ollama,
validés). **Étape 2 (tool MCP pour jean-michel) : VIABLE** — le serveur MCP HTTP existe bien
(`python -m graphify.serve … --transport http`), juste pas exposé dans `--help`. Voir §Étape 2.

**Modèle Ollama retenu : `qwen2.5-coder:7b`** (installé). C'est le **défaut natif** du backend
ollama de graphify (donc zéro override `OLLAMA_MODEL`), code-tuned : noms de communautés
précis et en anglais propre (« Main Agent Loop », « Persistence Layer », « Request Dispatcher »,
« Git Snapshots for Conversations », « ALEXA Decision Execution »…), ~48 s pour 290 communautés.
Nettement meilleur que `granite4.1:8b` (qui glissait en franglais). ~4.7 Go, local, gratuit.

## Setup retenu

Installé **hors** du `.venv` 3.14 du projet, via uv avec un Python managé 3.12.
L'extra `[openai]` est requis pour piloter Ollama (API OpenAI-compatible `/v1`) :

```bash
uv tool install "graphifyy[openai]" --python 3.12   # exécutable: graphify (PyPI: graphifyy)
```

- **Compat Python 3.14** : non testée directement — contournée proprement. uv provisionne un
  CPython 3.12 isolé ; toutes les deps (tree-sitter ×28, numpy/scipy, rapidfuzz) s'installent
  sans build cassé. graphify étant un **process externe** (pas d'API librairie), notre venv 3.14
  n'est jamais touché. Recommandation : rester sur cet env isolé (ou Docker).
- `.graphifyignore` à la racine (committé) exclut `.venv/`, `conversations/`,
  `web/node_modules/`, `web/dist/`, `voice_models/`, `dist/`, `*.db`, `graphify-out/`.
- `graphify-out/` ajouté à `.gitignore` (artefact build régénérable).

### ✅ Recette validée — naming des communautés 100 % local via Ollama

Confirmé en pratique : nommage des **290 communautés en ~50 s**, **aucune clé cloud**, en
réutilisant un modèle qu'on a déjà (`granite4.1:8b`, le modèle dispatcher). Les noms sont
justes (« Agent Lifecycle Management », « Conversation Memory Isolation », « Workspace File
Operations », « Code Mode and Sandbox Strategy », « Bootstrap User Memory »…).

Le pilotage se fait par **2 variables d'env** (les flags `--backend`/`--model` de `label` sont
ignorés dans la 0.8.35 — ne PAS compter dessus) :

```bash
export PATH="$HOME/.local/bin:$PATH"
unset OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY   # aucune clé cloud (sinon graphify les préfère)
export OLLAMA_BASE_URL="http://localhost:11434/v1"      # sa PRÉSENCE => detect_backend() choisit 'ollama'
# OLLAMA_MODEL : inutile désormais — qwen2.5-coder:7b est le DÉFAUT du backend ollama, et il est installé.
# (override possible : export OLLAMA_MODEL="granite4.1:8b" pour réutiliser le modèle dispatcher.)

graphify update .          # 1) graphe code-only déterministe (tree-sitter, no LLM) — ~5 s
graphify label .           # 2) nomme les communautés via Ollama local (qwen2.5-coder:7b) — ~48 s, 0 token cloud
```

Pourquoi ces 2 vars précisément (lu dans `graphify/llm.py`) :
- backend ollama : `base_url = OLLAMA_BASE_URL | "http://localhost:11434/v1"` ;
  `default_model = OLLAMA_MODEL | "qwen2.5-coder:7b"` (modèle PAS installé chez nous → d'où
  l'override). `env_key = OLLAMA_API_KEY` (placeholder, ignoré par Ollama).
- `detect_backend()` ne retient ollama qu'**en dernier** et seulement si une de ses vars est
  présente (sécurité F-002 : une clé payante n'est jamais shadowée par un OLLAMA_BASE_URL traînant).
- Déterminisme : le **graphe reste déterministe** (tree-sitter) ; seuls les **noms** de
  communautés sont générés par LLM (non déterministes, franglais occasionnel avec un petit
  modèle — cosmétique, les requêtes `explain`/`path`/`affected` n'en dépendent pas).
- Optionnel : `GRAPHIFY_OLLAMA_NUM_CTX`, `GRAPHIFY_OLLAMA_KEEP_ALIVE=30m` pour tuner.

## Résultats — extraction code-only

```bash
graphify update .    # "re-extracting code files (no LLM needed)"
```

- **~5 s**, 24 workers, 371 fichiers → **3750 nodes · 6111 edges · 290 communities**.
- **0 token, 0 clé API, 0 appel LLM** · 92 % EXTRACTED / 8 % INFERRED (495 edges inférés, conf. moy. 0.52).
- Sorties `graphify-out/` : `graph.json` (3.5 Mo), `graph.html` (3.3 Mo, viz interactive),
  `GRAPH_REPORT.md` (87 Ko), `manifest.json`, `cache/` (SHA256 → rebuilds incrémentaux).
- Rebuild incrémental : `graphify update .` après changements (gratuit, déterministe).

## Qualité (ce qui marche bien)

- **God Nodes exacts** : `ToolSpec` (81), `tool_error()`/`tool_ok()`, `ToolCall`,
  `ConversationState`, `run_main_loop()`, `LLMResponse` — c'est bien le cœur réel du projet,
  produit **sans LLM**.
- **`explain "<node>"`** : localisation source exacte (`orchestrator_v2.py:723`), communauté,
  et les 37 arêtes typées (`calls`/`references`/`imports`, tag EXTRACTED). Très utile, fiable.
- **`path "A" "B"`** : chemin le plus court avec arêtes labellisées (ex. `run_main_loop()` →…→
  `ToolSpec` en 5 hops, via `turn_runner` → `mcp_client` → `MCPManager`). Correct.
- **`affected "X"`** : traversée inverse (impact) — utile pour « qu'est-ce qui casse si je touche X ».
- Sections rapport : God Nodes, Surprising Connections, **Import Cycles**, communautés.

## Limites / frictions (la part critique)

1. **Communautés non nommées sans LLM** — ⟶ **RÉSOLU localement** : sans backend, les 290
   communautés restent `Community N`. Mais le nommage tourne **100 % en local via Ollama**
   (`qwen2.5-coder:7b`, ~48 s, 0 token cloud) — voir la recette validée ci-dessus. Donc plus une
   vraie limitation pour nous. (Piège : `--backend ollama`/`--model` sur `label` sont ignorés
   dans la 0.8.35 ; utiliser les variables d'env `OLLAMA_BASE_URL` (+ `OLLAMA_MODEL` si override).)
2. **Ambiguïté de labels** sur `path`/`affected` : `affected "ConversationState"` → « No unique
   node match » (def + références multiples). Il faut viser un label unique ou l'ID de node
   (`jeanmichel_orchestrator_v2_run_main_loop`). Friction réelle pour un usage agent/LLM.
3. **`query` bruité par les docs** : la BFS de `query "<question>"` part de matches texte et
   ramène des nœuds Markdown (HOWTO, `docs/system_prompts/claude-code.md`) mêlés au code.
   Pour un graphe purement structurel, exclure aussi `*.md`/`docs/` du `.graphifyignore` ;
   sinon accepter que les docs ajoutent du contexte navigationnel.
4. **Étape 2 (MCP) — PAS bloquée** (correction d'une conclusion hâtive) : le serveur MCP
   n'est pas une sous-commande CLI mais le **module** `graphify/serve.py`, lançable via
   `python -m graphify.serve …`. Il supporte `--transport http` (Streamable HTTP) — voir §Étape 2.
   Seul prérequis : ajouter le paquet `mcp` à l'env (`uv tool install "graphifyy[openai]"
   --with mcp` ; `import mcp` échoue sinon). La voie « skill IDE » (`graphify install`) écrit un
   hook PreToolUse dans `CLAUDE.md` — non pertinent pour notre orchestrateur maison ; on vise
   le serveur HTTP + notre client MCP existant.

## Recommandation

- **Adopter comme outil DEV local maintenant** : `graphify update .` à la demande pour
  naviguer/auditer notre propre codebase (God Nodes, cycles, `explain`/`path`/`affected`).
  Coût ~0, déterministe, local. Le `.graphifyignore` est committé ; `graphify-out/` ignoré.
- **Étape 2 (MCP pour jean-michel) : VIABLE, à prototyper.** Le serveur MCP HTTP existe
  (`graphify/serve.py`). Aucun code cœur nouveau côté jean-michel — on réutilise le client MCP
  opt-in. Voir la recette §Étape 2 ci-dessous. Pré-requis : paquet `mcp` dans l'env graphify.
- **Étape 3 (codebase arbitraire)** : reste différée.

## Étape 2 — recette de lancement (MCP HTTP, à valider)

```bash
# 1) ajouter le paquet mcp à l'env isolé de graphify
uv tool install "graphifyy[openai]" --with mcp --python 3.12 --force

# 2) (pré-requis) un graphe construit : graphify update .  (+ graphify label . pour les noms)

# 3) servir le graphe en MCP Streamable HTTP, scopé à NOTRE repo, avec auth
GRAPHIFY_API_KEY="$(openssl rand -hex 16)" \
  python -m graphify.serve graphify-out/graph.json \
    --transport http --host 127.0.0.1 --port 8080 --path /mcp --api-key "$GRAPHIFY_API_KEY"
```

Côté jean-michel (`mcp_servers.toml`, gitignoré ; plomberie MCP déjà en place) :
```toml
[servers.graphify]
url = "http://127.0.0.1:8080/mcp"
category = "code"            # => exposé à jean-michel + code-fetcher (mapping existant)
auth_env = "GRAPHIFY_MCP_TOKEN"   # même valeur que GRAPHIFY_API_KEY ci-dessus
```
→ outils exposés en `mcp__graphify__{query_graph,get_node,get_neighbors,get_community,god_nodes,
graph_stats,shortest_path,…}`, gatés par les grants existants. **À valider** : compat exacte du
schéma d'auth (header) entre notre client MCP et `serve.py`, et fraîcheur du graphe (git hook
`graphify hook install` ou `graphify update .` au commit).

## Commandes utiles
```bash
export PATH="$HOME/.local/bin:$PATH"
# naming local (optionnel) — 1 var suffit (modèle défaut qwen2.5-coder:7b installé), pas de clé cloud :
export OLLAMA_BASE_URL="http://localhost:11434/v1"

graphify update .                      # rebuild code graph (tree-sitter, no LLM) — déterministe
graphify label .                       # nomme les communautés via Ollama local (qwen2.5-coder:7b, ~48 s)
graphify explain "run_main_loop()"     # node + voisins typés
graphify path "A()" "B()"              # plus court chemin
graphify affected "X()"                # impact inverse (label unique requis)
```

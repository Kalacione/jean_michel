# graphify — évaluation étape 1 (essai dev-only, code-only local)

**Date** : 2026-06-08 · **Version testée** : graphify `0.8.35` · **Verdict : GO comme outil dev
local** (extraction code-only déterministe + nommage des communautés 100 % local via Ollama,
validés). Étape 2 (tool MCP pour jean-michel) reste à revalider — voir blocage ci-dessous.

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
export OLLAMA_MODEL="granite4.1:8b"                     # réutilise notre modèle local (override du défaut)

graphify update .          # 1) graphe code-only déterministe (tree-sitter, no LLM) — ~5 s
graphify label .           # 2) nomme les communautés via Ollama local — ~50 s, 0 token cloud
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
   (granite4.1:8b, ~50 s, 0 token cloud) — voir la recette validée ci-dessus. Donc plus une
   vraie limitation pour nous. (Piège : `--backend ollama`/`--model` sur `label` sont ignorés
   dans la 0.8.35 ; utiliser les variables d'env `OLLAMA_BASE_URL` + `OLLAMA_MODEL`.)
2. **Ambiguïté de labels** sur `path`/`affected` : `affected "ConversationState"` → « No unique
   node match » (def + références multiples). Il faut viser un label unique ou l'ID de node
   (`jeanmichel_orchestrator_v2_run_main_loop`). Friction réelle pour un usage agent/LLM.
3. **`query` bruité par les docs** : la BFS de `query "<question>"` part de matches texte et
   ramène des nœuds Markdown (HOWTO, `docs/system_prompts/claude-code.md`) mêlés au code.
   Pour un graphe purement structurel, exclure aussi `*.md`/`docs/` du `.graphifyignore` ;
   sinon accepter que les docs ajoutent du contexte navigationnel.
4. **⚠ Bloquant pour l'étape 2 (MCP)** : la CLI `0.8.35` n'expose **PAS** de commande `serve`
   ni de flag `--mcp`/`--transport` (vérifié : absent de `--help`). Le « serveur MCP HTTP »
   évoqué par des sources tierces n'est donc pas disponible tel quel dans cette version.
   L'intégration MCP de l'étape 2 doit être **revalidée** : identifier la vraie version/commande
   (`python -m graphify.serve` ? skill-based hooks ? version plus récente ?) AVANT de s'engager.
   La voie « skill IDE » (`graphify install --platform claude`) écrit un hook PreToolUse dans
   `CLAUDE.md` — non pertinent pour notre orchestrateur maison.

## Recommandation

- **Adopter comme outil DEV local maintenant** : `graphify update .` à la demande pour
  naviguer/auditer notre propre codebase (God Nodes, cycles, `explain`/`path`/`affected`).
  Coût ~0, déterministe, local. Le `.graphifyignore` est committé ; `graphify-out/` ignoré.
- **Étape 2 (MCP pour jean-michel) : NE PAS lancer en l'état.** Bloquée par l'absence de
  serveur MCP HTTP dans la version installée. À rouvrir seulement après avoir confirmé une
  commande `serve --transport http` réelle (sinon il faudrait wrapper la CLI
  `query`/`path`/`explain`/`affected` dans un tool natif — option de repli, plus de code).
- **Étape 3 (codebase arbitraire)** : reste différée.

## Commandes utiles
```bash
export PATH="$HOME/.local/bin:$PATH"
# naming local (optionnel) — 2 vars suffisent, pas de clé cloud :
export OLLAMA_BASE_URL="http://localhost:11434/v1"
export OLLAMA_MODEL="granite4.1:8b"

graphify update .                      # rebuild code graph (tree-sitter, no LLM) — déterministe
graphify label .                       # nomme les communautés via Ollama local (~50 s)
graphify explain "run_main_loop()"     # node + voisins typés
graphify path "A()" "B()"              # plus court chemin
graphify affected "X()"                # impact inverse (label unique requis)
```

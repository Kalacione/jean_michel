# graphify — évaluation étape 1 (essai dev-only, code-only local)

**Date** : 2026-06-08 · **Version testée** : graphify `0.8.35` · **Verdict : GO conditionnel**
(garder comme outil dev local ; étape 2 MCP à revalider — voir blocage ci-dessous).

## Setup retenu

Installé **hors** du `.venv` 3.14 du projet, via uv avec un Python managé 3.12 :

```bash
uv tool install graphifyy --python 3.12   # exécutable: graphify (PyPI: graphifyy)
```

- **Compat Python 3.14** : non testée directement — contournée proprement. uv provisionne un
  CPython 3.12 isolé ; toutes les deps (tree-sitter ×28, numpy/scipy, rapidfuzz) s'installent
  sans build cassé. graphify étant un **process externe** (pas d'API librairie), notre venv 3.14
  n'est jamais touché. Recommandation : rester sur cet env isolé (ou Docker).
- `.graphifyignore` à la racine (committé) exclut `.venv/`, `conversations/`,
  `web/node_modules/`, `web/dist/`, `voice_models/`, `dist/`, `*.db`, `graphify-out/`.
- `graphify-out/` ajouté à `.gitignore` (artefact build régénérable).

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

1. **Communautés non nommées sans LLM** : les 290 communautés restent `Community N`
   (le *naming* nécessite un backend LLM). Le rapport perd en lisibilité « narrative » ;
   la structure (nodes/edges/queries) reste pleinement exploitable. Option : `graphify label .
   --backend ollama` plus tard pour nommer via Ollama.
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
graphify update .                      # rebuild code graph (no LLM)
graphify explain "run_main_loop()"     # node + voisins typés
graphify path "A()" "B()"              # plus court chemin
graphify affected "X()"                # impact inverse
# (optionnel, LLM) graphify label . --backend ollama   # nommer les communautés
```

# Évaluation — graphify pour Jean-Michel (repo + outil code mode)

## Context

L'utilisateur a repéré **graphify** (https://github.com/safishamsi/graphify) et veut savoir
si ça bénéficie (a) à la gestion de NOTRE repo et (b) comme outil pour jean-michel en mode
`code` (« codebase capable »). Il demande une analyse honnête — *y compris si c'est une idée
à la con*. On est sur une nouvelle branche dédiée (main poussé).

graphify = skill/CLI/serveur Python qui transforme un dossier (code, SQL, docs, PDF, images)
en **graphe de connaissance interrogeable**. Extraction de code **locale via tree-sitter**
(28 langages, sans clé API), enrichissement sémantique via un LLM (cloud **ou Ollama local**,
`--backend ollama`). Sorties dans `graphify-out/` : `graph.json`, `graph.html` (viz),
`GRAPH_REPORT.md`. Détection de communautés (Leiden), diagrammes Mermaid.

**Interfaces** : skill IDE (`/graphify .`), CLI (`extract`/`query`/`path`/`explain`),
**serveur MCP HTTP** (`graphify serve graph.json --transport http --api-key …`, + Docker),
git hooks (`--watch`, rebuild post-commit). Outils MCP exposés : `query_graph`, `get_node`,
`get_neighbors`, `shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs`.

## Verdict : NON, pas une idée à la con — mais à cadrer serré

C'est un **bon fit architectural**, pour une raison précise et non accidentelle : le
**transport MCP HTTP** de graphify se branche tel quel sur le **client MCP opt-in déjà
présent** dans jean-michel (`src/jeanmichel/mcp_client.py`, `mcp_servers.toml`, catégorie →
agents). Aucune refonte du cœur. Et l'alignement **Ollama / local-first / déterministe**
colle à la philosophie du projet (les ops `get_node`/`get_neighbors`/`shortest_path` sont du
graphe AST, pas des devinettes LLM).

**Ça comble un vrai trou** : le mode `code` a `code-fetcher` (lookup externe : GitHub/SO/PyPI)
et `code-runner` (écrit + exécute en sandbox), mais **rien pour comprendre STRUCTURELLEMENT une
codebase**. graphify = la couche « naviguer/comprendre un gros repo » manquante → sert
directement l'objectif « codebase capable ».

### Pourquoi ce n'est PAS con
1. **Zéro changement de cœur** : intégration = une entrée `mcp_servers.toml` + un graphe servi
   en HTTP. Les outils apparaissent en `mcp__graphify__*`, gatés par les grants existants.
2. **Le problème de compat Python 3.14 s'évapore** : graphify tourne comme **process séparé**
   (son propre venv / Docker) ; jean-michel ne fait que parler HTTP. Pas d'import dans notre
   venv 3.14 (et il n'y a de toute façon **pas d'API librairie**).
3. **Déterministe + local** : extraction code = tree-sitter (offline, sans clé) ;
   enrichissement possible 100 % Ollama. Cohérent avec nos contraintes.
4. **Valeur immédiate pour NOTRE repo**, indépendamment de toute intégration : un graphe +
   `GRAPH_REPORT.md` de l'orchestrateur/hooks/tools/service aide à naviguer le mille-feuille.
5. **Coût d'essai quasi nul** et réversible (process externe, rien de committé côté cœur).

### Où ça peut foirer / les vraies limites (la part « idée à la con »)
1. **Ça lit/comprend, ça n'édite pas.** « Gérer une codebase » (refactor, PR, écrire du code)
   reste le boulot de `code-runner`/workspace/git. graphify n'est **que la moitié
   compréhension**. Ne pas survendre l'ambition « gérer une codebase ».
2. **QUELLE codebase ?** jean-michel travaille dans un `workspace/` **par conversation**
   (sandbox, éphémère, quota 256 Mo). graphify veut indexer un **vrai dossier persistant**.
   Les deux modèles ne s'alignent pas. Sans décider la cible (notre repo ? un checkout fourni ?),
   l'outil n'a **rien d'utile à interroger** → c'est LÀ que ça devient con si on force trop tôt.
3. **Fraîcheur du graphe** : `graph.json` se périme ; il faut le rebuild (hook/`--watch`).
   Opérationnellement gérable sur NOTRE repo, lourd sur un repo arbitraire à la volée.
4. **Maturité/churn** : projet très actif et jeune (releases quasi quotidiennes, beaucoup de
   PR ouvertes) → API mouvante. **Épingler une version** est obligatoire.
5. **Sécurité** : ne PAS donner à jean-michel un accès FS hôte arbitraire. Le serveur graphify
   (qu'on contrôle, scopé à un repo choisi) contient ça ; l'auth `--api-key` → `auth_env`.
6. **Enrichissement non-code = cloud** (docs/PDF/images). Pour rester local/gratuit/déterministe :
   se limiter à l'**extraction code** (+ Ollama si enrichissement souhaité).

## Recommandation : approche étagée — DÉCISION VERROUILLÉE

**Périmètre validé avec l'utilisateur : Étape 1 SEULEMENT (essai dev-only), extraction
code-only 100 % locale (tree-sitter, aucune clé, aucun LLM).** Les étapes 2–3 restent
documentées mais **différées**, conditionnées au go/no-go de l'étape 1.

### Étape 1 — Essai comme OUTIL DEV sur notre repo  ← LE TRAVAIL À FAIRE
But : juger si la sortie est réellement utile AVANT toute intégration. Risque ~0, réversible
(process externe, rien de committé côté cœur sauf un `.graphifyignore` optionnel).

1. **Installer graphify en environnement ISOLÉ** — jamais dans le `.venv` 3.14 du projet :
   `pipx install graphifyy` (ou `uv tool install graphifyy`, ou l'image Docker). Vérifier
   `graphify --version` hors `.venv`. **Noter la compat Python 3.14** (wheels tree-sitter /
   igraph-leiden) — si ça casse en pipx, retomber sur Docker.
2. **`.graphifyignore`** à la racine : exclure le non-code et le volumineux —
   `conversations/`, `web/node_modules/`, `web/dist/`, `voice_models/`, `.venv/`, `dist/`,
   `*.db`, `db/migrations/` (optionnel). Garde l'extraction **code-only** et rapide.
3. **Extraction code-only, sans backend LLM ni clé** : `graphify .` (extraction AST locale ;
   ne pas définir de clé API ni `--backend` cloud). Confirmer pendant l'essai le drapeau exact
   d'un mode strictement code-only si l'outil tente d'enrichir des docs.
4. **Inspecter les sorties** `graphify-out/` : ouvrir `graph.html`, lire `GRAPH_REPORT.md`.
   Critères de jugement :
   - le **call-flow** `orchestrator_v2.py` → `spawn_subagent` / `run_main_loop` est-il correct ?
   - les **« god nodes »** (prompts.py, db.py, orchestrator) et **communautés** reflètent-ils
     l'archi réelle (Tier 0/1/2, hooks, service/, tools/) ?
   - les liens `service/turn_runner` ↔ `consolidation`/`memory` apparaissent-ils ?
5. **Tester quelques requêtes** : `graphify query "comment un subagent est-il spawné ?"`,
   `graphify path "run_turn" "spawn_subagent"`, `graphify explain "PreToolUse"`.
6. **Livrable** : une note `DevNotes/GRAPHIFY/01_eval.md` (verdict go/no-go : qualité de la
   sortie, temps d'extraction, taille du graphe, compat 3.14, friction) + le `.graphifyignore`
   conservé seulement si go. **C'est le point de décision pour l'étape 2.**

Fichiers touchés (étape 1) : `.graphifyignore` (racine), `DevNotes/GRAPHIFY/01_eval.md`.
**Aucune modification du cœur jean-michel.** `graphify-out/` est un artefact build → l'ajouter
à `.gitignore` (ou le couvrir par un ignore existant).

---
### Étape 2 (DIFFÉRÉE) — Intégration opt-in via MCP HTTP (si l'étape 1 convainc)
Réutilise INTÉGRALEMENT la plomberie MCP existante — pas de code cœur nouveau :
- Lancer le serveur : `graphify serve graphify-out/graph.json --transport http --api-key $TOKEN`
  (process/Docker séparé, scopé à **notre repo** d'abord — auto-introspection).
- Ajouter à `mcp_servers.toml` (gitignoré) :
  ```toml
  [servers.graphify]
  url = "http://localhost:<port>/mcp"
  category = "code"
  auth_env = "GRAPHIFY_MCP_TOKEN"
  ```
  `[categories] code = ["jean-michel", "code-fetcher"]` (déjà le cas) → outils
  `mcp__graphify__query_graph` etc. exposés et gatés automatiquement.
- 1 paradigme de routing (migration BDD, anglais, model-agnostic) côté `code-fetcher` /
  jean-michel : « pour une question STRUCTURELLE sur la codebase (qui appelle quoi, impact d'un
  changement, où vit X), interroger le graphe (`mcp__graphify__*`) avant de grep à l'aveugle ».
- Doc : `mcp_servers.example.toml` + section README (le pattern « serveur hébergé » existe déjà).
- Garde-fous : version graphify épinglée ; rebuild du graphe via git hook ; kill-switch
  `JEANMICHEL_MCP_DISABLED=1` déjà présent.

### Étape 3 (DIFFÉRÉE) — « Gérer une codebase arbitraire » (ambitieux)
Indexer un repo cible fourni par l'utilisateur, cycle de vie du graphe, build à la demande,
modèle de checkout persistant. **Hors périmètre tant que 1–2 n'ont pas prouvé la valeur.**
Ne pas s'y lancer maintenant (c'est là que le rapport simplicité/bénéfice se dégrade).

## Vérification (étape 1, code-only local)
- Env isolé : `pipx install graphifyy` puis `graphify --version` (hors `.venv`). Si échec
  d'install sur 3.14 → tester l'image Docker ; consigner le résultat.
- `graphify .` (sans clé, sans backend cloud) → présence de `graphify-out/graph.json` +
  `graph.html` + `GRAPH_REPORT.md`.
- Ouvrir le HTML : vérifier que le call-flow `orchestrator_v2.py` / `hooks.py` / `service/`
  et les communautés reflètent l'archi réelle.
- Requêtes de contrôle : `graphify query "comment un subagent est-il spawné ?"`,
  `graphify path "run_turn" "spawn_subagent"`.
- Mesurer temps d'extraction, taille du graphe, pertinence. Conclusion go/no-go dans
  `DevNotes/GRAPHIFY/01_eval.md`.

## Décisions verrouillées
- **Scope : étape 1 dev-only uniquement** (intégration MCP différée, conditionnée au go).
- **Backend : code-only tree-sitter, 100 % local** (aucune clé, aucun LLM, déterministe).

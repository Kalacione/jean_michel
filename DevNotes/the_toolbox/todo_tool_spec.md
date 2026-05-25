# Spec — Outil `manage_todo_list` pour Jean-Michel

**Statut** : draft initial — sources externes glanées + intégration codebase.
**Auteur** : analyse Copilot (2026-05-25).
**Contexte** : la méta-cognition de Jean-Michel est aujourd'hui **réactive**
(décision après chaque retour d'agent). Pour les requêtes comparatives /
recherches croisées / pipelines multi-étapes, cette linéarité bride la
qualité : pas de vue d'ensemble, pas de parallélisation, oubli en cours de
route ("context rot"). Lui donner un outil de TODO list explicite — couplé
au `plan.md` déjà vivant — règle ces trois problèmes d'un coup.

---

## 1. État de l'art — sources

### 1.1 Claude Code / Anthropic
- **TodoWrite → TaskCreate / TaskUpdate / TaskList / TaskGet** (migration depuis
  SDK TS 0.3.142 / Claude Code v2.1.142). Schéma item : `{ subject, description,
  activeForm?, status, metadata? }`, statut ∈ `pending | in_progress | completed`
  (+ `deleted` via update). Le `taskId` est attribué dans le `tool_result`, pas
  l'input. — [code.claude.com/docs/en/agent-sdk/todo-tracking](https://code.claude.com/docs/en/agent-sdk/todo-tracking)
- **Auto-trigger** : "Complex multi-step tasks requiring 3 or more distinct
  actions / user-provided task lists / non-trivial operations" — même source.
- **Lifecycle** : `pending → in_progress → completed` ; suppression du groupe
  quand tout est terminé.

### 1.2 VSCode Copilot Chat / `manage_todo_list`
- Outil unique avec deux opérations : `read` (snapshot courant) et `write`
  (remplacement complet de la liste).
- Schéma : `{ id, title, description, status }` avec statut ∈
  `not-started | in-progress | completed`.
- Règle workflow : "plan → mark in-progress → complete → mark completed → repeat",
  un seul `in-progress` à la fois (mais ré-entrant via subagents).
- Réimplémentation open-source documentée : [github.com/tintinweb/pi-manage-todo-list](https://github.com/tintinweb/pi-manage-todo-list)
  — détaille schéma exact, alertes sur listes < 3 items, persistance par session.

### 1.3 Pattern `write_todos` — analyse design
- Synthèse claire (Adaptive Engineer, nov. 2025) :
  [newsletter.adaptiveengineer.com/p/this-is-the-powerful-pattern-behind](https://newsletter.adaptiveengineer.com/p/this-is-the-powerful-pattern-behind)
- 3 raisons d'externaliser le plan :
  1. **Transparence / debuggability** — humain (et autres agents) lit le plan
     avant exécution.
  2. **Tolérance aux pannes** — reprise après crash en lisant l'état persisté.
  3. **Human-in-the-loop** — révision/modification du plan avant action.
- Cas typiques : tâches ≥ 3 étapes, opérations longues (minutes-heures),
  enjeux élevés (mutations, dépenses), workflows collaboratifs.
- Anti-cas : question single-step ("quelle heure ?"), latence sub-seconde,
  opérations idempotentes triviales.
- Cité explicitement : Manus réécrit régulièrement sa TODO list pour
  contrer la dérive de contexte sur > 50 tool calls.

### 1.4 Plan-and-Execute / ReWOO
- **Plan-and-Execute** (LangChain, 2023) : planner LLM up-front → executor pas
  à pas → optional re-planner après observation.
  [langchain.com/blog/planning-agents](https://www.langchain.com/blog/planning-agents)
- **ReWOO** (Xu et al.) : un seul appel planner émet la liste complète de
  steps avec **variables d'assignation** (sortie de step N réutilisable en
  step N+1 sans relancer le LLM). Réduit drastiquement les tokens.
  [agent-patterns.readthedocs.io/en/stable/patterns/rewoo.html](https://agent-patterns.readthedocs.io/en/stable/patterns/rewoo.html)
- **BabyAGI** (Nakajima, 2023) : boucle planner → task_creation → prioritizer
  → executor. Pionnier de la to-do persistante côté agent.
  [github.com/yoheinakajima/babyagi](https://github.com/yoheinakajima/babyagi)

### 1.5 Implémentations existantes — outils prêts à étudier
| Source | Pertinence pour nous |
|---|---|
| [github.com/sigoden/llm-functions — Todo Agent](https://deepwiki.com/sigoden/llm-functions/4.3.4-todo-agent) | Agent dédié, opérations CRUD natural-language |
| [pypi.org/project/llm-tools-todo](https://pypi.org/project/llm-tools-todo/) | Plugin pour `llm` de Simon Willison, six ops, session-scoped (≈ Claude Code) |
| [pkg.go.dev/.../trpc-agent-go/tool/todo](https://pkg.go.dev/trpc.group/trpc-go/trpc-agent-go/tool/todo) | Tool session-scoped en Go — bonne référence d'API minimaliste |
| [github.com/mayf3/llm-todo](https://github.com/mayf3/llm-todo) | Local-first, SQLite, planification long terme |

---

## 2. Analyse codebase

### 2.1 Ce qui existe déjà
- **`plan.md` vivant**, écrit déterministiquement par
  [src/jeanmichel/plan_writer.py](src/jeanmichel/plan_writer.py) à partir
  des `delegate_to` du router. Steps `S1`, `S1.1`, etc., avec statut
  (`in_progress | done | blocked | partial`), briefing, summary,
  `files_produced`, action log.
- **Limite actuelle** : c'est un **journal des steps réalisés**, pas un
  **plan prévisionnel**. Jean-Michel délègue en aveugle : il n'a pas
  écrit "voilà mes 4 sous-tâches" avant de commencer.
- L'orchestrateur (`orchestrator.py:1073`) attribue le `step_id`
  **au moment du `delegate_to`** — il n'y a pas d'avance de phase.
- Le step de `plan_writer` a déjà tout ce qu'il faut sauf l'idée de
  "todo non encore exécuté".

### 2.2 Ce qui manque pour les recherches croisées / comparatifs
1. **Vue d'ensemble préalable** — jean-michel ne pose pas la liste des
   sous-questions avant de déléguer. Conséquence : pour un comparatif
   "X vs Y vs Z", il fait X → décide → Y → décide → Z, sans jamais voir
   les 3 ensemble.
2. **Parallélisation possible mais non utilisée** — l'orchestrateur
   sait déjà traiter plusieurs `delegate_to` séquentiellement dans un
   tour modèle (cf. README §"Échanges entre agents"). Avec une todo
   list, jean-michel peut émettre N delegate_to d'un coup pour les
   items `pending` indépendants.
3. **Reprise / mémoire** — en mode `chat`/`analyse`, après un long
   pipeline, jean-michel relit son contexte. Si la todo list est dans
   `plan.md`, il sait exactement où il en est, même après dérive.
4. **Convergence claire** — la condition de fin devient "tous les
   items en `completed` (ou `skipped`)" plutôt que l'intuition du LLM.

### 2.3 Contraintes du projet
- **Tools natifs Gemma 4**, contrat `tool_ok / tool_error` avec champ
  `summary` obligatoire (cf. `tools/_errors.py`).
- **DB = source de vérité** pour les grants. Pas de hardcode.
- **Pas de runtime deps nouvelle** (cf. copilot-instructions).
- **Artefacts plats** dans le dossier de conversation, frontmatter YAML.

---

## 3. Proposition de design

### 3.1 Nom et opérations

**Outil** : `manage_todo_list` (cohérent avec Copilot/VSCode — convention
déjà familière pour qui lit la littérature agent).

**Opérations** (paramètre `operation`) :

| op | input | sortie |
|---|---|---|
| `write` | `todos: [{id, title, status, depends_on?, assignee_hint?}]` | snapshot complet rendu |
| `read` | aucune | snapshot courant |
| `update_status` | `id, status, note?` | item mis à jour + snapshot |

Le pattern **`write` = remplacement total** (à la Copilot) est plus simple
qu'un CRUD complet et limite la dérive : l'agent doit toujours assumer la
liste entière, ce qui force le passage en revue.

`update_status` est un raccourci pratique qui évite de renvoyer toute la
liste pour un simple changement d'état.

### 3.2 Schéma d'item

```python
{
  "id": "T1",                     # str — séquentiel, attribué par l'agent
  "title": "...",                 # str — phrase courte impérative
  "status": "pending",            # pending | in_progress | completed | skipped | blocked
  "depends_on": ["T0"],           # list[str] — ids prérequis (facultatif)
  "assignee_hint": "wikipedia-specialist",  # str — code agent (facultatif)
  "note": "..."                   # str — résultat/blocage (facultatif, alimenté par update_status)
}
```

Statuts alignés sur Claude Code + ajout de `skipped` (item décidé non
pertinent en cours de route) et `blocked` (dépendance non résolue, voire
échec — surface visible pour l'humain).

### 3.3 Persistance

Deux artefacts :
- **`conversations/<id>/todo.json`** — source de vérité machine-readable.
- **Bloc rendu dans `plan.md`** — prepended au-dessus des steps existants
  (modif minime de `plan_writer.write`).

```markdown
# Plan

## TODOs (3/5)
- [x] T1 ✅ Récupérer la fiche Wikipedia de la France
- [x] T2 ✅ Récupérer la fiche Wikipedia de l'Allemagne
- [ ] T3 🔄 Comparer les indicateurs économiques (en cours)
- [ ] T4 ⏸ Comparer les indicateurs démographiques
- [ ] T5 ⏸ Rédiger la synthèse comparative (depends_on: T3, T4)

## S1 ✅ wikipedia-specialist — done
…
```

Pas de nouvelle table SQL : le filesystem est l'inventaire (même
philosophie que le workspace).

### 3.4 Grants et paradigmes

**Grants DB** (uniquement `jean-michel` au départ) :
```sql
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'manage_todo_list' FROM agents WHERE code='jean-michel';
```

**Nouveau paradigme** (`process.planning`, binding sur jean-michel) :
```
- For requests that decompose into 3+ distinct sub-questions, or whenever
  comparative / cross-research / multi-source work is involved, START by
  calling `manage_todo_list` with operation="write" to lay out the plan
  before any delegation.
- Update items via `update_status` as soon as a delegate_to returns.
- Before each new delegation, scan `pending` items: if several are
  independent (no `depends_on` overlap), emit multiple `delegate_to`
  calls in the same turn — the orchestrator processes them sequentially
  but you save one full re-decision cycle per item.
- Stop when all items are `completed` or `skipped`.
- Anti-pattern: do not create a todo list for trivial / single-step
  requests ("what time is it ?").
```

Mode-restrictions via `paradigm_modes` :
- `analyse` + `chat` ✓
- `vocal` ✗ (la concision exclut le pas-à-pas planifié)

### 3.5 Intégration avec `plan.md`

`plan_writer.write` lit `todo.json` s'il existe et prepend le bloc TODOs.
Pas de duplication — la todo list **résume l'intention**, les `## Sx`
**résument l'exécution**. Les deux vivent côte à côte.

Optionnel V2 : lier `todo.id` ↔ `step.id` via un champ `step_ref` dans
l'item, ce qui permet de cliquer-suivre dans `plan.md`.

### 3.6 Contrat de retour outil

Conformément à `tool_ok` :

```json
{
  "summary": "Wrote 5 todos (0 done, 5 pending).",
  "todos": [ ...full snapshot... ],
  "stats": { "total": 5, "pending": 5, "in_progress": 0, "completed": 0 }
}
```

Le `summary` est lu par `plan_writer.log_action` sans cas particulier
(contrat respecté).

---

## 4. Non-objectifs et risques (rappel)

**Non-objectifs V1** :
- Pas d'attribution automatique `assignee_hint` → agent réel : reste un
  indice pour le LLM, pas un routing automatique.
- Pas de parallélisation **wall-clock** des `delegate_to` (les délégations
  multiples dans un tour modèle restent séquentialisées par l'orchestrateur).
  Le vrai parallélisme est une V2 distincte (worker pool).
- Pas de cross-agent : la todo d'un spécialiste n'est pas visible du
  routeur (cf. §8).

**Risques** :
- **Sur-planning** : LLM tenté de poser une todo pour tout. Mitiger via le
  paradigme (seuil ≥ 3 sous-questions) et l'anti-pattern explicite.
- **Désync `plan.md` vs `todo.json`** : régénérer plan.md à chaque écriture
  du tool, jamais d'édition manuelle.
- **Dépendances cycliques** : valider au `write` (DAG simple, fail-fast
  via `tool_error("invalid_dependency_graph", ...)`).
- **Taille** : caper à ~20 items par liste (au-delà = signe que la
  décomposition est trop fine ou le sujet mal cadré).

---

## 5. Décisions tranchées

1. **`update_status` conservé** — utile pour économiser les tokens de sortie sur
   modèle local lent.
2. **Grants élargis aux spécialistes** — mais avec **isolation par
   `request_id`** : chaque spécialiste a sa propre todo list dans
   `todo_<request_id>.json`, scoped à sa requête, qui ne contamine pas la
   todo conversationnelle du routeur. Cf. §8 ci-dessous.
3. **Bloc TODOs en tête de `plan.md`**.
4. **Statut `skipped`** retenu (sémantique : "décidé non pertinent après
   info nouvelle").

---

## 6. Annexe — exemples de scénarios

### 6.1 Comparatif simple
> "Compare les politiques climatiques de la France et de l'Allemagne en 2025."

Jean-Michel pose au tour 1 :
```
T1 ⏸ Récupérer politique climat FR 2025 (wikipedia + web_search)
T2 ⏸ Récupérer politique climat DE 2025 (wikipedia + web_search)
T3 ⏸ Critique de cohérence des sources (critical-thinker, depends_on: T1, T2)
T4 ⏸ Rédiger synthèse comparative (document-builder, depends_on: T3)
```
Délégue T1 et T2 en parallèle (même tour modèle). Update au retour.
T3 dès que T1+T2 done. T4 en bout.

### 6.2 Recherche croisée à plusieurs angles
> "Quels sont les avantages et limites du LLM local face au cloud pour une PME ?"

```
T1 ⏸ Coûts (web_search)
T2 ⏸ Privacy/sécurité (web_search + critical-thinker)
T3 ⏸ Performance/latence (web_search)
T4 ⏸ Stack technique (wikipedia + web_search)
T5 ⏸ Synthèse pondérée (synthesizer-equivalent ou document-builder)
```
T1-T4 indépendants → batch. T5 final.

### 6.3 Anti-cas
> "Heure à Tokyo ?"

→ pas de todo list. `clock` tool direct, `return_to_user`. Le paradigme
doit explicitement éviter ça (seuil 3+).

---

## 7. Sources — récap URLs

- https://code.claude.com/docs/en/agent-sdk/todo-tracking
- https://github.com/tintinweb/pi-manage-todo-list
- https://newsletter.adaptiveengineer.com/p/this-is-the-powerful-pattern-behind
- https://www.langchain.com/blog/planning-agents
- https://agent-patterns.readthedocs.io/en/stable/patterns/rewoo.html
- https://github.com/yoheinakajima/babyagi
- https://deepwiki.com/sigoden/llm-functions/4.3.4-todo-agent
- https://pypi.org/project/llm-tools-todo/
- https://pkg.go.dev/trpc.group/trpc-go/trpc-agent-go/tool/todo
- https://github.com/mayf3/llm-todo
- https://www.ibm.com/think/topics/ai-agent-planning
- https://www.analyticsvidhya.com/blog/2024/11/agentic-ai-planning-pattern/
- https://towardsdatascience.com/how-agents-plan-tasks-with-to-do-lists/
- https://docs.pupau.ai/docs/guides/assistant_configuration/tool_use/native_tools/todo_list/

---

## 8. Isolation des TODOs par niveau

### 8.1 Deux niveaux distincts

| Niveau | Fichier | Écrit par | Lu par |
|---|---|---|---|
| **Conversation** (router) | `todo.json` | `jean-michel` uniquement | tous les agents (via `plan.md`) |
| **Requête** (spécialiste) | `todo_<request_id>.json` | le spécialiste owner de la requête | lui seul (les autres n'y ont pas accès logique) |

### 8.2 Pourquoi cette séparation

- Un spécialiste comme `comparator-specialist` reçoit "compare X, Y, Z" et
  doit lui-même décomposer en sous-recherches. S'il écrivait dans
  `todo.json` (conv), il polluerait le plan stratégique du router.
- L'isolation par `request_id` garantit que **chaque spécialiste a son
  propre cockpit**, sans risque de course / écrasement entre frères.
- Le routeur n'a **pas besoin** de voir le détail interne d'un spécialiste —
  son intérêt c'est le `report_findings` final, pas la liste de sous-coups.

### 8.3 Comportement de l'outil

L'outil `manage_todo_list` reçoit le `request_id` au moment de la
construction du registry (via le `request_id_provider` callable, comme
`bash_sandbox` le fait déjà). Il déduit **automatiquement** le bon fichier
selon le rôle de l'agent :

- **router** (jean-michel) → écrit/lit `todo.json` (conv-level).
- **specialist** → écrit/lit `todo_<request_id>.json` (request-level).
- **finalizer** (synthesizer, archivist) → pas grant, l'outil est invisible.

Le rôle est passé à `make_spec` au build du registry (l'orchestrateur le
connaît déjà). Pas de paramètre `scope` exposé au LLM — invisible et
non-fakeable.

### 8.4 Rendu dans `plan.md`

`plan.md` est **conv-level** : il ne contient que `todo.json` (router).
Les todos request-level vivent dans leur JSON et sont rendus dans la
**CLI** uniquement (cf. §9), pas dans `plan.md`. Rationale : `plan.md`
est un artefact partagé entre agents, le rendre encombré d'infos
internes d'un spécialiste casse sa lisibilité.

### 8.5 Cleanup

Les fichiers `todo_<request_id>.json` sont **gardés** (mêmes règles
que les autres artefacts de la conversation). Utiles au debugging
via `--inspect-conv`.

### 8.6 Grants DB (V1)

| Agent | Grant `manage_todo_list` |
|---|:-:|
| `jean-michel` (router) | ✓ |
| `comparator-specialist` | ✓ |
| `critical-thinker` | ✓ |
| `meta-analyst` | ✓ |
| `document-builder` | ✓ |
| `code-runner` | ✓ |
| `web-search-specialist`, `wikipedia-specialist`, `weather-specialist`, `summarizer`, `workspace-manager` | ✗ (tâches mono-step, todo inutile) |
| `synthesizer`, `archivist` (finalizers) | ✗ (par rôle, cf. 8.3) |

---

## 9. Affichage CLI — progression temps réel

### 9.1 Objectif visuel

Quand le LLM appelle `manage_todo_list`, la CLI doit afficher la liste
mise à jour de façon **distincte** du log textuel (pas juste un
`tool_response` formaté), avec :

- statut graphique de chaque item (icône + couleur)
- compteur de progression (`3/5 done`)
- distinction visuelle **conv-level vs request-level** (indent +
  préfixe agent pour les sub-todos)

### 9.2 Format proposé (mode `rich`)

```
╭─ TODO · jean-michel · 2/4 done ─────────────────────────╮
│  ✅ T1  Récupérer fiche FR                              │
│  ✅ T2  Récupérer fiche DE                              │
│  🔄 T3  Comparer indicateurs (in_progress)             │
│  ⏸  T4  Rédiger synthèse  (depends_on: T3)             │
╰─────────────────────────────────────────────────────────╯
```

Pour un sub-todo de spécialiste :

```
  ╭─ sub-TODO · comparator-specialist · req=8a4f… · 1/3 done ─╮
  │   ✅ S1  Wikipedia FR                                     │
  │   🔄 S2  Wikipedia DE                                     │
  │   ⏸  S3  Cross-check chiffres                             │
  ╰───────────────────────────────────────────────────────────╯
```

Indent (2 espaces) + titre `sub-TODO` + couleur plus discrète (dim) pour
visualiser la hiérarchie sans surcharger.

### 9.3 Mécanisme événementiel

Nouvel événement dans `orchestrator.py` :

```python
@dataclass(frozen=True)
class TodoListUpdated:
    agent: str               # ex. "jean-michel"
    scope: str               # "conversation" | "request"
    request_id: str | None   # None si conversation-level
    todos: list[dict]        # snapshot complet
    stats: dict              # {total, pending, in_progress, completed, skipped, blocked}
```

Émis par l'orchestrateur **après** chaque `tool_response` réussi de
`manage_todo_list` (write ou update_status). Read ne re-émet rien si
contenu inchangé (économie d'affichage).

### 9.4 Renderer dans `cli.py`

Branche supplémentaire dans `render_events` :

```python
elif isinstance(ev, TodoListUpdated):
    _render_todo_panel(console, ev)
```

Où `_render_todo_panel` :
1. Calcule l'indent selon `ev.scope`.
2. Construit un `rich.panel.Panel` avec titre `TODO · {agent} · X/Y done`
   (ou `sub-TODO · …` si request-level).
3. Boucle sur `todos` : icône (`✅ 🔄 ⏸ ⏭ 🚫`), id, title, hint
   `depends_on` si présent, status entre parens si non-terminal.

Pas d'affichage live-updating (`rich.live`) en V1 — on imprime un nouveau
panel à chaque update, plus simple et compatible avec le spinner
existant.

### 9.5 Mode `vocal`

Désactivé : pas d'affichage de panel TODO. Cohérent avec la
restriction du paradigme.

### 9.6 Mode `--inspect-conv`

`debug/inspect_conv.py` doit savoir afficher les `todo*.json` quand on
inspecte une conversation : ajouter une section "TODO snapshots" listant
le fichier conv-level + tous les request-level, avec rendu identique
à celui de la CLI (réutiliser la fonction de rendu si possible).

---

## 10. Plan d'implémentation séquencé pour Claude 4.6

> **Hypothèse d'exécution** : un seul agent autonome exécute ces phases
> dans l'ordre, valide chaque phase par des tests verts avant de passer
> à la suivante, et commit après chaque phase.

> **Convention projet** : DB live ET `db/schema.sql` modifiés ensemble ;
> jamais de hardcode de grants en Python ; respect du contrat
> `tool_ok`/`tool_error` ; pas de docstrings/comments ajoutés à du code
> non modifié.

### Phase 0 — Préparation
- [ ] Lire intégralement [src/jeanmichel/tools/_base.py](src/jeanmichel/tools/_base.py),
  [src/jeanmichel/tools/_errors.py](src/jeanmichel/tools/_errors.py),
  [src/jeanmichel/tools/conv_status.py](src/jeanmichel/tools/conv_status.py) (modèle context-bound + DB)
  et [src/jeanmichel/tools/bash_sandbox.py](src/jeanmichel/tools/bash_sandbox.py) (modèle `request_id_provider`).
- [ ] Lire intégralement [src/jeanmichel/plan_writer.py](src/jeanmichel/plan_writer.py).
- [ ] Lister les emplacements actuels d'appel à `build_registry` dans
  [src/jeanmichel/orchestrator.py](src/jeanmichel/orchestrator.py)
  (`grep -n build_registry src/jeanmichel/orchestrator.py`) — il faudra
  propager le nouveau paramètre `agent_role`.

**Critère de sortie** : aucun fichier modifié, mais notes prises sur
les signatures à toucher.

---

### Phase 1 — Tool `manage_todo_list` (autonome, sans intégration)

**Objectif** : produire un outil fonctionnel, testable en isolation, qui
écrit/lit un JSON dans un chemin choisi par son contexte de construction.

1. Créer `src/jeanmichel/tools/manage_todo_list.py` :
   - Signature : `make_spec(conv_folder: Path, agent_role: str,
     request_id_provider: Callable[[], str] | None) -> ToolSpec`.
   - Helper interne `_todo_path()` qui retourne :
     - `conv_folder / "todo.json"` si `agent_role == "router"`
     - `conv_folder / f"todo_{request_id_provider()}.json"` si `agent_role == "specialist"`
     - lève `RuntimeError` sinon (finalizer ne devrait jamais arriver ici)
   - Schéma item validé en Python (pas de jsonschema, dict natif) :
     - clés autorisées : `id, title, status, depends_on, assignee_hint, note`
     - `status` ∈ `{pending, in_progress, completed, skipped, blocked}`
     - `id` non vide, unique dans la liste
     - `depends_on` ⊆ ids existants (validation DAG : pas de cycle)
     - `len(todos) ≤ 20`, sinon `tool_error("too_many_todos", ...)`
   - Opérations :
     - `write(todos)` → remplace, valide, écrit fichier, retourne
       `tool_ok(summary, todos=..., stats=...)`.
     - `read()` → lit fichier (ou retourne liste vide si absent),
       `tool_ok(summary, todos=..., stats=...)`.
     - `update_status(id, status, note?)` → patch item, retourne snapshot
       complet, ou `tool_error("todo_not_found"/"invalid_status", ...)`.
   - `summary` toujours présent : `f"{op}: {n_done}/{n_total} done, {n_progress} in progress"`.
   - **Format du fichier JSON** :
     ```json
     {
       "updated_at": "2026-05-25T14:32:18Z",
       "todos": [ {...}, ... ]
     }
     ```
     Écriture atomique : écrire dans `tmp` puis `os.replace`.

2. Tests `tests/test_manage_todo_list.py` :
   - Helper local pour instancier le tool avec un `tmp_path` et un
     `request_id_provider` mock.
   - Cas conv-level (`router`) et request-level (`specialist`).
   - `write` valide + invalide (id manquant, status inconnu, cycle de
     dépendance, > 20 items, ids dupliqués).
   - `read` sans fichier préalable (retourne liste vide).
   - `update_status` valide + cas d'erreur.
   - Écriture atomique (vérifier que pas de fichier `.tmp` orphelin
     après crash simulé : OK skip si trop complexe en V1).

**Critère de sortie** : `pytest tests/test_manage_todo_list.py -v` vert.

---

### Phase 2 — Intégration dans `build_registry`

1. Étendre `build_registry` ([src/jeanmichel/tools/__init__.py](src/jeanmichel/tools/__init__.py)) avec un nouveau paramètre `agent_role: str = ""` :
   - Si `agent_role in {"router", "specialist"}` et `request_id_provider`
     est fourni → construit le spec de `manage_todo_list` et l'ajoute au
     registry.
   - Sinon (finalizer ou contexte de test minimal) → ne pas ajouter.

2. Propager `agent_role` depuis l'orchestrateur dans tous les appels à
   `build_registry`. Source : la table `agents` (déjà chargée par
   l'orchestrateur, champ `role`).
   - Identifier précisément les call sites dans `orchestrator.py` et
     répliquer.

3. Le filtrage par grant DB (`agent_tools.tool_code='manage_todo_list'`)
   est déjà géré par `tools_payload_for_agent` dans
   [src/jeanmichel/prompts.py](src/jeanmichel/prompts.py) — vérifier
   qu'aucun ajustement n'est nécessaire (devrait être transparent).

4. Tests : un mini-test qui construit le registry pour un router et
   pour un specialist, et vérifie la présence/absence de `manage_todo_list`
   selon le rôle.

**Critère de sortie** : suite de tests complète (`pytest tests/ -v`) toujours verte.

---

### Phase 3 — DB : migration, schéma, grants, paradigme

1. Créer `db/migrations/migrate_NNN_manage_todo_list.sql` (NNN = prochain numéro libre, vérifier avec `ls db/migrations/`) :
   ```sql
   -- Paradigme "planning_with_todos" (section process, catégorie nouvelle ou existante "planning")
   INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at)
   SELECT id, 'planning', 'Planning', 55, 1, datetime('now'), datetime('now')
   FROM sections WHERE code='process'
     AND NOT EXISTS (SELECT 1 FROM categories WHERE code='planning');

   INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
   SELECT
     (SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='planning'),
     'planning_with_todos',
     'Plan multi-step work with manage_todo_list',
     '<content du paradigme — cf. §3.4 du spec>',
     'Externalise the plan for transparency, fault tolerance, and to enable batched delegation on independent sub-questions.',
     0, 10, 1, datetime('now'), datetime('now')
   WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code='planning_with_todos');

   -- Bindings agent_paradigms (router + spécialistes capables de décomposer)
   INSERT INTO agent_paradigms (agent_id, paradigm_id)
   SELECT a.id, p.id
   FROM agents a, paradigms p
   WHERE p.code='planning_with_todos'
     AND a.code IN ('jean-michel','comparator-specialist','critical-thinker','meta-analyst','document-builder','code-runner')
     AND NOT EXISTS (
       SELECT 1 FROM agent_paradigms ap WHERE ap.agent_id=a.id AND ap.paradigm_id=p.id
     );

   -- Restrictions de mode : exclu de vocal
   INSERT INTO paradigm_modes (paradigm_id, mode)
   SELECT p.id, m.mode FROM paradigms p, (VALUES ('analyse'),('chat')) AS m(mode)
   WHERE p.code='planning_with_todos'
     AND NOT EXISTS (
       SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id=p.id AND pm.mode=m.mode
     );

   -- Grants outils
   INSERT INTO agent_tools (agent_id, tool_code)
   SELECT a.id, 'manage_todo_list'
   FROM agents a
   WHERE a.code IN ('jean-michel','comparator-specialist','critical-thinker','meta-analyst','document-builder','code-runner')
     AND NOT EXISTS (
       SELECT 1 FROM agent_tools at WHERE at.agent_id=a.id AND at.tool_code='manage_todo_list'
     );
   ```

2. Appliquer sur la DB live :
   `sqlite3 jeanmichel.db < db/migrations/migrate_NNN_manage_todo_list.sql`.

3. **Reporter les mêmes INSERTs** dans `db/schema.sql` (convention : schema = source de vérité consolidée).

4. Vérification : `./jm.sh --paradigm-matrix` doit montrer le nouveau paradigme bindé aux bons agents, restreint aux modes `analyse`/`chat`.

**Critère de sortie** : tests verts + paradigm-matrix conforme.

---

### Phase 4 — Intégration `plan.md` (conv-level uniquement)

1. Dans `plan_writer.py`, ajouter une fonction privée `_render_todo_block(conv_folder: Path) -> str | None` :
   - Lit `conv_folder/todo.json` ; si absent, retourne `None`.
   - Sinon, rend un bloc Markdown :
     ```
     ## TODOs (3/5)
     - [x] ✅ T1 ... title
     - [ ] 🔄 T2 ... title (in_progress)
     - [ ] ⏸ T3 ... title (depends_on: T1)
     ```

2. Modifier `plan_writer.write(...)` : si `_render_todo_block` retourne
   un bloc, l'insérer entre `# Plan\n\n` et le body des steps.

3. Tests : étendre `tests/test_plan_writer.py` (ou créer si absent) pour
   couvrir le rendu avec/sans `todo.json`.

**Critère de sortie** : `pytest tests/test_plan_writer.py -v` vert, ET
inspection manuelle d'un `plan.md` avec todo présent dans une
conversation factice.

---

### Phase 5 — Événement orchestrateur + émission

1. Ajouter le dataclass `TodoListUpdated` dans `orchestrator.py`
   (cf. §9.3) à proximité des autres events.

2. Dans la boucle de traitement des `tool_response` : après tout appel
   réussi à `manage_todo_list` (détection par `tool_name == "manage_todo_list"`),
   parser le JSON de retour et émettre `TodoListUpdated` avec :
   - `agent` = agent courant
   - `scope` = "conversation" si rôle router, "request" sinon
   - `request_id` = id courant si specialist, None sinon
   - `todos` = liste depuis le payload
   - `stats` = stats depuis le payload

3. Re-appeler `plan_writer.write(...)` pour rafraîchir `plan.md` quand le
   `scope == "conversation"` (changement de todo conv-level → plan.md
   doit refléter).

4. Tests : MockClient script qui simule un `tool_response` de
   `manage_todo_list` et vérifie qu'un `TodoListUpdated` est émis avec
   les bons champs.

**Critère de sortie** : `pytest tests/test_orchestrator.py -v` vert + nouveau test dédié vert.

---

### Phase 6 — Rendu CLI

1. Importer `TodoListUpdated` dans `cli.py`.

2. Ajouter une branche dans `render_events` :
   ```python
   elif isinstance(ev, TodoListUpdated):
       _render_todo_panel(console, ev, mode)
   ```
   (passer le `mode` pour skip en `vocal`).

3. Implémenter `_render_todo_panel(console, ev, mode)` :
   - Si `mode == "vocal"` → return.
   - Build titre : `f"TODO · {ev.agent} · {done}/{total} done"` ou
     `f"sub-TODO · {ev.agent} · req={ev.request_id[:8]}… · {done}/{total} done"`.
   - Build body : pour chaque todo, ligne `f"{icon} {id}  {title}{extras}"` où :
     - icon par status : ✅ completed, 🔄 in_progress, ⏸ pending, ⏭ skipped, 🚫 blocked
     - extras : `(depends_on: X, Y)` si présent et status=pending, `— {note}` si note.
   - Wrap dans `Panel` ; style `dim` si scope=request, normal sinon ;
     indent (préfixe 2 espaces) si scope=request.

4. Tests CLI : difficile en pur unit-test (rich = visuel) — privilégier
   un test de smoke qui appelle `_render_todo_panel` sans crasher pour
   les deux scopes, et vérifie via `console.capture()` que les chaînes
   attendues (titre, icônes) sont présentes.

**Critère de sortie** : suite complète verte + démo manuelle via
`tests/demo_cli.py` ou un mini-script visuel.

---

### Phase 7 — Inspection / debug

1. Patcher `debug/inspect_conv.py` pour, en plus des artefacts existants,
   afficher :
   - Le contenu rendu de `todo.json` (conv-level) s'il existe.
   - La liste des fichiers `todo_*.json` (request-level) avec leur
     contenu rendu.
   - Réutiliser le rendu `_render_todo_panel` si import croisé propre,
     sinon dupliquer un mini-renderer (acceptable car script debug).

2. Vérification manuelle : créer une conversation factice avec un
   `todo.json` + un `todo_<id>.json` sample, lancer `./jm.sh --inspect-conv <id>`,
   vérifier que les deux blocs apparaissent.

**Critère de sortie** : inspection manuelle OK.

---

### Phase 8 — Documentation

1. **README.md** :
   - Liste outils natifs : ajouter `manage_todo_list (conv + request scoped)`.
   - Section "Récursion & garde-fous" : mentionner la todo list comme
     mécanisme de planification.

2. **docs/HOWTO_ADD_SPECIALIST_OR_TOOL.md** :
   - Cas particulier "Outils scopés au niveau requête vs conversation" :
     expliquer le pattern `agent_role` + `request_id_provider` (utile
     pour de futurs outils similaires).

3. **DevNotes/the_toolbox/todo_tool_spec.md** (ce doc) :
   - Marquer `Statut: implémenté V1` en tête.
   - Renvoyer aux commits / migration numéro.

**Critère de sortie** : doc cohérente avec l'implémentation.

---

### Phase 9 — Validation end-to-end

1. Lancer un scénario réel (Ollama up) :
   `./jm.sh --mode analyse`
   puis "Compare les politiques climatiques de la France et de l'Allemagne en 2025."
2. Vérifier visuellement :
   - Panel TODO affiché par jean-michel au tour 1 (≥3 items).
   - Sub-TODO affiché si `comparator-specialist` est sollicité.
   - `plan.md` contient le bloc TODOs en tête.
   - `todo.json` + `todo_*.json` présents dans le dossier de conversation.
3. Faire un second scénario trivial ("quelle heure ?") : vérifier qu'aucune
   todo list n'est créée (anti-cas respecté).
4. Run `./jm.sh --mode vocal` sur un sujet complexe : vérifier que le
   paradigme planning_with_todos est **inactif** (pas de todo créée,
   pas de panel CLI).

**Critère de sortie** : checklist visuelle complète.

---

### Récapitulatif checklist Claude 4.6

- [ ] Phase 0 — Lecture préalable, notes
- [ ] Phase 1 — Tool standalone + tests
- [ ] Phase 2 — Intégration registry + propagation `agent_role`
- [ ] Phase 3 — Migration DB + miroir schema.sql
- [ ] Phase 4 — Plan_writer header TODO
- [ ] Phase 5 — Event orchestrateur + émission
- [ ] Phase 6 — Renderer CLI panel + sub-panel
- [ ] Phase 7 — inspect_conv affichage des todos
- [ ] Phase 8 — Docs
- [ ] Phase 9 — Validation end-to-end Ollama réelle

Commit après chaque phase, suite complète `pytest tests/ -v` verte avant
de passer à la suivante.

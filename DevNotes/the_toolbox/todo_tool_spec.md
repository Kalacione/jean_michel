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

## 4. Plan d'implémentation

### 4.1 Étapes

1. **`src/jeanmichel/tools/manage_todo_list.py`** — context-bound
   (`make_spec(conv_folder)`). Lit/écrit `conv_folder/todo.json`,
   validation schéma, retourne `tool_ok` standard.
2. **`src/jeanmichel/tools/__init__.py`** — enregistrer dans
   `build_registry`.
3. **`src/jeanmichel/plan_writer.py`** — fonction `_render_todos(conv_folder)`
   appelée en tête de `write(...)` ; rend le bloc Markdown depuis
   `todo.json` si présent.
4. **DB / `db/schema.sql`** + migration `migrate_NNN_manage_todo_list.sql` :
   - INSERT `agent_tools` pour jean-michel
   - INSERT `paradigms` (`process.planning`)
   - INSERT `agent_paradigms` (jean-michel × planning)
   - INSERT `paradigm_modes` (analyse, chat)
5. **Tests** (`tests/test_manage_todo_list.py`) :
   - tool unit-tests (write/read/update_status, validation, dépendances)
   - intégration `plan_writer` (rendu correct du bloc TODOs)
   - scénario orchestrateur via `MockClient` : un script où jean-michel
     écrit 3 todos, délègue, met à jour, conclut.
6. **Docs** — patch `README.md` (liste outils) + entrée brève dans
   `docs/HOWTO_ADD_SPECIALIST_OR_TOOL.md` mentionnant le pattern todo.

### 4.2 Non-objectifs (V1)
- Pas d'attribution automatique `assignee_hint` → agent réel : reste
  un indice pour le LLM, pas un routing automatique.
- Pas de parallélisation **réelle** des `delegate_to` (les délégations
  multiples dans un tour modèle sont déjà séquentialisées par
  l'orchestrateur — gain attendu surtout cognitif, pas wall-clock).
  Le vrai parallélisme est une V2 distincte (worker pool).
- Pas de partage de la todo list avec les spécialistes (V1 : seul le
  router écrit/lit). Les spécialistes voient déjà `plan.md` via
  `conv_read_file`, donc ils héritent du contexte naturellement.

### 4.3 Points d'attention / risques

- **Dérive vers le sur-planning** : un LLM peut être tenté de poser
  une todo list pour tout. Mitiger via le paradigme (seuil ≥ 3
  sous-questions) et l'anti-pattern explicite.
- **Désync `plan.md` vs `todo.json`** : régénérer plan.md à chaque
  écriture du tool, jamais d'édition manuelle.
- **Dépendances cycliques** : valider au `write` (DAG simple, fail-fast
  via `tool_error("invalid_dependency_graph", ...)`).
- **Taille** : caper à ~20 items par liste (au-delà = signe que la
  décomposition est trop fine ou le sujet est mal cadré).

---

## 5. Décisions ouvertes (à trancher avant code)

1. **`update_status` ou pas ?** Copilot/VSCode = `write` only. Plus simple,
   mais oblige à renvoyer toute la liste. → Je propose de **garder
   `update_status`** : on est sur un modèle local plus lent, économiser
   les tokens en sortie a un vrai impact wall-clock.
2. **Granter à d'autres agents ?** `meta-analyst`, `comparator-specialist`,
   `critical-thinker` pourraient en bénéficier. → V1 : router seulement.
   V2 : granter au `comparator-specialist` qui a précisément ce profil
   multi-entités.
3. **Bloc TODOs en tête ou en pied de `plan.md`** ? En tête : c'est le
   "résumé exécutif", lu en premier par tous les agents qui lisent
   `plan.md`. → tête.
4. **Statut `skipped` vs `cancelled`** ? `skipped` (sémantique : "décidé
   non pertinent après info nouvelle") — `cancelled` implique action
   externe, peu adapté ici.

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

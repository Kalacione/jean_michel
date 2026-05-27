# 02 — Architecture cible (proposition à itérer)

> Document de discussion. Pas d'implémentation tant que la cible n'est pas
> validée. Les choix proposés sont **discutables** — chaque section porte
> ses arbitrages et signale ce qui peut être tranché autrement.

## Principes fondateurs

1. **Le Python dirige, pas le LLM.** L'orchestrateur est un *programme*. Il
   décide quoi faire ensuite à partir d'un état explicite. Le LLM n'est
   appelé que pour des **micro-décisions ciblées**, jamais pour piloter le
   processus.
2. **Plusieurs modèles, un par usage.** Routeur tiny, spécialiste medium,
   synthétiseur fort. Le bon outil pour le bon coup.
3. **Un seul artefact d'état canonique** (la "task tree"), source de vérité.
   Tout le reste (plan.md, recap, etc.) en est un *rendu*.
4. **Un budget unique de coût par tour**, dépensé à chaque action.
5. **Profondeur bornée, largeur dynamique.** L'arbre de tâches grandit en
   profondeur sous une limite stricte ; la largeur est libre tant que le
   budget tient.
6. **Pas de RAG.** Tout passe par le workspace markdown. Recherche
   intra-conversation = grep / glob déterministe.
7. **K.I.S.S.** Si une couche peut être supprimée, elle l'est.

---

## Diagramme global

```
┌─────────────────────────────────────────────────────────────────┐
│                       CLI (humain)                              │
└─────────────────────────────────────────────────────────────────┘
                │ user_text
                ▼
┌─────────────────────────────────────────────────────────────────┐
│  DISPATCHER (tiny LLM, ~1-3B, sans thinking)                    │
│  Input : user_text                                              │
│  Output : { intent: ALEXA | DEEP, route_hint: "weather" | … }   │
└─────────────────────────────────────────────────────────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
   ALEXA mode        DEEP mode
        │               │
        ▼               ▼
   ┌────────┐    ┌─────────────────────────────────────────────┐
   │ 1 tool │    │  PLANNER (medium LLM, thinking)             │
   │ 1 reply│    │   crée le task tree initial                 │
   └────────┘    └─────────────────────────────────────────────┘
                        │
                        ▼
                ┌─────────────────────────────────────────────┐
                │  EXECUTOR LOOP (Python, déterministe)       │
                │  - pop next ready task                      │
                │  - debit budget                             │
                │  - choose model & agent for the task        │
                │  - run task (LLM call ciblé OU tool natif)  │
                │  - parse strict output → mutate state       │
                │  - emit follow-up tasks si nécessaire       │
                │  jusqu'à arbre.is_complete() ou budget=0    │
                └─────────────────────────────────────────────┘
                        │
                        ▼
                ┌────────────────────┐
                │ SYNTHESIZER (strong) │
                │ → réponse finale     │
                └────────────────────┘
```

---

## A. Le DISPATCHER : LLM ridicule en porte d'entrée

**Modèle** : `llama3.2:1b` ou `qwen2.5:1.5b`, sans thinking, température
basse, response_format JSON forcé.

**Input** :
```
system: "Tu classes une requête utilisateur. Réponds en JSON strict."
user:   "<user_text>"
```

**Output strict** :
```json
{
  "intent": "ALEXA" | "DEEP",
  "route_hint": "weather" | "clock" | "wikipedia" | "web_search" | null,
  "args": { ... }   // si ALEXA + route_hint connu
}
```

**Si `intent == ALEXA`** : l'orchestrateur appelle directement le tool
indiqué (un seul tool, un seul résultat) puis envoie le résultat au
SYNTHESIZER ou répond brut. **Pas de thinking, pas de planning, pas de
task tree.** Latence cible : < 2s pour "quelle heure est-il".

**Si `intent == DEEP`** : on bascule sur le PLANNER.

**Arbitrage à discuter** :
- Faut-il vraiment un LLM ou un classifieur regex/embedding ? Pour
  "quelle heure", un keyword spotter suffit. Mais un 1.5B est plus robuste
  à l'ambiguïté ("dis-moi un truc cool sur Rome" → DEEP).
- Faut-il faire confiance au `route_hint` ? Proposition : si confiance LLM
  basse ou hint absent, fallback DEEP. Le défaut sûr est "réfléchir
  davantage", pas "deviner un tool".

---

## B. Le TASK TREE : structure d'état canonique

C'est le **seul artefact d'état**. Il remplace `plan.md` + `todo.json` +
`current_task_class` + les flags de phase.

```python
@dataclass
class Task:
    id: str                  # "T1", "T1.2", ...
    parent_id: str | None
    kind: Literal[
        "GATHER",            # collecte d'info (search, conv_read_file, …)
        "ANALYZE",            # réflexion sur données existantes
        "BUILD",             # production d'artefact workspace
        "SYNTHESIZE",         # réponse finale
        "ASK_HUMAN",          # blocage clarification
    ]
    intent: str              # phrase humaine, 1 ligne max
    inputs: list[str]        # paths workspace requis avant exécution
    outputs: list[str]       # paths workspace produits attendus
    depends_on: list[str]    # task_ids à compléter avant celle-ci
    status: Literal["pending", "running", "done", "failed", "blocked"]
    assigned_agent: str | None    # choisi par l'executor, pas le LLM
    assigned_model: str | None
    budget_cost: int         # estimé à création, débité à exécution
    result_summary: str = "" # 1 ligne, écrit après exécution
    artifact_paths: list[str] = []   # outputs réellement créés
```

**Stockage** : un seul fichier `conversations/<id>/state.json` (atomic
write). Le rendu humain (`plan.md`) est généré à partir de cet état, à la
demande. **Aucune écriture parallèle**, l'orchestrateur est mono-thread.

**Opérations primitives** (toutes en Python pur, déterministes) :

- `tree.add_task(parent_id, kind, intent, depends_on, ...)` → renvoie task_id
- `tree.ready_tasks()` → tasks `pending` dont toutes les dépendances sont `done`
- `tree.mark(task_id, status, result_summary, artifact_paths)`
- `tree.depth(task_id)` → int
- `tree.budget_spent()` / `tree.budget_remaining(total)`
- `tree.is_complete()` → bool (toutes les tâches `done` ou `failed`)

Le LLM **ne touche jamais** à cet état directement. Il peut **proposer** des
sous-tâches (output structuré), mais c'est l'orchestrateur qui valide et
insère.

---

## C. Le PLANNER : un seul appel ciblé pour bootstrapper l'arbre

**Modèle** : medium (Gemma3 12B ou Qwen2.5 14B), thinking activé.

**Input** : user_text + résumé des outils disponibles + paradigmes du rôle
planner.

**Output strict** (JSON, validé par schéma) :
```json
{
  "rationale": "court paragraphe expliquant l'approche",
  "tasks": [
    {
      "id": "T1",
      "kind": "GATHER",
      "intent": "rassembler la doc python 3.14 sur les match patterns",
      "depends_on": [],
      "expected_outputs": ["workspace/T1_doc.md"]
    },
    {
      "id": "T2",
      "kind": "ANALYZE",
      "intent": "comparer match python vs switch rust",
      "depends_on": ["T1"],
      "expected_outputs": ["workspace/T2_analysis.md"]
    },
    {
      "id": "T3",
      "kind": "SYNTHESIZE",
      "intent": "produire la réponse finale en FR",
      "depends_on": ["T2"]
    }
  ]
}
```

**Validation Python stricte** :
- IDs uniques, séquentiels.
- `depends_on` ne contient que des IDs déjà déclarés (DAG, pas de cycle).
- Au moins une `SYNTHESIZE` terminale.
- Pas de référence à des fichiers en dehors du workspace.

Si validation échoue → on relance le PLANNER avec l'erreur en input (1
retry max). Si toujours invalide → fallback "ALEXA-degraded" : on tente une
réponse directe sans plan, avec mention au user.

**Arbitrage** : faut-il que le PLANNER fasse un *re-plan* en cours de route ?
Proposition initiale : **non**. Les `BUILD` et `ANALYZE` peuvent émettre
des sous-tâches (voir section D), mais le squelette racine reste figé.
C'est suffisant pour 95% des cas et simplifie énormément le state mgmt.

---

## D. L'EXECUTOR LOOP : la vraie state machine (Python pur)

```python
def execute(tree, budget):
    while not tree.is_complete() and budget > 0:
        ready = tree.ready_tasks()
        if not ready:
            break  # deadlock — toutes pending sont bloquées
        task = pick_next(ready)        # priorité = profondeur + ordre
        if tree.depth(task.id) > MAX_DEPTH:
            tree.mark(task.id, "failed", "max depth reached")
            continue
        if budget < task.budget_cost:
            tree.mark(task.id, "blocked", "budget exhausted")
            break
        agent, model = route(task)     # déterministe selon task.kind
        result = run_task(task, agent, model, tree)
        budget -= result.actual_cost
        tree.mark(task.id, ...)
        for proposed in result.proposed_subtasks:
            if validate(proposed, parent=task):
                tree.add_task(parent_id=task.id, **proposed)
```

**Points clés** :

- **Boucle Python pure**, 30 lignes. Lisible, testable, déterministe.
- **Pas de récursion** : l'arbre grandit, mais le code reste plat. Pas de
  call stack qui explose.
- **Une seule limite de profondeur** (`MAX_DEPTH=5` proposé), gérée par
  `tree.depth()`. Largeur libre tant que le budget tient.
- **Budget unique** consommé à chaque tâche. Pas de 7 compteurs.
- **`pick_next`** déterministe : tri par (profondeur croissante,
  ordre d'insertion). Plus profond = traité plus tôt → on referme les
  branches avant d'en ouvrir d'autres. Anti-explosion en largeur.
- **`route(task)`** : pure fonction `task.kind → (agent_code, model_name)`.
  Configurable par DB (`task_routing` table), pas par LLM.

---

## E. `run_task` : appel LLM ciblé avec contrat de sortie strict

Chaque tâche est exécutée par **un seul appel LLM** (sauf si tool natif
pur, p.ex. `clock` → pas de LLM du tout).

**Contrat** :

```python
@dataclass
class TaskResult:
    status: Literal["ok", "partial", "error"]
    summary: str                       # 1 ligne pour le tree
    artifacts: list[str]               # chemins workspace écrits
    proposed_subtasks: list[dict]      # 0..N propositions structurées
    actual_cost: int                   # tokens-equivalent
```

L'output LLM est forcé en JSON structuré (`response_format` Ollama). Si le
JSON est invalide → 1 retry avec le schéma + l'erreur. Sinon `status=error`.

**Le LLM n'a accès qu'à** :
- son system prompt rendu pour ce kind + cet agent (paradigmes, contrat de
  sortie spécifique à `kind`),
- l'intent de la tâche,
- les fichiers listés dans `task.inputs` (déjà résolus → contenu injecté),
- les outils filtrés pour ce `kind` (`GATHER` voit `web_search` etc.,
  `ANALYZE` ne voit que `workspace_view`, etc.).

**Pas d'historique multi-tour.** Le LLM est appelé une fois par tâche. S'il a
besoin de plusieurs étapes pour produire un résultat, ces étapes sont des
**sous-tâches** qu'il propose et que l'executor insère dans l'arbre. C'est
*la* manière de casser le pattern "spécialiste qui boucle".

**Arbitrage critique** : "un seul appel LLM par tâche" est radical. Cas
limites :

- Une `GATHER` peut nécessiter 3 `web_search` enchaînés pour vraiment cerner.
  → Solution : la tâche `GATHER` haut-niveau émet des sous-tâches
  `GATHER` plus précises, chacune = 1 appel = 1 recherche. L'arbre porte la
  séquentialité.
- Coût : plus de calls LLM courts vs aujourd'hui 1 call long. Mais chaque
  call est petit, déterministe, parallélisable, traçable.

**Alternative à discuter** : autoriser jusqu'à `K=3` appels LLM par tâche
avec re-injection des tool_results (vrai pattern Ollama `tool` messages
multi-turn), mais avec un **budget local strict** par tâche. C'est plus
proche de Claude Code mais plus complexe à border. À trancher.

---

## F. Mémoire et contexte : le WORKSPACE comme bus

Aujourd'hui le workspace existe mais sert peu d'inter-agent. Proposition :

- **Chaque tâche écrit ses résultats** dans `workspace/<task_id>_<slug>.md`.
- **Chaque tâche déclare ses `inputs`** = paths workspace nécessaires. L'
  executor les lit et les injecte dans le prompt LLM, **résolus**. Le LLM ne
  lance pas de tool pour lire ses inputs : ils sont déjà là.
- **`render_plan_recap`** disparaît. Remplacé par : "voici tes inputs
  pré-chargés, voici ton intent, produis l'output".

Conséquence : la mémoire **n'est plus à reconstruire** à chaque tour, elle
est *physiquement* dans le prompt. Plus de récap basse-fidélité. Plus de
spécialiste qui oublie pourquoi il cherche.

**Recherche cross-conversation** (besoin futur) : grep déterministe sur
`conversations/*/workspace/**/*.md`. Pas de RAG. Si le besoin grandit,
SQLite FTS5 suffit.

---

## G. Routage modèle (table DB)

Nouvelle table `task_routing` :

| task_kind   | preferred_agent       | preferred_model      | thinking | max_tokens |
|-------------|-----------------------|----------------------|----------|------------|
| GATHER      | web-search-specialist | gemma3:4b            | false    | 4000       |
| ANALYZE     | summarizer            | gemma3:12b           | true     | 6000       |
| BUILD       | (selon agent)         | gemma3:12b           | true     | 8000       |
| SYNTHESIZE  | synthesizer           | gemma3:27b           | true     | 8000       |
| ASK_HUMAN   | (router)              | llama3.2:1b          | false    | 500        |

Tunable sans toucher au code. Le PLANNER ne décide pas du modèle ; il
décide du `kind`, le routage est déterministe ensuite.

**Arbitrage** : faut-il un fallback automatique (si gemma3:4b échoue ou est
absent, prendre gemma3:12b) ? Proposition : oui, configuré en DB
(`fallback_model`), géré par l'executor.

---

## H. Garde-fous unifiés

On supprime 5 des 7 budgets actuels. On garde :

1. **`TURN_BUDGET`** : un coût agrégé (tokens estimés) par tour, dérivé de
   `intent`. Exemple : `ALEXA → 5_000`, `DEEP → 200_000`. Décrémenté à
   chaque appel LLM par `actual_cost`. À 0 → on synthétise avec ce qu'on a.
2. **`MAX_DEPTH=5`** : limite stricte de profondeur de l'arbre. Au-delà,
   les sous-tâches proposées sont refusées, la tâche est synthétisée.

Les wall-clocks restent comme **safety net technique** (timeout Ollama,
crash protection) mais ne sont plus des leviers fonctionnels.

---

## I. Persistance et observabilité

- `state.json` : task tree, écrit après chaque mutation.
- `events.jsonl` : event log append-only de l'executor (TaskCreated,
  TaskCompleted, BudgetDebited, ...). Remplace les artefacts éparpillés.
- Les artefacts `.md` individuels restent dans `workspace/`, c'est leur place.
- `plan.md` devient un *render* à la demande de `state.json`, pas une
  écriture déterministe à chaque event.

L'`inspect_conv` debug tool affiche `state.json` + `events.jsonl` côte à côte.

---

## J. Migration depuis l'existant

Phasable :

1. **Phase 0** : introduire `state.json` et `Task` dataclass en parallèle de
   l'existant, sans le brancher. Écrire des tests.
2. **Phase 1** : implémenter le DISPATCHER tiny en porte d'entrée. Si
   `intent=ALEXA`, court-circuit direct. Tout le reste continue comme avant.
   Gain immédiat sur la latence des cas triviaux.
3. **Phase 2** : remplacer `_run_request` méga-loop par l'EXECUTOR sur les
   nouveaux DEEP. Les anciens chemins (`delegate_to` récursif) restent
   disponibles le temps de stabiliser.
4. **Phase 3** : supprimer `plan_writer` (devient render-only), supprimer
   `manage_todo_list` (la todo est l'arbre), supprimer `set_task_class`
   (le DISPATCHER s'en charge).
5. **Phase 4** : nettoyer les paradigmes inutiles (les anti-loops, les MUST
   de migration 057-061). Le plan métier remplace les incantations.

Chaque phase reste testable en isolation via MockClient.

---

## K. Ce qu'on ne propose PAS (volontairement)

- Pas de RAG / embeddings. Workspace + grep suffisent à notre échelle.
- Pas de scheduler async / threading. Mono-thread, un appel LLM à la fois.
  La simplicité bat la performance ici (Ollama est de toute façon
  séquentialisé côté GPU).
- Pas de file de messages / event bus distribué. Les `events.jsonl` plats
  suffisent.
- Pas de nouveau format de prompt. On garde le rendu actuel (paradigmes +
  identité + contrat), on change juste **ce qu'on appelle, quand, avec quel
  modèle, avec quelle entrée**.

---

## Questions ouvertes à trancher avec toi

1. **K=1 ou K=3 appels LLM par tâche ?** (section E)
2. **Le PLANNER peut-il re-plan ?** Ma proposition : non, mais discutable.
3. **`pick_next` priorité profondeur ou breadth ?** Profondeur d'abord
   referme l'arbre vite. Breadth permet parallélisation future. À choisir.
4. **DISPATCHER : 1.5B Ollama ou regex simple ?** ([analyse coût/robustesse](#a-le-dispatcher--llm-ridicule-en-porte-d-entrée))
5. **Garder le concept de "conversation phase" (`gather/critic/build`) ou
   tout passer par `kind` de tâche ?** Ma proposition : tout par `kind`,
   le concept de phase disparaît.
6. **Routage par DB ou hardcodé dans le code ?** DB = configurable mais
   ajoute une indirection. Code = simple mais nécessite redéploiement.
7. **Que devient `delegate_to` côté LLM ?** Il disparaît du tool set. Le
   LLM ne délègue plus — il *propose des sous-tâches* en JSON, et
   l'executor décide. Plus de risque d'explosion en largeur.

À toi la balle : on tranche ensemble, puis je rédige le `03_implementation_plan.md`
avec les étapes concrètes et les fichiers à toucher.

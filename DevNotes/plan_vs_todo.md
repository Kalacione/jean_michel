Analyse approfondie. Voici le mapping complet avant de conclure.

---

## Cartographie plan.md

**Écritures** (4 endroits dans l'orchestrateur) :

| Point | Condition | Contenu écrit |
|---|---|---|
| `delegate_to` avant délégation (L.1099) | 1ère délégation de la requête | steps + todo_block (si todo.json existe) |
| `delegate_to` après retour enfant (L.1183) | Toujours | idem, statut step mis à jour |
| `manage_todo_list` handler (L.1331) | scope == "conversation" (router) | steps vides + todo_block |
| `_plan_log_action` (L.1589) | Après chaque tool call logué | steps + todo_block |

**Lectures** (5 endroits) :

| Point | Usage |
|---|---|
| `prompts.py:_render_plan_block()` | Injecté dans le **system prompt** une fois par requête |
| `prompts.py:render_plan_recap()` | Ré-injecté dans le **user message** entre chaque itération LLM |
| `orchestrator.py:804-818` | **Deep-research guard** : `if plan.md exists → strip web_search/wikipedia_*` |
| `plan_writer.py:_render_todo_block()` | **Interne** : lit `todo.json` pour l'inclure dans plan.md à chaque écriture |
| `inspect_conv.py` | Debug uniquement |

---

## Les vrais problèmes

**1. `set_task_class()` existe mais n'est jamais appelé**

```python
# db.py:253 — helper présent, jamais utilisé
def set_task_class(conn, conv_id, task_class): ...
```
L'orchestrateur ne sait PAS si la requête est `deep_research`, `medium_task`, ou `single_fact`. Le LLM le classe dans `<think>` mais ça reste privé.

**2. Le deep-research guard est déclenché par `plan.md`, pas par la complexité**

Le guard actuel :
```python
if agent.role == "router" and (conv_folder / "plan.md").exists():
    # strip web_search, wikipedia_*
```
Problème : `manage_todo_list` (router) appelle `_plan_writer.write()` → crée `plan.md` → active le guard dès le premier appel, **avant toute délégation**. En mode `chat`, si jean-michel voulait faire un `web_search` lui-même (cas légitimes en chat simple), il se retrouve bloqué prématurément.

**3. La "circular concern" — pas un deadlock, mais un timing**

```
manage_todo_list → todo.json créé → _plan_writer.write() → plan.md créé (steps=[]) → guard activé
delegate_to → _plan_writer.write() → plan.md mis à jour (steps=[S1...])
```
Pas de cycle, mais le guard s'active **avant la première délégation** si manage_todo_list est appelé en premier. Effet de bord potentiel en `chat` mode.

**4. plan.md vs todo.json — complémentaires, non redondants**

| | plan.md | todo.json |
|---|---|---|
| Quand | Post-délégation (déterministe) | Pré-délégation (LLM intent) |
| Granularité | Délégations (S1, S2, S1.1…) | Sous-tâches libres |
| Dépendances | Implicites (ordre temporel) | Explicites (`depends_on` DAG) |
| Qui écrit | Orchestrateur uniquement | LLM via tool |

---

## Proposition : wirer `set_task_class` comme outil léger

Le helper DB existe. On en fait un outil obligatoire **avant toute délégation**. L'orchestrateur bloque le premier `delegate_to` si `task_class` n'a pas été déclaré. Une fois déclaré :
- `deep_research` → orchestrateur exige aussi `manage_todo_list` avant de laisser passer `delegate_to`
- `medium_task` / `single_fact` → `delegate_to` autorisé directement

**Avantages** :
- Un seul appel minimal (`set_task_class("medium_task")`) suffit pour les cas simples — pas de todo overhead
- `deep_research` → enforce manage_todo_list structurellement, sans texte dans le prompt
- `set_task_class` n'écrit PAS plan.md → le guard deep-research n'est pas déclenché prématurément
- Compatible avec l'existing `assess_complexity_first` paradigm (qui demande déjà au LLM de classifier)

**Effets de bord** :
- `set_task_class` apparaît dans le payload tools (légèrement plus grand)
- Chaque requête qui délègue ajoute 1 tool call → 1 LLM step consommé
- Si le LLM n'appelle pas `set_task_class` → même problème de compliance. Mais : `set_task_class("medium_task")` est un appel *trivial* (aucun argument complexe) — bien plus simple à respecter que `manage_todo_list(operation="write", todos=[...])`

**Effets de bord du guard côté `chat` mode** :
- Actuellement manage_todo_list est restreint à `analyse+chat` via `paradigm_modes`
- Si on ajoute le gate "set_task_class avant delegate" en `analyse+chat` seulement → guard n'active pas en `vocal`
- En `chat`, jean-michel peut répondre directement sans déléguer → gate ne s'active pas pour les requêtes triviales

---

Je t'implémente ça ? Ce serait :
1. `set_task_class` tool (minimal — 1 param `class_name`)
2. Gate dans `delegate_to` : block si pas encore classifié (dans les modes `analyse` + `chat`)  
3. Si `deep_research` classifié : second gate requis (`manage_todo_list` avant passage)
4. Paradigm update : `assess_complexity_first` appelle `set_task_class` (non `<think>`) 


---


Commit `b9a39c9` — 332 tests verts.

**Ce qui a été livré :**

- **`set_task_class` tool** : appel unique, un seul paramètre enum — overhead minimal pour le LLM.
- **Gate 1 (classify_first)** : en mode `analyse`/`chat`, tout `delegate_to` est bloqué tant que `set_task_class` n'a pas été appelé. Transparent dès le tour 2 (task_class chargée depuis la DB).
- **Gate 2 (plan_first)** : si `task_class == "deep_research"`, `delegate_to` est bloqué supplémentairement tant que `manage_todo_list(write)` n'a pas été appelé. Satisfait par un `todo.json` existant d'un tour précédent.
- **Aucun effet de bord sur le deep-research guard existant** (`plan.md → strip web_search`) : `set_task_class` n'écrit pas `plan.md`, le guard n'est pas déclenché prématurément.
- **Paradigme `assess_complexity_first` mis à jour** : "call `set_task_class`" remplace "classify in `<think>`".
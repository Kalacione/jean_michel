# 07 — Pipeline GATHER → CRITIC → BUILD imposé

**Référence audit** : §2.6, §2.9, §3.5, §3.6 — `critical-thinker` et `document-builder` **jamais invoqués**, aucun fichier workspace produit malgré ~75 appels d'outils de recherche.
**Dépend de** : [05](05_remove_planner_agent.md) (verbes `gather_done` / `critic_done` / `build_done`).

## Problème

Le paradigme `research_phase_routing` (présent en DB) prescrit la séquence GATHER → CRITIC → BUILD pour les tâches `deep_research`. Mais le routeur (jean-michel) :
- Délègue 3× à web-search et 2× à wikipedia (phase GATHER).
- Saute totalement CRITIC.
- Saute totalement BUILD.
- Retourne directement à l'utilisateur (ou est interrompu par le hang).

Aucune contrainte côté orchestrateur n'empêche ce shortcut. Le paradigme est descriptif, pas prescriptif.

## Solution — state machine côté orchestrateur

### A. Suivi de phase par requête racine

Quand jean-michel détecte qu'une requête est `deep_research` (via le paradigme `assess_complexity_first` qui le pousse à classifier dans son thought channel), il appelle `plan_update(action="init", ...)` (cf. doc 06). Cette initialisation enregistre en DB :

```sql
UPDATE conversations SET task_class = 'deep_research', current_phase = 'planner_done'
WHERE id = ?;
```

Schéma `conversations` (à amender en migration 044 ou nouvelle 045) :

```sql
ALTER TABLE conversations ADD COLUMN task_class TEXT;     -- 'single_fact' | 'medium_task' | 'deep_research'
ALTER TABLE conversations ADD COLUMN current_phase TEXT;  -- NULL | 'planner_done' | 'gather_done' | 'critic_done' | 'build_done'
```

Mirror dans `db/schema.sql` : ajouter ces 2 colonnes à `CREATE TABLE conversations` (lignes ~118).

### B. Transitions autorisées

| current_phase | Verbes acceptés en sortie de la prochaine délégation |
|---------------|------------------------------------------------------|
| NULL | `planner_done` (uniquement), via `plan_update(init)` |
| `planner_done` | `gather_done` (délégation à web-search ou wikipedia obligatoire) |
| `gather_done` | `critic_done` (délégation à critical-thinker obligatoire) ; OU re-`gather_done` si plan_update a ajouté des substeps |
| `critic_done` | `build_done` (délégation à document-builder obligatoire) ; OU retour à `gather_done` si critic identifie un gap |
| `build_done` | `return_to_user` autorisé |

Implémentation côté orchestrateur : avant de traiter un `delegate_to(...)` ou un `return_to_user(...)` émis par **jean-michel** sur une conversation `task_class='deep_research'`, vérifier la transition :

```python
_PHASE_NEXT = {
    None: {"planner_done"},
    "planner_done": {"gather_done"},
    "gather_done": {"critic_done", "gather_done"},
    "critic_done": {"build_done", "gather_done"},
    "build_done": {"return_to_user"},
}

# When jean-michel calls delegate_to(target):
expected_phase_completion = _expected_completion_for_target(target)
# (web-search/wikipedia → gather_done ; critical-thinker → critic_done ; document-builder → build_done)
if expected_phase_completion not in _PHASE_NEXT[current_phase]:
    tool_responses.append(json.dumps({
        "tool": "delegate_to", "error":
            f"Pipeline violation: current phase is {current_phase!r}. "
            f"Next expected completion is one of {sorted(_PHASE_NEXT[current_phase])}. "
            f"Delegating to {target!r} would produce {expected_phase_completion!r}.",
    }))
    continue
```

Similairement pour `return_to_user` : refuser tant que `current_phase != 'build_done'` (uniquement pour `task_class='deep_research'`). Pour `single_fact` / `medium_task`, aucune contrainte.

### C. Mise à jour automatique du phase courant

Quand un specialist termine via `gather_done` / `critic_done` / `build_done` (cf. doc 05), l'orchestrateur fait :

```python
db.update_conversation_phase(conn, conv_id, phase + "_done")
```

`src/jeanmichel/db.py` :

```python
def update_conversation_phase(conn, conv_id: str, phase: str) -> None:
    conn.execute(
        "UPDATE conversations SET current_phase=?, modified_at=datetime('now') WHERE id=?",
        (phase, conv_id),
    )
```

### D. Paradigmes — exposer la machine à jean-michel

Migration (suite de 044 ou nouvelle 045) :

```sql
UPDATE paradigms
SET content = '- For deep_research tasks, the orchestrator enforces the pipeline GATHER → CRITIC → BUILD.
- Phase order (you cannot skip):
    1. PLAN: call plan_update(action="init", ...) to materialise the plan in workspace/plan.md.
    2. GATHER: delegate_to web-search-specialist and/or wikipedia-specialist. Each completes with gather_done.
    3. CRITIC: delegate_to critical-thinker with the gather artifacts in support_files. Completes with critic_done.
    4. BUILD: delegate_to document-builder with the gather + critic artifacts. Completes with build_done.
    5. RETURN: call return_to_user with a concise summary referencing the final workspace document.
- After each phase, plan_update(action="mark", step_id=..., status="done", findings=...) before moving to the next phase.
- If CRITIC identifies a gap, you may go back to GATHER once (the orchestrator allows gather_done → critic_done → gather_done → critic_done loop, but BUILD must be the eventual outcome).',
    modified_at = datetime('now')
WHERE code = 'research_phase_routing';
```

Mirror schema.sql.

### E. Exposer le phase courant dans le prompt

`src/jeanmichel/prompts.py` : dans le rendu du system prompt pour jean-michel, ajouter un bloc :

```markdown
# PIPELINE STATE
task_class: deep_research
current_phase: gather_done
next_allowed: critic_done (delegate_to critical-thinker) OR another gather_done if a substep was added
```

Lu depuis la DB au moment de `_run_request` pour jean-michel.

## Cas de bordure

- **`task_class` jamais setté** (jean-michel a oublié de classifier) → traiter comme `single_fact`, aucune contrainte de pipeline. Le paradigme `assess_complexity_first` doit pousser à classifier explicitement ; à terme, `plan_update(action="init")` pourrait bumper automatiquement `task_class='deep_research'`.
- **L'humain change d'avis pendant la conversation** : `plan_update(action="reset")` archive le plan ; le phase reset à `planner_done`.

## Tests

`tests/test_pipeline_enforcement.py` :

1. **`test_skip_gather_blocked`** : task_class=deep_research, current_phase=planner_done → `delegate_to(critical-thinker)` rejeté avec message clair.
2. **`test_full_flow`** : enchaînement plan_update init → gather_done → critic_done → build_done → return_to_user, chaque transition OK.
3. **`test_critic_can_loop_back_to_gather`** : critic_done → gather_done (substep ajouté) → critic_done → build_done.
4. **`test_single_fact_no_constraint`** : task_class=single_fact, jean-michel peut return_to_user directement.

## Critères d'acceptation

- Sur un replay synthétique de la conversation auditée, jean-michel ne peut **plus** `return_to_user` sans être passé par GATHER + CRITIC + BUILD.
- `document-builder` produit nécessairement au moins un fichier dans `workspace/` (vérifié par le critère du doc 09).

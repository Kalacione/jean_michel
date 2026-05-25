# 06 — Outil `plan_update` (remplaçant mécanique du planner)

**Référence audit** : §2.2, §2.5, §2.8 — plan jamais enrichi, pas d'imbrication des findings.
**Décision** : Q3 — tool mécanique uniquement.
**Dépend de** : [05](05_remove_planner_agent.md) (planner agent désactivé).

## Problème

Le plan vit comme `workspace/plan.md`. Il devrait :
1. Être créé déterministiquement au début d'une tâche `deep_research`.
2. Être patché incrémentalement après chaque retour de specialist, **avec les findings**.
3. Être lu/écrit sans LLM dans la boucle (5 LLM round-trips planner dans la conversation auditée pour un patch trivial).

## Solution — outil context-bound `plan_update`

### A. Spécification

| Opération | Effet |
|-----------|-------|
| `plan_update(action="init", title="...", steps=[{"id":"S1","title":"...","agent":"web-search-specialist","deliverable":"gather/sources.md"}, ...])` | Crée `workspace/plan.md` à partir d'un template Markdown. Refuse si le fichier existe déjà. |
| `plan_update(action="mark", step_id="S1", status="in_progress" \| "done" \| "blocked", findings="...")` | Patch en place : change le statut du step et **ajoute** un bloc `### Findings (S1)` (ou met à jour s'il existe) sous le step. |
| `plan_update(action="add_substep", parent_step_id="S1", title="...", reason="...")` | Insère un sub-step (`S1.1`, `S1.2`…) sous le parent (cas page d'homonymie wiki, lien à creuser). |
| `plan_update(action="reset", new_steps=[...])` | Re-génère plan.md complet (escape hatch ; archive l'ancien dans `workspace/plan.archive.<ts>.md`). |
| `plan_update(action="read")` | Retourne le contenu courant de `plan.md` (équivalent `workspace_view` mais expose la structure parsée). |

### B. Format Markdown canonique

```markdown
# Plan — <title>

_Created: <iso8601> · Last updated: <iso8601>_

## Steps

### S1 — <step title> [⬜ pending | 🟡 in_progress | ✅ done | 🔴 blocked]
- Agent: `<agent_code>`
- Deliverable: `<path>`

#### Findings (S1)
<markdown text injected by plan_update action="mark">

### S1.1 — <substep title> [⬜ pending]
- Parent: S1
- Reason: <why this substep was added>
...

### S2 — ...

## Revision log
- <iso8601> · <action> · <one-line description>
```

L'outil parse le markdown avec une regex simple (ou un parser dédié si volume justifie). Les modifications sont **diffables** et reproductibles.

### C. Fichier `src/jeanmichel/tools/plan_update.py`

Squelette :

```python
"""Tool: plan_update — mechanical create/patch of workspace/plan.md."""

from __future__ import annotations
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ._base import ToolSpec
from ._workspace import safe_resolve, quota_remaining, workspace_root_for

_PLAN_FILENAME = "plan.md"
_STATUS_MAP = {
    "pending": "⬜ pending",
    "in_progress": "🟡 in_progress",
    "done": "✅ done",
    "blocked": "🔴 blocked",
}

def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    def _handler(action: str, **kwargs) -> str:
        if not has_write_grant and action != "read":
            return json.dumps({"error": "Write access not granted for this agent."})
        ws_root = workspace_root_for(conv_folder)
        plan_path = safe_resolve(ws_root, _PLAN_FILENAME)
        try:
            if action == "init":
                return _do_init(plan_path, ws_root, **kwargs)
            if action == "mark":
                return _do_mark(plan_path, **kwargs)
            if action == "add_substep":
                return _do_add_substep(plan_path, **kwargs)
            if action == "reset":
                return _do_reset(plan_path, ws_root, **kwargs)
            if action == "read":
                return _do_read(plan_path)
            return json.dumps({"error": f"unknown action: {action!r}"})
        except _PlanError as e:
            return json.dumps({"error": str(e)})

    return ToolSpec(
        name="plan_update",
        description=(
            "Create or patch the conversation's plan.md. Mechanical, deterministic. "
            "Actions: init | mark | add_substep | reset | read. "
            "Use this instead of workspace_create_file for plan.md."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["init", "mark", "add_substep", "reset", "read"]},
                "title": {"type": "string"},
                "steps": {"type": "array"},
                "step_id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["pending", "in_progress", "done", "blocked"]},
                "findings": {"type": "string"},
                "parent_step_id": {"type": "string"},
                "reason": {"type": "string"},
                "new_steps": {"type": "array"},
            },
            "required": ["action"],
        },
        handler=_handler,
    )

class _PlanError(Exception): ...

def _do_init(...): ...   # raises if plan.md exists ; writes template
def _do_mark(...): ...   # regex-locates "### <step_id> — ... [...]", swaps status, injects findings
def _do_add_substep(...): ...
def _do_reset(...): ...  # archive old plan, write new
def _do_read(...): ...
```

Les fonctions `_do_*` font des opérations textuelles (parse + replace), pas de LLM, pas d'appel externe.

### D. Grants — `db/migrate_044_remove_planner_agent.sql` (suite)

Ajouter à la migration 044 (déjà créée en doc 05) :

```sql
-- 6. Grant plan_update to agents that need to write the plan.
--    jean-michel: init / mark / add_substep / reset / read (full ownership of the plan)
--    specialists with workspace write grant: mark / add_substep / read
--    (the orchestrator does NOT police action granularity; we rely on paradigms.)
INSERT INTO agent_tools (agent_id, tool_code, created_at) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'),           'plan_update', datetime('now')),
  ((SELECT id FROM agents WHERE code='web-search-specialist'), 'plan_update', datetime('now')),
  ((SELECT id FROM agents WHERE code='wikipedia-specialist'),  'plan_update', datetime('now')),
  ((SELECT id FROM agents WHERE code='critical-thinker'),      'plan_update', datetime('now')),
  ((SELECT id FROM agents WHERE code='document-builder'),      'plan_update', datetime('now'));
```

Mirror dans `db/schema.sql` :
- Section grants : ajouter les 5 lignes `INSERT INTO agent_tools` ci-dessus.

### E. Registry — `src/jeanmichel/tools/__init__.py`

Importer et brancher dans `build_registry` :

```python
from . import plan_update
...
def build_registry(conv_folder, *, has_workspace_write, ...):
    registry = {...}
    if has_workspace_write:
        registry["workspace_create_file"] = workspace_create_file.make_spec(conv_folder, has_write_grant=True)
        registry["workspace_str_replace"] = workspace_str_replace.make_spec(conv_folder, has_write_grant=True)
        registry["plan_update"] = plan_update.make_spec(conv_folder, has_write_grant=True)
    else:
        registry["plan_update"] = plan_update.make_spec(conv_folder, has_write_grant=False)  # read-only
    ...
```

### F. Paradigme — `task_plan_file` mis à jour

Migration 044 (suite) :

```sql
-- 7. Update task_plan_file paradigm to reference plan_update instead of workspace_create_file
UPDATE paradigms
SET content = '- For deep_research or multi-turn tasks, maintain a workspace/plan.md file as the single source of truth for the task state. Create it via plan_update(action="init", ...) at the start of the first turn.
- Read the current plan via plan_update(action="read") before deciding what to do next.
- Mark steps as you progress via plan_update(action="mark", step_id="...", status="in_progress" | "done" | "blocked", findings="...").
- If a sub-research emerges (disambiguation, link to follow), call plan_update(action="add_substep", parent_step_id="...", title="...", reason="...").
- NEVER call workspace_create_file with relative_path="plan.md". The plan is managed exclusively via plan_update.',
    modified_at = datetime('now')
WHERE code = 'task_plan_file';
```

Mirror dans `schema.sql` (chercher l'UPDATE existant sur `task_plan_file` ligne ~1825/2084 et le remplacer).

### G. Garde-fou dans `workspace_create_file`

Refuser explicitement `relative_path == "plan.md"` :

```python
# in workspace_create_file.py _handler
if normalised_path == "plan.md":
    return json.dumps({
        "error": "plan.md is managed by the plan_update tool. "
                 "Use plan_update(action='init' | 'mark' | 'add_substep') instead.",
        "action_required": "plan_update",
    })
```

(`normalised_path` = result of strip-workspace from doc 02.)

## Tests

`tests/test_plan_update.py` :

1. **`test_init_creates_plan`** : action=init avec 3 steps → plan.md créé, format Markdown valide.
2. **`test_init_refuses_existing`** : 2 inits consécutifs → 2ᵉ retourne erreur.
3. **`test_mark_swaps_status_and_injects_findings`** : init S1 pending → mark S1 done + findings "x" → plan.md contient `[✅ done]` + `#### Findings (S1)\nx`.
4. **`test_add_substep_creates_S1_1`** : add_substep parent=S1 → S1.1 visible avec parent + reason.
5. **`test_reset_archives_old_plan`** : init + reset → plan.archive.<ts>.md écrit.
6. **`test_workspace_create_file_refuses_plan_md`** : `workspace_create_file(relative_path="plan.md")` → erreur, redirige vers plan_update.
7. **`test_read_only_grant`** : agent sans write grant → action=read OK, action=mark refusé.

## Critères d'acceptation

- Aucun appel `workspace_create_file(relative_path='plan.md')` ne réussit.
- Le contenu de `plan.md` reste lisible humainement, avec findings imbriqués sous chaque step.
- 0 LLM round-trip pour patcher le plan après un retour de specialist.

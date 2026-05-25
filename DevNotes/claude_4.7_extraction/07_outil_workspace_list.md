# Outil — `workspace_list`

## Définition

Liste le contenu du workspace de la conversation courante, avec affichage en arbre limité à 2 niveaux. Outil simple, sans alternative dans `workspace_view` car cette dernière n'expose qu'un listing plat.

## Paramètres

| Param | Type | Required | Description |
|---|---|:-:|---|
| `subpath` | string | (default `""`) | Sous-chemin dans le workspace, ex: `data/`. Vide = racine. |

## Comportement

1. Liste récursive limitée à 2 niveaux.
2. Pour chaque entrée : nom, type (file/directory), taille en bytes (pour fichiers), date de dernière modification.
3. Tri lexicographique par nom.

## Garde-fous

Idem outils précédents : path traversal bloqué, chemin doit rester dans `workspace/`.

## Erreurs typiques

```json
{"error": "Path escapes workspace: '../somewhere'"}
{"error": "Not a directory: 'notes.md'"}
```

## Intégration Jean-Michel

### `src/jeanmichel/tools/workspace_list.py`

```python
from datetime import UTC, datetime
from pathlib import Path
import json
from ._base import ToolSpec

MAX_DEPTH = 2

def make_spec(conv_folder: Path) -> ToolSpec:
    workspace_root = (conv_folder / "workspace").resolve()
    workspace_root.mkdir(exist_ok=True)

    def _handler(subpath: str = "") -> str:
        try:
            target = (workspace_root / subpath).resolve() if subpath else workspace_root
            if not target.is_relative_to(workspace_root):
                return json.dumps({"error": f"Path escapes workspace: {subpath!r}"})
            if not target.is_dir():
                return json.dumps({"error": f"Not a directory: {subpath!r}"})

            def _walk(p: Path, depth: int) -> dict:
                node = {
                    "name": p.name,
                    "type": "directory",
                    "modified_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(),
                }
                if depth < MAX_DEPTH:
                    node["children"] = sorted(
                        ([_walk(c, depth + 1) if c.is_dir()
                          else {"name": c.name, "type": "file",
                                "size_bytes": c.stat().st_size,
                                "modified_at": datetime.fromtimestamp(c.stat().st_mtime, UTC).isoformat()}
                          for c in p.iterdir()]),
                        key=lambda d: d["name"]
                    )
                return node

            tree = _walk(target, 0)
            return json.dumps(tree)
        except Exception as e:
            return json.dumps({"error": f"Tool failed: {e}"})

    return ToolSpec(
        name="workspace_list",
        description=(
            "List the contents of the workspace as a tree (up to 2 levels deep). "
            "Returns names, sizes, and modification dates."
        ),
        parameters={
            "type": "object",
            "properties": {
                "subpath": {"type": "string", "default": ""},
            },
            "required": [],
        },
        handler=_handler,
    )
```

### Grant en BDD

À granter avec `workspace_create_file` (un agent qui peut créer doit pouvoir lister). Lecture seule, donc bénin.

```sql
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_list' FROM agents WHERE code='code-runner';
```

## Tests à prévoir

- Liste workspace vide.
- Liste workspace avec arbre profond (vérifier la coupe à 2 niveaux).
- Refus de path traversal.
- Sortie JSON parseable.

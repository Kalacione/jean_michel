# Outil — `workspace_str_replace`

## Définition

Modifie un fichier existant dans `workspace/` en remplaçant une chaîne unique par une autre. Calqué sur `str_replace` du sysprompt Claude. La chaîne `old_str` doit apparaître **exactement une fois** dans le fichier.

## Paramètres

| Param | Type | Required | Description |
|---|---|:-:|---|
| `relative_path` | string | ✓ | Chemin du fichier à modifier dans le workspace. |
| `old_str` | string | ✓ | Chaîne à remplacer. Doit être unique dans le fichier. |
| `new_str` | string | (default `""`) | Chaîne de remplacement. Vide = suppression. |
| `description` | string | ✓ | Brève justification (loggée). |

## Comportement

1. Validation du chemin (idem `workspace_create_file`).
2. Lecture du fichier (refus si `old_str` n'apparaît pas, ou apparaît plusieurs fois).
3. Remplacement.
4. Écriture du nouveau contenu.
5. Enregistrement en artefact `kind='workspace_file'` (la modification est tracée comme un événement, pas comme un nouveau fichier).
6. Retour : `{"path": "...", "occurrences_replaced": 1, "bytes_after": N}`.

## Garde-fous

- **Path traversal** bloqué (idem create_file).
- **Unicité de `old_str`** : si 0 ou ≥2 occurrences, erreur explicite. Force l'agent à fournir un contexte distinctif autour du segment à modifier.
- **Pas d'édition de fichiers root de la conversation** : seulement le workspace.

## Erreurs typiques

```json
{"error": "File not found: 'notes.md'"}
{"error": "old_str does not appear in 'notes.md'"}
{"error": "old_str appears 3 times in 'notes.md'. Provide more context to make it unique."}
```

## Intégration Jean-Michel

### `src/jeanmichel/tools/workspace_str_replace.py`

```python
from pathlib import Path
import json
from ._base import ToolSpec

def make_spec(conv_folder: Path) -> ToolSpec:
    workspace_root = (conv_folder / "workspace").resolve()
    workspace_root.mkdir(exist_ok=True)

    def _handler(relative_path: str, old_str: str, description: str,
                 new_str: str = "") -> str:
        try:
            target = (workspace_root / relative_path).resolve()
            if not target.is_relative_to(workspace_root):
                return json.dumps({"error": f"Path escapes workspace: {relative_path!r}"})
            if not target.is_file():
                return json.dumps({"error": f"File not found: {relative_path!r}"})
            content = target.read_text(encoding="utf-8")
            count = content.count(old_str)
            if count == 0:
                return json.dumps({"error": f"old_str does not appear in {relative_path!r}"})
            if count > 1:
                return json.dumps({
                    "error": (f"old_str appears {count} times in {relative_path!r}. "
                              f"Provide more context to make it unique.")
                })
            target.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")
            return json.dumps({
                "path": str(target.relative_to(workspace_root)),
                "occurrences_replaced": 1,
                "bytes_after": target.stat().st_size,
            })
        except Exception as e:
            return json.dumps({"error": f"Tool failed: {e}"})

    return ToolSpec(
        name="workspace_str_replace",
        description=(
            "Replace a unique string in a file inside the workspace. "
            "old_str must appear exactly once. View the file before editing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string"},
                "old_str":       {"type": "string"},
                "new_str":       {"type": "string", "default": ""},
                "description":   {"type": "string"},
            },
            "required": ["relative_path", "old_str", "description"],
        },
        handler=_handler,
    )
```

### Grant en BDD

Idem `workspace_create_file` : à granter aux agents qui ont écriture workspace.

## Pourquoi pas un outil "edit-by-line"

L'édition par numéro de ligne est fragile (le fichier change, les numéros aussi). Le pattern `str_replace` avec contexte unique est plus robuste : si l'agent veut modifier "ligne 12", il prend la ligne 12 + ses voisines, ça reste unique sans dépendre d'un compteur.

## Tests à prévoir

- Remplacement nominal.
- Refus quand 0 ou ≥2 occurrences.
- Refus quand fichier hors workspace.
- Suppression (new_str vide).
- Pas de corruption sur écriture interrompue (à terme : écriture atomique via temp file).

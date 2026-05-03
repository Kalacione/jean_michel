# Outil — `workspace_create_file`

## Définition

Crée un nouveau fichier dans le `workspace/` de la conversation courante. Si le fichier existe déjà, l'opération échoue (utiliser `workspace_str_replace` pour modifier).

## Paramètres

| Param | Type | Required | Description |
|---|---|:-:|---|
| `relative_path` | string | ✓ | Chemin relatif au workspace, ex: `notes.md`, `data/results.json`. Pas de `..`, pas d'absolu. |
| `content` | string | ✓ | Contenu complet du fichier à écrire. |
| `description` | string | ✓ | Brève justification de la création (loggée en artefact). |

## Comportement

1. Validation du `relative_path` :
   - Refus de `..`, `/` initial, chemin absolu, lien symbolique.
   - Résolution finale **dans** `{conv_folder}/workspace/` ou erreur.
2. Si le fichier existe → erreur explicite.
3. Création des sous-dossiers parents si nécessaire (`mkdir -p`).
4. Écriture en UTF-8.
5. Enregistrement en BDD comme artefact (`kind='workspace_file'`).
6. Retour structuré : `{"path": "...", "bytes_written": N, "created_at": "..."}`.

## Garde-fous

- **Path traversal** bloqué via `Path.resolve()` + `is_relative_to()`.
- **Quota** : si la taille du workspace dépasse 100 Mo après écriture, refus avec message clair.
- **Pas d'écrasement** : un fichier existant n'est jamais remplacé via cet outil. Force l'agent à utiliser `str_replace` ou `delete + create`, ce qui rend l'intention explicite.

## Erreurs typiques

```json
{"error": "Path escapes workspace: '../conversation.md'"}
{"error": "File already exists: 'notes.md'. Use workspace_str_replace to modify."}
{"error": "Workspace quota exceeded (100MB)."}
```

## Intégration Jean-Michel

### Ajout du tool dans `src/jeanmichel/tools/workspace_create_file.py`

```python
from pathlib import Path
import json
from ._base import ToolSpec

WORKSPACE_QUOTA_BYTES = 100 * 1024 * 1024  # 100 MB

def make_spec(conv_folder: Path) -> ToolSpec:
    workspace_root = (conv_folder / "workspace").resolve()
    workspace_root.mkdir(exist_ok=True)

    def _handler(relative_path: str, content: str, description: str) -> str:
        try:
            target = (workspace_root / relative_path).resolve()
            if not target.is_relative_to(workspace_root):
                return json.dumps({"error": f"Path escapes workspace: {relative_path!r}"})
            if target.exists():
                return json.dumps({"error": f"File already exists: {relative_path!r}"})
            # Quota check
            current_size = sum(p.stat().st_size for p in workspace_root.rglob("*") if p.is_file())
            if current_size + len(content.encode("utf-8")) > WORKSPACE_QUOTA_BYTES:
                return json.dumps({"error": "Workspace quota exceeded (100MB)."})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return json.dumps({
                "path": str(target.relative_to(workspace_root)),
                "bytes_written": len(content.encode("utf-8")),
            })
        except Exception as e:
            return json.dumps({"error": f"Tool failed: {e}"})

    return ToolSpec(
        name="workspace_create_file",
        description=(
            "Create a new file in the conversation workspace. "
            "Path is relative to the workspace root, no '..' allowed. "
            "Fails if the file exists — use workspace_str_replace to modify."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string"},
                "content":       {"type": "string"},
                "description":   {"type": "string"},
            },
            "required": ["relative_path", "content", "description"],
        },
        handler=_handler,
    )
```

### Enregistrement dans `tools/__init__.py`

```python
from . import workspace_create_file as _workspace_create_file_mod

def build_registry(conv_folder: Path) -> dict[str, ToolSpec]:
    return {
        ...,
        _workspace_create_file_mod.make_spec(conv_folder).name:
            _workspace_create_file_mod.make_spec(conv_folder),
    }
```

### Grant en BDD

L'outil est ajouté dans `agent_tools` **uniquement** pour les agents qui ont écriture workspace (table `agent_workspace_grants`). Aujourd'hui : aucun. Mis à disposition pour de futurs agents (code-runner, data-analyst, document-builder).

```sql
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_create_file' FROM agents WHERE code='code-runner';
```

## Tests à prévoir

- Création nominale dans le workspace.
- Refus de path traversal (`../something`, `/etc/passwd`).
- Refus de réécriture sur fichier existant.
- Refus quand quota dépassé.
- Création de sous-dossiers.

# Outil — `workspace_view`

## Définition

Lit un fichier ou liste un dossier dans le workspace. Permet aussi la lecture **en lecture seule** des artefacts root de la conversation (équivalent fonctionnel de `conv_read_file` actuel, étendu).

## Paramètres

| Param | Type | Required | Description |
|---|---|:-:|---|
| `relative_path` | string | ✓ | Chemin relatif au dossier de conversation. Si vide ou `.`, liste le workspace. |
| `view_range` | array of 2 ints | (optional) | `[start_line, end_line]` pour les fichiers texte. `[N, -1]` = de N à la fin. |
| `max_bytes` | integer | (default 100000) | Taille max à retourner. |

## Comportement

1. Validation du chemin :
   - Doit pointer dans `{conv_folder}/` (root + workspace).
   - Pas de `..` final qui sort.
2. Si dossier → liste plate du contenu (pas récursive sauf workspace, qui peut être listé en arbre 2-niveaux).
3. Si fichier texte → lecture (avec `view_range` si fourni).
4. Si fichier binaire ou non-UTF8 → erreur explicite.
5. Si dépasse `max_bytes` → tronquer et signaler dans la réponse.

## Garde-fous

- **Lecture seule sur conversation root** : un fichier hors workspace est lisible mais jamais modifiable par un agent. Cette extension de scope par rapport à `conv_read_file` actuel permet à un agent code-runner de lire, par exemple, un summary.md précédent ou un response.md d'un autre agent.
- **Path traversal** : même validation que les outils précédents. Sortir du dossier de conversation est refusé.

## Différences avec `conv_read_file` actuel

`conv_read_file` lit le contenu de la conversation (root) — c'est ce que les agents font aujourd'hui pour récupérer une réponse précédente déléguée.

`workspace_view` :
- Couvre le même cas d'usage (lecture root)
- Ajoute la lecture du workspace
- Ajoute le listing de dossier
- Ajoute `view_range`

**Reco** : à terme, `workspace_view` remplace `conv_read_file`. Migration : déprécier conv_read_file en double tooling pendant 1 release puis le retirer.

## Intégration Jean-Michel

### `src/jeanmichel/tools/workspace_view.py`

```python
from pathlib import Path
import json
from ._base import ToolSpec

def make_spec(conv_folder: Path) -> ToolSpec:
    conv_root = conv_folder.resolve()
    workspace_root = conv_root / "workspace"

    def _handler(relative_path: str = "", max_bytes: int = 100_000,
                 view_range: list[int] | None = None) -> str:
        try:
            if not relative_path or relative_path == ".":
                target = workspace_root
            else:
                target = (conv_root / relative_path).resolve()
            if not target.is_relative_to(conv_root):
                return json.dumps({"error": f"Path escapes conversation: {relative_path!r}"})
            if not target.exists():
                return json.dumps({"error": f"Not found: {relative_path!r}"})
            if target.is_dir():
                entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
                return json.dumps({"directory": str(target.relative_to(conv_root)),
                                   "entries": entries})
            try:
                content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return json.dumps({"error": "File is not valid UTF-8."})
            if view_range:
                lines = content.splitlines()
                start, end = view_range
                end = len(lines) if end == -1 else end
                content = "\n".join(lines[start - 1:end])
            truncated = False
            if len(content.encode("utf-8")) > max_bytes:
                content = content.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
                truncated = True
            return json.dumps({
                "path": str(target.relative_to(conv_root)),
                "content": content,
                "truncated": truncated,
            })
        except Exception as e:
            return json.dumps({"error": f"Tool failed: {e}"})

    return ToolSpec(
        name="workspace_view",
        description=(
            "View a file or list a directory inside the conversation folder. "
            "Read-only on root files, full access on workspace/. "
            "Use view_range=[start, end] to read specific lines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string"},
                "max_bytes":     {"type": "integer", "default": 100000},
                "view_range":    {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            },
            "required": [],
        },
        handler=_handler,
    )
```

### Grant en BDD

Granter à tous les agents qui peuvent bénéficier de la lecture étendue. Pour les agents existants : reste équivalent à `conv_read_file`. Pour les futurs agents avec workspace : permet aussi la lecture de leurs propres fichiers.

```sql
-- Garde conv_read_file en place pour rétrocompatibilité ;
-- ajoute workspace_view aux mêmes agents
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'workspace_view' FROM agents
WHERE code IN ('jean-michel', 'summarizer', 'critical-thinker',
               'comparator-specialist', 'synthesizer');
```

## Tests à prévoir

- Lecture nominale fichier root (équivalent conv_read_file).
- Lecture nominale fichier workspace.
- Listing du workspace.
- view_range correct.
- Refus de path traversal.
- Refus binaire.
- Tronquage propre.

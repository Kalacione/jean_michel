# 02 — Normalisation des chemins workspace

**Référence audit** : §2.3, §3.9 — création du fichier nested `workspace/workspace/plan.md` (artefact `042220949`).

## Problème

`safe_resolve(workspace_root, "workspace/plan.md")` ([src/jeanmichel/tools/_workspace.py](../../src/jeanmichel/tools/_workspace.py)) résout en `<conv>/workspace/workspace/plan.md`. Le check `is_relative_to(workspace_root)` **passe** (le chemin est bien dans le workspace), donc aucune erreur n'est levée. Conséquence : un sous-dossier `workspace/` parasite est créé silencieusement.

Le LLM y est conduit parce que le prompt mentionne fréquemment `workspace/plan.md` comme chemin canonique, et il le recopie verbatim dans l'argument `relative_path`.

## Solution — strip + warning loggué (décision Q6)

Dans `safe_resolve`, **avant** le `(workspace_root / relative_path).resolve()` :

1. Strip leading `workspace/`, `./workspace/`, `/workspace/` (insensible à la casse).
2. Refuser les paths absolus (`relative_path.startswith("/")`) → `ValueError`.
3. Refuser les composants `..` (déjà couvert par le `is_relative_to` mais explicite avant).
4. Émettre un warning structuré (via `logging`) si un strip a eu lieu, avec le path original et le path normalisé. Ce warning sera capté par `journal.log` ou un futur observability hook.

### Patch proposé

```python
# src/jeanmichel/tools/_workspace.py
import logging
import re
from pathlib import Path
from ..config import WORKSPACE_QUOTA_BYTES

_log = logging.getLogger(__name__)
_STRIP_PREFIX_RE = re.compile(r"^(?:\./)?workspace/+", re.IGNORECASE)


def safe_resolve(workspace_root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path must be a non-empty string.")
    raw = relative_path
    # 1. Reject absolute paths outright
    if raw.startswith("/"):
        raise ValueError(
            f"Path {raw!r} is absolute. Use a path relative to the workspace root."
        )
    # 2. Strip leading workspace/ prefix (defensive against LLM recopying the prefix)
    normalised = _STRIP_PREFIX_RE.sub("", raw, count=1)
    if normalised != raw:
        _log.warning(
            "workspace path normalised: %r → %r (leading 'workspace/' stripped)",
            raw, normalised,
        )
    if not normalised:
        raise ValueError(f"Path {raw!r} resolves to the workspace root itself.")
    candidate = (workspace_root / normalised).resolve()
    if not candidate.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"Path {raw!r} escapes the workspace root.")
    return candidate
```

## Tests

`tests/test_workspace_path_normalization.py` :

1. `safe_resolve(ws, "workspace/plan.md")` → `ws/plan.md` + 1 warning loggué (utiliser `caplog`).
2. `safe_resolve(ws, "WORKSPACE/plan.md")` → idem (case-insensitive).
3. `safe_resolve(ws, "plan.md")` → `ws/plan.md` sans warning.
4. `safe_resolve(ws, "/etc/passwd")` → `ValueError("absolute")`.
5. `safe_resolve(ws, "../escape.md")` → `ValueError("escapes")`.
6. `safe_resolve(ws, "workspace/")` → `ValueError("resolves to the workspace root")`.
7. Test e2e : un `MockClient` qui force le LLM à appeler `workspace_create_file(relative_path="workspace/notes.md")` → un seul `notes.md` créé à la racine du workspace.

## Aucune modification BDD

## Critères d'acceptation

- Aucun fichier `workspace/workspace/*` ne peut plus être créé.
- `workspace_create_file`, `workspace_str_replace`, `workspace_view` bénéficient tous du strip (puisqu'ils passent par `safe_resolve`).
- Le warning loggué permet d'identifier les agents qui produisent encore le préfixe (pour ajustements de prompt ultérieurs).

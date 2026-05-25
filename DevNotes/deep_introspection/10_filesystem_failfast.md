# 10 — Fail-fast sur erreurs filesystem

**Référence audit** : §2.10, §3.4 — Erreurs FS (file not found, path traversal denied, quota dépassé) traitées silencieusement. Pas de post-mortem à `step_budget_exhausted`.

## Problème

Aujourd'hui, les outils workspace retournent une string JSON `{"error": "..."}` que l'orchestrateur injecte tel quel dans `tool_responses`. Le LLM peut l'ignorer, mal l'interpréter, ou boucler. Pour certaines erreurs **critiques** (file not found persistant, quota épuisé, path traversal denied), il vaut mieux : (a) logger côté orchestrateur, (b) signaler clairement au parent agent, (c) compter ces erreurs comme un signal de boucle.

## Solution

### A. Classer les erreurs des tools

`src/jeanmichel/tools/_base.py` (ou un nouveau `_errors.py`) :

```python
CRITICAL_ERROR_CODES = frozenset({
    "path_escape",        # safe_resolve refused (traversal)
    "quota_exceeded",     # workspace quota
    "file_not_found",     # read on missing file
    "absolute_path",      # absolute path attempted
})

def tool_error(code: str, message: str, **extra) -> str:
    payload = {"error": message, "error_code": code}
    payload.update(extra)
    return json.dumps(payload)
```

Refactorer `_workspace.py`, `workspace_view.py`, `workspace_create_file.py`, `workspace_str_replace.py`, `workspace_list.py`, `conv_read_file.py` pour qu'elles utilisent `tool_error(code, ...)` au lieu de strings JSON ad-hoc. (Garder le champ `error` lisible pour le LLM ; ajouter `error_code` pour la machine.)

Exemple :

```python
# in workspace_view.py
if not target.exists():
    return tool_error("file_not_found",
                      f"File does not exist: {relative_path}",
                      relative_path=relative_path)
```

### B. Détection orchestrateur

Dans `_run_request`, après l'exécution du tool :

```python
result_obj = None
try:
    result_obj = json.loads(result) if isinstance(result, str) else None
except json.JSONDecodeError:
    pass

error_code = (result_obj or {}).get("error_code") if isinstance(result_obj, dict) else None

if error_code in CRITICAL_ERROR_CODES:
    _critical_fs_errors += 1
    yield FilesystemErrorObserved(agent_code=agent_code, tool_name=call.name,
                                  error_code=error_code,
                                  message=(result_obj or {}).get("error", ""))
    if _critical_fs_errors >= 3:
        # Fail fast: 3 critical FS errors in one request = something is fundamentally wrong.
        return self._fail_request(
            req_id, agent_code, "critical_fs_errors",
            f"Agent encountered {_critical_fs_errors} critical filesystem errors in one request."
        )
```

`FilesystemErrorObserved` est un nouvel event dataclass (à yielder vers la CLI pour visibilité).

### C. Refus de `support_files` invalides (déjà présent) — durcir

L'orchestrateur valide déjà l'existence des `support_files` dans la branche `delegate_to` (vu en lecture du code). On garde mais on classe le rejet comme `file_not_found` côté orchestrateur (et on incrémente le compteur).

### D. Quota — alerter avant l'épuisement

Quand `quota_remaining(ws_root) < 10% * WORKSPACE_QUOTA_BYTES` (seuil mou), l'orchestrateur émet un événement `QuotaWarning` (non bloquant) que la CLI affiche en jaune. Si quota épuisé pendant l'écriture → erreur critique habituelle.

### E. Pas de paradigme à modifier

Ces changements sont purement code/orchestrateur. Aucune migration SQL nécessaire.

### F. Nouveaux événements

```python
@dataclass
class FilesystemErrorObserved:
    agent_code: str
    tool_name: str
    error_code: str
    message: str

@dataclass
class QuotaWarning:
    remaining_bytes: int
    total_bytes: int
```

## Tests

`tests/test_filesystem_failfast.py` :

1. **`test_file_not_found_counts`** : MockClient script → 3 calls `workspace_view(relative_path="nope.md")` → après le 3ᵉ, request fail avec `critical_fs_errors`.
2. **`test_path_escape_logged`** : `workspace_create_file(relative_path="../escape.md")` → event `FilesystemErrorObserved(error_code="path_escape")`.
3. **`test_quota_warning_threshold`** : pré-remplir le workspace à 95% du quota, écrire un petit fichier supplémentaire → `QuotaWarning` event.
4. **`test_quota_exceeded_critical`** : écrire un fichier qui dépasse → `error_code="quota_exceeded"` + compteur incrémenté.

## Critères d'acceptation

- Toute erreur FS critique est traçable dans la CLI (event yielded).
- 3 erreurs FS critiques dans une même requête provoquent un `fail-fast` avec status `critical_fs_errors`.
- Aucun outil workspace ne retourne une erreur sans `error_code` machine-lisible.

# 03 — Détection de boucles (fingerprint normalisé + verrou strict)

**Référence audit** : §3.1, §3.2, §3.3 — Phase 5 (7× arXiv, 4× sci DBs), Phase 7 (10× `wikipedia_search('MediaWiki API')` consécutifs).

## Problème

Le filtre `_successful_calls` ([src/jeanmichel/orchestrator.py:515-547](../../src/jeanmichel/orchestrator.py)) calcule un fingerprint :

```python
call_fingerprint = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
```

Faiblesses observées dans la conversation `2026-05-24_04-08_ca049ab8be2a` :

1. **Sensibilité au bruit** : `"MediaWiki API"` vs `"mediawiki api"` vs `"MediaWiki  API"` (espaces, casse) produisent 3 fingerprints distincts. Idem pour `dumps` vs `dump`, `arXiv API documentation` vs `arXiv documentation API`.
2. **Sensibilité aux paramètres optionnels** : `wikipedia_search(query="X")` vs `wikipedia_search(query="X", results=5)` (valeur par défaut) produisent 2 fingerprints distincts si le LLM omet/ajoute le paramètre.
3. **Pas de "near-duplicate"** : reformulation à 1 caractère contourne le filtre (audit Phase 2 : `dumps` → `dump`).

**Note KISS** : on ne touche **pas** aux outils individuels (`web_search.py`, `wikipedia.py`, etc.). Tout se règle au niveau de l'orchestrateur, de façon uniforme. Décision Q4 = stricte + normalisation.

## Solution

### A. Fingerprint normalisé (couvre 90% des cas)

Nouvelle fonction utilitaire dans `src/jeanmichel/orchestrator.py` :

```python
import re
_WS_RE = re.compile(r"\s+")

def _normalise_value(v):
    if isinstance(v, str):
        return _WS_RE.sub(" ", v.strip().lower())
    if isinstance(v, list):
        return [_normalise_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _normalise_value(x) for k, x in v.items()}
    return v

def _fingerprint(tool_name: str, args: dict, defaults: dict) -> str:
    """Stable fingerprint robust to whitespace, casing, and omitted defaults."""
    merged = {**defaults, **(args or {})}
    norm = {k: _normalise_value(v) for k, v in merged.items()}
    return f"{tool_name}:{json.dumps(norm, sort_keys=True, ensure_ascii=False)}"
```

Les valeurs par défaut de chaque outil sont récupérées depuis `ToolSpec.parameters["properties"][k].get("default")` au moment du build_registry — pas besoin de re-déclarer.

### B. Verrou strict — 2ᵉ occurrence = bloquée

Remplacer la logique actuelle :

```python
# avant
if call_fingerprint in _successful_calls:
    tool_responses.append(json.dumps({"error": "Duplicate call detected. ..."}))
    continue
# … exécution …
if not (isinstance(result, str) and '"error"' in result):
    _successful_calls.add(call_fingerprint)
```

par :

```python
defaults = _spec_defaults(spec)  # cached
fp = _fingerprint(call.name, call.arguments, defaults)
if fp in _seen_calls:
    tool_responses.append(json.dumps({
        "tool": call.name,
        "error": (
            "Duplicate call blocked. This call (after normalising whitespace, "
            "casing, and default-valued arguments) was already executed in "
            "this request. Re-running it cannot change the result. "
            "Either: (a) use the result you already have, or (b) change the "
            "ANGLE of your query (different keyword, different domain, different tool) "
            "— not a surface reformulation."
        ),
    }))
    _consecutive_duplicates += 1
    if _consecutive_duplicates >= 3:
        # Hard stop: force return_to_user with current state
        yield ForcedConvergence(agent_code=agent_code,
                                reason="3 consecutive duplicate-blocked calls")
        # ...fail the request gracefully (see below)
    continue
_seen_calls.add(fp)  # register BEFORE execution (idempotent: ws lookups, web searches)
_consecutive_duplicates = 0
```

**Différence clef** : on enregistre **avant** l'exécution (un duplicate bloqué était déjà problématique même quand le 1er call retournait une erreur). Le filtre s'applique aussi aux calls qui ont échoué — refaire la même requête échouera de la même façon.

### C. Détection de boucle "verrou tournant" — 3 duplicates consécutifs

Si l'agent reçoit 3 erreurs "Duplicate call blocked" d'affilée sans produire d'autre tool_call valide, l'orchestrateur **force la sortie** en faisant échouer la requête avec `status="loop_detected"`. Le parent agent reçoit alors un objet structuré :

```json
{"tool": "delegate_to", "agent": "...", "error": "loop_detected",
 "summary": "child agent looped on duplicate calls and was force-stopped"}
```

### D. Nouvel événement

```python
@dataclass
class DuplicateCallBlocked:
    agent_code: str
    tool_name: str
    fingerprint: str

@dataclass
class ForcedConvergence:
    agent_code: str
    reason: str
```

CLI affichera un badge orange/rouge pour observabilité.

## Tests

`tests/test_loop_detection.py` :

1. **`test_fingerprint_case_insensitive`** : appels successifs `wikipedia_search(query="MediaWiki API")` puis `query="mediawiki api"` → 2ᵉ bloqué.
2. **`test_fingerprint_default_value`** : `wikipedia_search(query="X")` puis `wikipedia_search(query="X", results=5)` (5 = default) → 2ᵉ bloqué.
3. **`test_fingerprint_whitespace`** : `query="hello world"` puis `query="hello  world"` → 2ᵉ bloqué.
4. **`test_consecutive_duplicates_triggers_convergence`** : MockClient qui rend 3× le même call → après 3 blocages, `ForcedConvergence` yieldé et requête en `status="loop_detected"`.
5. **`test_non_duplicate_resets_counter`** : 2 dupes, 1 call différent, 2 dupes → pas de forced convergence (compteur reset).

## Aucune modification BDD

## Critères d'acceptation

- Sur le replay synthétique de Phase 7 (10× `wikipedia_search('MediaWiki API')`), l'agent est stoppé après le 3ᵉ blocage.
- Sur le replay synthétique de Phase 5, les variations `arXiv API` / `arXiv api` / `arxiv API` sont bien collapsées (1 seul fingerprint).
- Aucun outil n'a besoin d'être modifié.

# 01 — Wall-clock timeouts (LLM + requête + tour)

**Référence audit** : §2.1, §3.7 — hang de 6h42 sans aucune erreur ni timeout, VRAM bloquée.

## Problème

`OllamaClient.chat()` ([src/jeanmichel/llm.py](../../src/jeanmichel/llm.py)) appelle le client Ollama avec `stream=False` et **aucun timeout**. Si le serveur Ollama hangue (charge VRAM, prompt long, bug réseau), le process Python attend indéfiniment.

`Orchestrator._run_request` ([src/jeanmichel/orchestrator.py](../../src/jeanmichel/orchestrator.py)) borne le nombre d'itérations (`MAX_STEPS_PER_REQUEST=15`) mais **pas le temps wall-clock**.

Cause directe du gap observé entre `042639` et `110843` (6h42 sans le moindre artefact).

## Solution

Ajouter trois plafonds wall-clock, configurables via env var :

| Constante | Défaut | Env var | Portée |
|-----------|--------|---------|--------|
| `LLM_CALL_TIMEOUT_SECONDS` | 120 | `JEANMICHEL_LLM_TIMEOUT` | Un appel LLM (Ollama) |
| `REQUEST_WALL_CLOCK_SECONDS` | 900 | `JEANMICHEL_REQUEST_TIMEOUT` | Une `_run_request` complète (toutes itérations) |
| `TURN_WALL_CLOCK_SECONDS` | 1800 | `JEANMICHEL_TURN_TIMEOUT` | Un tour humain complet (toute la chaîne de délégations) |

## Modifications

### A. `src/jeanmichel/config.py`

Ajouter après `MAX_STEPS_PER_REQUEST` :

```python
def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default

LLM_CALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_LLM_TIMEOUT", 120)
REQUEST_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_REQUEST_TIMEOUT", 900)
TURN_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_TURN_TIMEOUT", 1800)
```

### B. `src/jeanmichel/llm.py` — timeout par appel

Le client Python officiel `ollama` accepte un `timeout` côté `httpx`. La voie la plus fiable est de l'imposer au `httpx.Client` interne — ou, plus simple, d'exécuter `self._client.chat()` dans un `concurrent.futures.ThreadPoolExecutor` avec `.result(timeout=LLM_CALL_TIMEOUT_SECONDS)`.

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from .config import LLM_CALL_TIMEOUT_SECONDS

class LLMTimeoutError(RuntimeError):
    """Raised when an Ollama chat() call exceeds LLM_CALL_TIMEOUT_SECONDS."""

class OllamaClient(LLMClient):
    def __init__(self, ...):
        ...
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ollama-call")

    def chat(self, ...):
        future = self._executor.submit(self._client.chat, **kwargs)
        try:
            raw = future.result(timeout=LLM_CALL_TIMEOUT_SECONDS)
        except FutureTimeout:
            future.cancel()  # best-effort
            raise LLMTimeoutError(
                f"Ollama chat() exceeded {LLM_CALL_TIMEOUT_SECONDS}s. "
                "Model may be hung or VRAM saturated."
            )
        # ...rest unchanged
```

### C. `src/jeanmichel/orchestrator.py` — wall-clock par requête + par tour

Dans `_run_request`, capter le `start_ts = time.monotonic()` au début ; avant chaque itération du `while llm_steps < MAX_STEPS_PER_REQUEST:`, vérifier :

```python
if time.monotonic() - start_ts > REQUEST_WALL_CLOCK_SECONDS:
    return self._fail_request(req_id, agent_code, "request_wall_clock_exceeded",
        f"Request exceeded {REQUEST_WALL_CLOCK_SECONDS}s wall-clock.")
```

Dans `run(user_input)`, capter `turn_start_ts` et propager via attribut d'instance (`self._turn_started_at`) que `_run_request` consulte aussi :

```python
if time.monotonic() - self._turn_started_at > TURN_WALL_CLOCK_SECONDS:
    return self._fail_request(req_id, agent_code, "turn_wall_clock_exceeded",
        f"Turn exceeded {TURN_WALL_CLOCK_SECONDS}s total wall-clock.")
```

Capturer `LLMTimeoutError` autour de `self.llm.chat(...)` et le convertir en `tool_responses` injecté à l'agent **uniquement si il reste du budget wall-clock** — sinon échouer la requête.

### D. Nouvel événement émis

```python
@dataclass
class WallClockExceeded:
    scope: str       # "llm_call" | "request" | "turn"
    agent_code: str
    elapsed_seconds: float
```

À yielder avant `OrchestrationFailed`. Le CLI affichera un message clair plutôt qu'un crash silencieux.

### E. CLI

`src/jeanmichel/cli.py` : ajouter une branche pour `WallClockExceeded` (Rich panel rouge).

## Tests

`tests/test_wall_clock.py` :

1. **`test_llm_call_timeout`** : `MockClient` qui `time.sleep(3)` ; régler `JEANMICHEL_LLM_TIMEOUT=1` ; attendre `LLMTimeoutError` propagé sous forme d'event `WallClockExceeded(scope="llm_call")`.
2. **`test_request_wall_clock`** : `MockClient` qui rend 20 réponses lentes (50 ms chacune) ; `JEANMICHEL_REQUEST_TIMEOUT=1` ; attendre fail.
3. **`test_turn_wall_clock`** : enchaînement de délégations qui dépasse `TURN_WALL_CLOCK_SECONDS` ; vérifier fail propre.

## Aucune modification BDD

Ce doc n'introduit pas de paradigme. Rien à mirror dans `schema.sql`.

## Critères d'acceptation

- Lancer une conversation avec `JEANMICHEL_LLM_TIMEOUT=2` puis un prompt qui ferait hanguer un vieux modèle → la CLI affiche `Wall-clock exceeded (llm_call) — 2.0s`, la conversation se termine proprement.
- Aucun thread Python ne reste suspendu après timeout (vérifier via `threading.enumerate()`).
- `ruff check` et `pytest` passent.

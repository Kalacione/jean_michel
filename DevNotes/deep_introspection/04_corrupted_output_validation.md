# 04 — Validation des sorties LLM corrompues

**Référence audit** : §2.1 (sortie post-hang), §3.10 — artefact `110843187_web-search-specialist_response.md` contient un `<thought ...` littéral.

## Problème

Quand le modèle (Gemma via Ollama) hangue puis reprend, il peut renvoyer une réponse mal parsée contenant des marqueurs de tokenisation bruts (`<thought`, `<|...|>`, `<start_of_turn>`, `</s>`, etc.) au lieu d'une synthèse propre. Rien dans la chaîne actuelle (ni `OllamaClient.chat()`, ni l'orchestrateur, ni le CLI) ne détecte ce cas — l'artefact `response` est écrit tel quel, le `return_to_user` propage le déchet à l'humain.

## Solution — Q7 retry (1×) puis escalate

### A. Détection — `src/jeanmichel/llm.py`

Centraliser une fonction `_looks_corrupted(text: str) -> bool` :

```python
_CORRUPTION_MARKERS = (
    "<thought",
    "</thought",
    "<|",
    "|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "</s>",
    "[/INST]",
    "<tool_call>",  # raw tag emitted by parser failures
)

def _looks_corrupted(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _CORRUPTION_MARKERS)
```

### B. Retry transparent côté `OllamaClient.chat()`

Après la conversion en `LLMResponse`, si `_looks_corrupted(response.content)` ou `_looks_corrupted(response.thinking)` → 1 seul retry avec le même payload. Si la 2ᵉ tentative est encore corrompue, marquer `LLMResponse.corrupted = True` et propager.

```python
@dataclass
class LLMResponse:
    content: str
    thinking: str | None
    tool_calls: list[ToolCall]
    corrupted: bool = False  # NEW

class OllamaClient(LLMClient):
    def chat(self, ...) -> LLMResponse:
        for attempt in (1, 2):
            raw = ...  # existing call (with timeout from doc 01)
            resp = self._to_llm_response(raw)
            if not (_looks_corrupted(resp.content) or _looks_corrupted(resp.thinking or "")):
                return resp
            if attempt == 1:
                _log.warning("LLM output looks corrupted, retrying once")
        resp.corrupted = True
        return resp
```

### C. Escalation côté orchestrateur

Dans `_run_request`, immédiatement après `response: LLMResponse = self.llm.chat(...)` :

```python
if response.corrupted:
    error_payload = json.dumps({
        "status": "llm_output_corrupted",
        "agent": agent_code,
        "error": (
            "LLM produced corrupted output (contains tokenisation markers) "
            "twice in a row. Likely cause: model hung or context truncated."
        ),
    })
    artifact = self._write_artifact(req_id, agent_code, "response", error_payload)
    with db.connect() as conn:
        db.update_request_status(conn, req_id, "failed", completed=True)
    yield CorruptedOutputDetected(agent_code=agent_code)
    return error_payload, artifact, False
```

### D. Validation aussi du payload `return_to_user`

Quand l'agent appelle `return_to_user(answer=...)`, vérifier `_looks_corrupted(answer)`. Si oui, ne pas terminer la requête : injecter une erreur dans `tool_responses` pour que l'agent re-tente :

```python
if call.name == "return_to_user":
    answer = (call.arguments.get("answer") or "").strip()
    if _looks_corrupted(answer):
        tool_responses.append(json.dumps({
            "tool": "return_to_user",
            "error": (
                "Your answer contains tokenisation markers (e.g. '<thought'). "
                "Rewrite a clean final answer without any XML-like markers."
            ),
        }))
        continue
    # ...existing handling
```

(Et de même pour `signal_convergence(synthesis=...)`.)

### E. Nouvel événement + CLI

```python
@dataclass
class CorruptedOutputDetected:
    agent_code: str
```

Le CLI affichera un panneau d'avertissement explicite plutôt que de laisser le déchet remonter à l'utilisateur.

## Tests

`tests/test_corrupted_output.py` :

1. **`test_ollama_retry_on_corrupted`** : `MockClient` script = `[corrupted_response, clean_response]` → `chat()` retourne la clean, `LLMResponse.corrupted=False`.
2. **`test_ollama_two_corrupted_in_a_row`** : script = `[corrupted, corrupted]` → retourne avec `corrupted=True`.
3. **`test_orchestrator_escalates_corrupted`** : MockClient corrompu 2×, orchestrateur émet `CorruptedOutputDetected`, request → `failed`.
4. **`test_return_to_user_with_marker_rejected`** : agent retourne `return_to_user(answer="<thought stuff")` → injection erreur, l'agent re-tente.

## Aucune modification BDD

## Critères d'acceptation

- Aucun artefact `response` ne peut contenir `<thought` / `<|...|>` / `</s>` etc. (vérifié par grep sur les conversations futures).
- En cas de corruption 2× → request `failed` et message clair à l'humain.

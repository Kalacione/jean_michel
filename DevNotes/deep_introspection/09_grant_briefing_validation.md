# 09 — Validation des grants vs briefings

**Référence audit** : §2.9, §3.5 — jean-michel envoie des briefings `expected=markdown file` à des agents sans grant d'écriture. Aucun fichier workspace n'est produit malgré ~75 appels d'outils de recherche.

## Problème

Avant le doc 06, `web-search-specialist` et `wikipedia-specialist` **avaient** déjà le grant `workspace_create_file` (vérifié en DB). Le problème n'était donc pas l'absence de grant mais :

1. Le briefing demande un livrable que rien ne **force** : "expected=markdown file" est texte libre ignoré par l'orchestrateur.
2. Aucune **validation post-délégation** : si le specialist retourne sans avoir écrit dans le workspace, jean-michel ne le détecte pas.
3. Le specialist retourne `step_budget_exhausted` (échec implicite) sans message structuré → jean-michel ne comprend pas qu'il faut récupérer ou faire fail-fast.

## Solution

### A. Spec structurée pour le champ `expected` (côté `delegate_to`)

Évoluer le schéma JSON de `delegate_to.expected` d'une string libre vers un objet structuré :

```python
# prompts.py
_DELEGATE_TO["function"]["parameters"]["properties"]["expected"] = {
    "type": "object",
    "description": "Structured contract for what the child agent must produce.",
    "properties": {
        "completion_verb": {
            "type": "string",
            "enum": ["gather_done", "critic_done", "build_done",
                     "return_to_user", "signal_convergence"],
        },
        "workspace_artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Workspace paths the child MUST create (relative to workspace root).",
        },
        "summary_format": {"type": "string",
                           "description": "Expected structure of the summary in the completion verb."},
    },
    "required": ["completion_verb"],
}
```

Backward compat : si jean-michel passe `expected` en string, l'orchestrateur le convertit en `{"completion_verb": "return_to_user", "summary_format": <the string>}` avec un warning.

### B. Validation post-délégation côté orchestrateur

Quand l'enfant termine, **avant** d'injecter le payload dans `tool_responses` du parent :

```python
def _validate_child_completion(expected: dict, child_result: dict,
                               conv_folder: Path) -> tuple[bool, str | None]:
    expected_verb = expected.get("completion_verb")
    actual_verb = child_result.get("phase") or "return_to_user"  # phase verbs use 'phase' key
    if expected_verb and actual_verb + "_done" != expected_verb and actual_verb != expected_verb:
        return False, (f"expected completion via {expected_verb}, got {actual_verb}")
    # Check artifacts
    required = expected.get("workspace_artifacts", [])
    ws_root = conv_folder / "workspace"
    missing = [p for p in required if not (ws_root / p).exists()]
    if missing:
        return False, f"missing required workspace artifacts: {missing}"
    return True, None

# In the delegate_to branch, after child returns:
ok, error = _validate_child_completion(expected_dict, child_result_dict, self.conv_folder)
if not ok:
    response_obj["validation_error"] = error
```

Le parent (jean-michel) reçoit alors :

```json
{"tool": "delegate_to", "agent": "web-search-specialist",
 "phase": "gather", "summary": "...",
 "validation_error": "missing required workspace artifacts: ['gather/sources.md']"}
```

Et peut décider (a) de re-déléguer avec un briefing corrigé, (b) de demander à l'humain via `ask_human`, ou (c) d'abandonner avec un message clair.

### C. Garde-fou en sortie d'enfant — l'enfant ne peut pas `gather_done` sans artifact

Quand un specialist appelle `gather_done(summary=..., artifacts=[...])` (cf. doc 05), l'orchestrateur **vérifie** que chaque `artifacts[i]` existe dans le workspace. Si non, rejet avec message :

```python
if call.name == "gather_done":
    artifacts = call.arguments.get("artifacts") or []
    ws_root = self.conv_folder / "workspace"
    missing = [a for a in artifacts if not (ws_root / a).exists()]
    if missing:
        tool_responses.append(json.dumps({
            "tool": "gather_done",
            "error": f"You declared artifacts {missing} but they do not exist in the workspace. "
                     f"Call workspace_create_file (or plan_update) to write them before signalling gather_done.",
        }))
        continue
    if not artifacts:
        # Soft warning, not blocking (some gather phases legitimately produce 0 files)
        # But: refuse if the parent's expected contract required artifacts.
        ...
```

### D. Paradigme — `orchestrator_inquiry_loop` étendu

Migration (suite 044 ou 045) :

```sql
UPDATE paradigms
SET content = '- Before each delegation, make explicit in your thought channel: (1) what exact question this agent is answering, (2) what a satisfactory response looks like, (3) which workspace files MUST exist after the agent returns.
- The `expected` parameter of delegate_to is now structured. Always provide:
    completion_verb: which phase verb the child should complete with (gather_done, critic_done, build_done, return_to_user)
    workspace_artifacts: array of workspace paths the child MUST produce (e.g. ["gather/wikipedia_pubmed.md"])
    summary_format: brief description of what the summary should contain
- After a delegation returns, check the result for `validation_error`. If present, the child did not meet the contract. Either re-delegate with a clearer briefing, or escalate to ask_human.',
    modified_at = datetime('now')
WHERE code = 'orchestrator_inquiry_loop';
```

Mirror schema.sql.

## Tests

`tests/test_grant_briefing_validation.py` :

1. **`test_gather_done_without_artifact_rejected`** : specialist appelle `gather_done(artifacts=["nope.md"])` sans avoir écrit → erreur, request continue.
2. **`test_delegation_validation_error_propagated`** : jean-michel délègue avec `expected.workspace_artifacts=["x.md"]` ; child termine sans écrire → parent reçoit `validation_error`.
3. **`test_legacy_string_expected_accepted`** : jean-michel passe `expected="markdown file"` (string) → converti, warning loggué.
4. **`test_correct_artifact_passes`** : child écrit le fichier + appelle `gather_done(artifacts=["x.md"])` → validation OK.

## Critères d'acceptation

- Un specialist ne peut **pas** signaler la complétion d'une phase sans produire les artifacts qu'il déclare.
- Jean-michel reçoit un `validation_error` lisible en cas de manquement → décision explicite (retry / ask_human / abandonner).
- Aucun cas comme l'audit (75 tool calls, 0 fichier produit, requête déclarée "complète") n'est plus possible.

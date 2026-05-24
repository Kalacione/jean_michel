# 05 — Suppression de l'agent `planner` + nouveaux verbes de contrôle

**Référence audit** : §2.2, §2.5, §2.7, §3.8 — 5 invocations planner, planner→`return_to_user` au lieu de signaler, plan jamais enrichi.
**Décisions** : Q1 (verbes nommés `planner_done` / `gather_done` / `critic_done` / `build_done`) + Q3 (planner = outil mécanique, plus d'agent).

## Problème

Sur 5 invocations du planner LLM :
- 2 ont produit une écriture (Phase 1 OK, Phase 6 `str_replace` OK)
- 2 ont retourné le plan en texte inline sans tool_call (Phases 3a/3b)
- 1 a écrit au mauvais chemin nested (Phase 6 bis)

Aucune invocation n'a jamais **enrichi** le plan avec les findings des recherches en aval. Le planner LLM est : (a) coûteux (1 LLM round-trip par invocation), (b) non-déterministe sur une tâche structurellement déterministe (patch d'un fichier markdown), (c) source de boucles (jean-michel le redélègue 5× par manque de feedback orchestrateur).

**Décision** : supprimer l'agent `planner`, remplacer par un outil Python mécanique `plan_update` ([doc 06](06_plan_update_tool.md)). Le contrat devient déterministe.

Cette suppression nécessite aussi de répondre au constat §2.7 : il faut des **verbes de contrôle nommés** qui permettent à l'orchestrateur de router et d'incrémenter le statut de la conversation. Décision Q1 : 4 verbes nommés.

## Solution — Partie A : suppression de l'agent

### A.1 Migration `db/migrate_044_remove_planner_agent.sql`

```sql
-- MIGRATION 044 — remove `planner` agent (replaced by mechanical plan_update tool)
-- ====================================================================
-- The planner LLM agent was non-deterministic on a deterministic task
-- (patching a markdown file). Replace with the plan_update tool (see doc 06).
-- Also removes the 'planner' role from the agents.role CHECK constraint.

-- 1. Remove paradigm bindings, tool grants, workspace grants for planner
DELETE FROM agent_paradigms     WHERE agent_id = (SELECT id FROM agents WHERE code='planner');
DELETE FROM agent_tools         WHERE agent_id = (SELECT id FROM agents WHERE code='planner');
DELETE FROM agent_workspace_grants WHERE agent_id = (SELECT id FROM agents WHERE code='planner');

-- 2. Deactivate the agent (keep row for historical FK in requests/artifacts)
UPDATE agents SET active = 0, modified_at = datetime('now') WHERE code = 'planner';

-- 3. Update jean-michel paradigms that reference the planner agent
UPDATE paradigms
SET content = replace(
    content,
    'delegate to planner first',
    'call plan_update first'
), modified_at = datetime('now')
WHERE content LIKE '%delegate to planner%' OR content LIKE '%delegate to dispatcher%';

UPDATE paradigms
SET content = replace(content, 'the planner will produce', 'plan_update will produce'),
    modified_at = datetime('now')
WHERE content LIKE '%planner will produce%';

-- 4. Remove planner-specific paradigms (planner_plan_format, plan_not_execute)
UPDATE paradigms SET active = 0, modified_at = datetime('now')
WHERE code IN ('planner_plan_format', 'plan_not_execute');

-- 5. Drop the 'planner' role from the schema constraint.
-- SQLite does not support ALTER TABLE … MODIFY COLUMN, so this is a no-op
-- at migration level. The schema.sql mirror MUST drop 'planner' from the
-- CHECK constraint. Existing rows with role='planner' remain (inactive).
```

### A.2 Mirror dans `db/schema.sql`

- Ligne 74 : `role TEXT NOT NULL CHECK (role IN ('router','specialist','finalizer','planner')),` → enlever `'planner'` → `CHECK (role IN ('router','specialist','finalizer'))`.
- Sections d'inserts/updates relatives au planner (lignes ~1896 (insert dispatcher), 1908 (planner_plan_format), 1991 (plan maintenance), 2008/2117/2119 etc.) : retirer les `INSERT`/`UPDATE` qui visent `code='planner'` ou `code='planner_plan_format'`, et ajuster les paradigmes globaux qui mentionnent "delegate to planner" pour qu'ils disent "call plan_update".
- Ajouter en fin de fichier le bloc complet de la migration 044 (en commentaire `-- MIGRATION 044 …`) à des fins de traçabilité, comme les autres.

### A.3 Code Python

- `src/jeanmichel/prompts.py` :
  - Retirer la clé `"planner"` de `_CONTROL_TOOLS_BY_ROLE`.
  - Retirer la branche `if role == "planner":` dans `_render_output_contract`.
- `src/jeanmichel/orchestrator.py` : aucun changement direct (le rôle est lu de la DB ; comme l'agent est `active=0`, il n'apparaîtra plus dans `available_agents`).

## Solution — Partie B : nouveaux verbes de contrôle

### B.1 4 nouveaux verbes

Tous déclarés dans `src/jeanmichel/prompts.py` au même endroit que `_RETURN_TO_USER` / `_SIGNAL_CONVERGENCE`. Tous sans paramètre exécutoire — uniquement un `summary` textuel obligatoire et un `next_hint` optionnel (champ libre pour le routeur).

| Verbe | Rôle autorisé | Sémantique |
|-------|---------------|-----------|
| `planner_done` | router (jean-michel) | "Le plan est à jour, je suis prêt à entrer en phase GATHER" |
| `gather_done` | specialist (web-search, wikipedia) | "J'ai collecté assez d'éléments, livre dans le workspace est faite" |
| `critic_done` | specialist (critical-thinker) | "Critique terminée, biais/gaps identifiés" |
| `build_done` | specialist (document-builder) | "Document final écrit dans le workspace" |

Schéma JSON commun :

```python
_PHASE_VERB = lambda name, desc: {
    "type": "function",
    "function": {
        "name": name,
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string",
                            "description": "One-paragraph summary of what was achieved in this phase."},
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Workspace paths produced during this phase (relative to workspace root).",
                },
                "next_hint": {
                    "type": "string",
                    "description": "Optional hint for the orchestrator about what should logically happen next.",
                },
            },
            "required": ["summary"],
        },
    },
}
```

### B.2 Mapping rôle → verbes autorisés

Étendre `_CONTROL_TOOLS_BY_ROLE` :

```python
_CONTROL_TOOLS_BY_ROLE = {
    "router":     [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER, _PLANNER_DONE],
    "specialist": [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER, _SIGNAL_CONVERGENCE,
                   _GATHER_DONE, _CRITIC_DONE, _BUILD_DONE],
    "finalizer":  [_RETURN_TO_USER],
}
```

**Restriction fine** : un specialist ne devrait pas voir les 3 verbes phase mais seulement celui correspondant à son agent. C'est trop intrusif à coder via le rôle seul → on l'exprime dans le **paradigme par agent** : `web-search-specialist` reçoit dans son output contract : *"Use `gather_done` to signal completion ; you may NOT call `critic_done` or `build_done`."*. Si le LLM le fait quand même, l'orchestrateur rejette le verbe avec une erreur :

```python
_PHASE_VERB_OWNER = {
    "planner_done": {"jean-michel"},
    "gather_done": {"web-search-specialist", "wikipedia-specialist"},
    "critic_done": {"critical-thinker"},
    "build_done": {"document-builder"},
}

if call.name in _PHASE_VERB_OWNER:
    if agent_code not in _PHASE_VERB_OWNER[call.name]:
        tool_responses.append(json.dumps({
            "tool": call.name,
            "error": f"'{call.name}' is not available to agent '{agent_code}'. "
                     f"It belongs to: {sorted(_PHASE_VERB_OWNER[call.name])}.",
        }))
        continue
```

### B.3 Effet orchestrateur — incrémentation du `conv_status`

Quand un verbe phase est reçu, l'orchestrateur :
1. Termine la requête en `status="completed"` (comme `return_to_user`).
2. Persiste l'événement dans la table `conversations` via une nouvelle colonne `phase` (ou réutilise `conv_status` si déjà observable — cf. migration 042). À déterminer en lisant `migrate_042_conv_status_observable_triggers.sql`.
3. Émet un événement `PhaseCompleted(agent_code, phase, summary, artifacts, next_hint)` que jean-michel (le parent) consomme dans `tool_responses` :

```json
{"tool": "delegate_to", "agent": "web-search-specialist",
 "phase": "gather", "summary": "...", "artifacts": ["gather/sources.md"],
 "next_hint": "ready for critic review"}
```

Code dans `_run_request`, à côté des branches `return_to_user` / `signal_convergence` :

```python
if call.name in _PHASE_VERB_OWNER:
    summary = (call.arguments.get("summary") or "").strip()
    artifacts = call.arguments.get("artifacts") or []
    next_hint = call.arguments.get("next_hint", "")
    phase = call.name.replace("_done", "")  # 'gather', 'critic', 'build', 'planner'
    payload = json.dumps({
        "phase": phase, "summary": summary,
        "artifacts": artifacts, "next_hint": next_hint,
    })
    artifact = self._write_artifact(req_id, agent_code, "response", payload)
    with db.connect() as conn:
        db.update_request_status(conn, req_id, "completed", completed=True)
        db.record_phase_completion(conn, self.conv_id, phase, agent_code, summary)
    yield PhaseCompleted(agent_code=agent_code, phase=phase,
                         summary=summary, artifacts=list(artifacts),
                         next_hint=next_hint)
    return payload, artifact, False
```

### B.4 Nouvelle table SQL — `conversation_phases`

```sql
-- (Same migration 044)
CREATE TABLE conversation_phases (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  phase           TEXT NOT NULL CHECK (phase IN ('planner','gather','critic','build')),
  agent_code      TEXT NOT NULL,
  summary         TEXT NOT NULL,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversation_phases_conv ON conversation_phases(conversation_id);
```

Mirror dans `schema.sql` après les autres tables de conversation.

`src/jeanmichel/db.py` : ajouter `record_phase_completion(conn, conv_id, phase, agent_code, summary)`.

## Tests

`tests/test_phase_verbs.py` :

1. **`test_planner_agent_removed`** : `db.get_agent_by_code(conn, 'planner')` retourne `active=0` ; `db.list_active_agents` ne le contient pas.
2. **`test_gather_done_records_phase`** : MockClient pour web-search-specialist qui appelle `gather_done(summary="ok")` → request `completed`, ligne dans `conversation_phases`.
3. **`test_wrong_owner_rejected`** : web-search-specialist appelle `critic_done(...)` → erreur injectée, request continue.
4. **`test_jean_michel_receives_phase_in_delegate_result`** : MockClient parent voit dans tool_responses le payload `{"phase": "gather", ...}`.

## Critères d'acceptation

- `sqlite3 jeanmichel.db "SELECT active FROM agents WHERE code='planner'"` → 0.
- Aucun appel `delegate_to(agent_code='planner')` ne fonctionne plus (erreur "unknown agent").
- Un `gather_done` produit une ligne en DB et un événement CLI.
- Une regression suite : aucun test existant ne casse (les tests qui utilisaient planner doivent être migrés vers `plan_update`).

# 08 — Promotion à depth ≥ 2 (empowerment hybride)

**Référence audit** : §2.6, §3.6 — depth jamais > 1, `signal_convergence` jamais offert, sous-recherches impossibles.
**Décision** : Q2 — hybride. Jean-michel orchestre par défaut (pipeline du doc 07). Mais :
- `critical-thinker` peut creuser un point précis en déléguant à un research specialist.
- `web-search-specialist` et `wikipedia-specialist` peuvent lancer des **sous-recherches** (cas page d'homonymie, lien à suivre).

## Problème

Aujourd'hui :
- `delegate_to` est offert à tous les rôles `router` et `specialist` côté `_CONTROL_TOOLS_BY_ROLE`.
- En pratique, **aucun specialist n'a un grant `delegate_to`** (vérifiable via `agent_tools` — seul jean-michel l'utilise).
- Le paradigme `convergence_gate` n'autorise `signal_convergence` qu'à `depth ≥ 2`, mais aucun specialist n'y arrive jamais → `signal_convergence` est code mort.

L'audit montre que `wikipedia-specialist` a tenté `delegate_to(planner)` à 041709 — preuve que le LLM veut déléguer mais n'a pas la cible appropriée.

## Solution — deux mécanismes distincts

### Mécanisme A : sous-recherches au sein du **même** outil/agent (depth=1, pas de récursion)

Pour `web-search-specialist` et `wikipedia-specialist`, autoriser la stratégie suivante dans le prompt :

> Si une page Wikipedia est une **page d'homonymie**, ou si une URL trouvée mérite d'être suivie pour clarifier l'intention de l'utilisateur :
> 1. Continuer dans la même requête (pas de delegate_to), en faisant un nouvel appel `wikipedia_get_page` ou `web_search` avec un terme plus précis.
> 2. Cataloguer les substeps via `plan_update(action="add_substep", parent_step_id=..., title=..., reason=...)`.
> 3. Conclure avec `gather_done` une fois les substeps résolus.

**Pas de changement de code ni de grant** — c'est purement un changement de paradigme. Migration :

```sql
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT
  (SELECT id FROM categories WHERE code='process'),
  'subresearch_inline', 'Sub-research within a single delegation',
  '- When a result reveals a disambiguation (Wikipedia disambiguation page, multiple homonyms, ambiguous link), DO NOT abort or escalate. Pick the most relevant candidate(s) and continue the search inline within the same request.
- When following a sub-research path, call plan_update(action="add_substep", parent_step_id=..., title=..., reason="why this branch") BEFORE the new tool calls. This makes the depth-of-research visible in plan.md.
- Limit: at most 3 substeps per delegation. Beyond, signal completion with gather_done and let the orchestrator route via a fresh delegation.',
  'Avoid coupling the depth of investigation to the recursion depth of agents.',
  0, 100, 1, datetime('now'), datetime('now');

INSERT INTO agent_paradigms (agent_id, paradigm_id, mode, created_at)
SELECT a.id, (SELECT id FROM paradigms WHERE code='subresearch_inline'), NULL, datetime('now')
FROM agents a WHERE a.code IN ('web-search-specialist', 'wikipedia-specialist');
```

Mirror schema.sql.

### Mécanisme B : `critical-thinker` peut creuser via délégation (depth = 2 ciblé)

`critical-thinker` est invoqué par jean-michel après GATHER (depth=1). S'il identifie un gap nécessitant une vérification factuelle, il peut déléguer à `web-search-specialist` ou `wikipedia-specialist` (depth=2). À cette profondeur, `signal_convergence` devient pertinent et est offert.

#### Grants DB

```sql
-- critical-thinker reçoit delegate_to (n'avait probablement pas le grant — vérifier)
-- delegate_to n'est pas un tool listé dans agent_tools — c'est un control verb géré par le rôle.
-- critical-thinker est déjà role='specialist', donc il a delegate_to via _CONTROL_TOOLS_BY_ROLE.
-- Aucune migration nécessaire pour ce point.
```

#### Restriction des cibles

Pour éviter qu'un specialist délègue n'importe où (cf. l'abus observé `wikipedia-specialist` → `delegate_to(planner)`), introduire une **whitelist** par agent dans une nouvelle table :

```sql
CREATE TABLE agent_delegation_targets (
  agent_id  INTEGER NOT NULL REFERENCES agents(id),
  target_code TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (agent_id, target_code)
);

INSERT INTO agent_delegation_targets (agent_id, target_code) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'),      'web-search-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'wikipedia-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'critical-thinker'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'document-builder'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'workspace-manager'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'comparator-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'code-runner'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'meta-analyst'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'weather-specialist'),
  ((SELECT id FROM agents WHERE code='jean-michel'),      'summarizer'),
  ((SELECT id FROM agents WHERE code='critical-thinker'), 'web-search-specialist'),
  ((SELECT id FROM agents WHERE code='critical-thinker'), 'wikipedia-specialist');
-- All other agents: no delegate_to targets — they cannot delegate at all.
```

Mirror schema.sql.

#### Code orchestrateur

`src/jeanmichel/db.py` : ajouter `load_delegation_targets(conn, agent_id) -> set[str]`.

`src/jeanmichel/orchestrator.py` dans la branche `delegate_to` :

```python
# After existing 'archivist' check, before recursion-depth check:
allowed = db.load_delegation_targets_for(conn, agent.id)  # cached at request start
if child_code not in allowed:
    tool_responses.append(json.dumps({
        "tool": "delegate_to",
        "error": (
            f"You ({agent_code}) cannot delegate to {child_code!r}. "
            f"Allowed targets: {sorted(allowed) or '[none]'}. "
            f"If you have completed your work, use gather_done/critic_done/build_done "
            f"or signal_convergence instead."
        ),
    }))
    continue
```

#### Promotion `signal_convergence`

Le paradigme `convergence_gate` actuel limite `signal_convergence` à `depth ≥ 2`. Avec ce mécanisme, depth=2 devient atteignable (critical-thinker → web-search-specialist). Pas de changement nécessaire de ce paradigme. Vérifier qu'il est bien actif.

### Mécanisme C : interdire les abus

- `wikipedia-specialist` qui tente `delegate_to(...)` → rejeté par la whitelist (aucune ligne dans `agent_delegation_targets`).
- `web-search-specialist` idem.
- Le LLM reçoit un message clair lui indiquant d'utiliser `gather_done` à la place.

## Tests

`tests/test_depth_promotion.py` :

1. **`test_specialist_cannot_delegate_to_planner`** : MockClient pour wikipedia-specialist appelle `delegate_to(planner)` → erreur whitelist + planner inactif.
2. **`test_critic_can_delegate_to_websearch`** : critical-thinker (depth=1 sous jean-michel) → `delegate_to(web-search-specialist)` → depth=2 réussi.
3. **`test_signal_convergence_offered_at_depth_2`** : web-search-specialist à depth=2 reçoit `signal_convergence` dans ses tool_payloads.
4. **`test_wikipedia_disambiguation_inline_substep`** : MockClient simule disambiguation → l'agent appelle `plan_update(add_substep)` puis un 2ᵉ `wikipedia_get_page`, **sans** delegate_to.

## Critères d'acceptation

- `critical-thinker` peut atteindre depth=2 dans un test ; aucun autre specialist ne le peut.
- L'abus `wikipedia-specialist → delegate_to(...)` est rejeté avec message explicite.
- `signal_convergence` reste offert uniquement à depth ≥ 2 (paradigme existant inchangé).

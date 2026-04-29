# Notice — Ajouter un spécialiste ou un outil

## A. Nouvel agent spécialiste (sans outil Python)

**1. DB — INSERT agent**
```sql
INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES ('mon-specialist', 'Mon Specialist', 'specialist', '<mission>', 1, 0.2, 1, datetime('now'), datetime('now'));
```
`role` ∈ `router | specialist | finalizer`. Ne pas créer de second routeur.

**2. DB — Paradigmes (si nécessaire)**

Si les paradigmes globaux suffisent, passer directement au point 4.

Nouvelle catégorie :
```sql
INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at)
VALUES ((SELECT id FROM sections WHERE code='process'), 'mon_domaine', 'Mon Domaine', 50, 1, datetime('now'), datetime('now'));
```

Nouveau paradigme (`is_global=0` pour un paradigme domaine-spécifique) :
```sql
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='mon_domaine'),
  'mon_paradigme', 'Titre court',
  '- Règle 1.\n- Règle 2.',
  'Pourquoi ce paradigme existe (usage interne, jamais injecté dans le prompt).',
  0, 10, 1, datetime('now'), datetime('now')
);
```

**3. DB — Bindings `agent_paradigms`**
```sql
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'mon-specialist'
  AND p.code IN ('mon_paradigme', 'autre_paradigme');
```

**4. DB — Grants `agent_tools` (si l'agent utilise des outils Python)**
```sql
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'nom_de_loutil' FROM agents WHERE code='mon-specialist';
```
Les outils de contrôle (`delegate_to`, `ask_human`, `return_to_user`) sont **toujours disponibles** — ne pas les lister ici.

**5. Miroir schema.sql** — reporter les mêmes INSERTs à la fin de `db/schema.sql`.

---

## B. Nouvel outil Python

**1. Créer `src/jeanmichel/tools/<nom>.py`**

Deux variantes :

```python
# Stateless (pas de dépendance au dossier de conversation)
from ._base import ToolSpec

def _handler(param1: str, param2: int = 5) -> str:
    # Retourner TOUJOURS une string (json.dumps(...) recommandé)
    import json
    return json.dumps({"result": "..."})

SPEC = ToolSpec(
    name="nom_de_loutil",           # nom LLM-facing, snake_case
    description="...",              # vu par le LLM, doit guider l'usage
    parameters={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "description": "..."},
        },
        "required": ["param1"],
    },
    handler=_handler,
)
```

```python
# Context-bound (accède au dossier de conversation)
from pathlib import Path
from ._base import ToolSpec

def make_spec(conv_folder: Path) -> ToolSpec:
    def _handler(param: str) -> str:
        # conv_folder est capturé par la closure
        ...
    return ToolSpec(name="...", description="...", parameters={...}, handler=_handler)
```

**2. Enregistrer dans `src/jeanmichel/tools/__init__.py`**
```python
from . import mon_module as _mon_module_mod

def build_registry(conv_folder: Path) -> dict[str, ToolSpec]:
    ...
    return {
        ...
        _mon_module_mod.SPEC.name: _mon_module_mod.SPEC,           # stateless
        # ou :
        _mon_module_mod.make_spec(conv_folder).name: _mon_module_mod.make_spec(conv_folder),  # context-bound
    }
```

**3. Granter l'outil aux agents concernés** (voir section A, point 4).

**4. Miroir schema.sql** — reporter le(s) INSERT `agent_tools`.

---

## Checklist

- [ ] Agent INSERT dans DB + schema.sql
- [ ] Catégorie + paradigme INSERT si domaine nouveau (DB + schema.sql)
- [ ] `agent_paradigms` bindings (DB + schema.sql)
- [ ] `agent_tools` grants si outil Python (DB + schema.sql)
- [ ] Fichier outil créé si nécessaire
- [ ] `build_registry` mis à jour si nouvel outil
- [ ] Paradigme de routing ajouté à jean-michel si le nouvel agent doit être ciblé explicitement

---

## Règles à respecter

- Tool `name` = clé LLM-facing (ex. `conv_read_file`), pas le nom du module Python.
- Les paradigmes globaux (`is_global=1`) s'appliquent à tous les agents — ne pas les rebinder.
- Ne pas hardcoder de grants en Python : toujours passer par `agent_tools`.
- `build_registry` expose TOUS les outils disponibles ; les grants DB filtrent ce que chaque agent voit dans son prompt.
- Un agent sans grant d'outils Python reçoit quand même `delegate_to`, `ask_human`, `return_to_user`.

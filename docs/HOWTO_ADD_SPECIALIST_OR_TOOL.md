# Notice — Ajouter un spécialiste ou un outil

## A. Nouvel agent spécialiste (sans outil Python)

**1. DB — INSERT agent**
```sql
INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES ('mon-specialist', 'Mon Specialist', 'specialist', '<mission>', 1, 0.2, 1, datetime('now'), datetime('now'));
```
`role` ∈ `router | specialist | finalizer`. Ne pas créer de second routeur.

Voir la section "Cas particuliers" en fin de doc pour le choix entre `specialist` et `finalizer`.

**2. DB — Paradigmes (si nécessaire)**

Si les paradigmes globaux suffisent, passer directement au point 4.

Nouvelle catégorie :
```sql
INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at)
VALUES ((SELECT id FROM sections WHERE code='process'), 'mon_domaine', 'Mon Domaine', 50, 1, datetime('now'), datetime('now'));
```

Sections existantes : `communication`, `reasoning`, `critical_thinking`, `process`, `code`, `safety`. Choisir celle qui correspond le mieux. Pour un paradigme de discipline cognitive ou de garde-fou de raisonnement, `critical_thinking` est probablement le bon foyer.

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
Les outils de contrôle (`delegate_to`, `ask_human`, `return_to_user`) sont **toujours disponibles** selon le rôle de l'agent — ne pas les lister ici.

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
- [ ] `paradigm_modes` restrictions si le paradigme ne s'applique pas à tous les modes (DB + schema.sql)
- [ ] `agent_tools` grants si outil Python (DB + schema.sql)
- [ ] Fichier outil créé si nécessaire
- [ ] `build_registry` mis à jour si nouvel outil
- [ ] Paradigme de routing ajouté à jean-michel si le nouvel agent doit être ciblé explicitement

---

## Règles à respecter

- Tool `name` = clé LLM-facing (ex. `conv_read_file`), pas le nom du module Python.
- Les paradigmes globaux (`is_global=1`) s'appliquent à tous les agents — ne pas les rebinder.
- Marquer un paradigme `is_global=1` doit être un choix conscient. Préférer un binding explicite si le paradigme ne sert que 2-3 agents : ça évite la pollution des prompts des autres agents.
- Ne pas hardcoder de grants en Python : toujours passer par `agent_tools`.
- `build_registry` expose TOUS les outils disponibles ; les grants DB filtrent ce que chaque agent voit dans son prompt.
- Un agent reçoit ses outils de contrôle selon son rôle (cf. cas particuliers ci-dessous).

---

## Cas particuliers

### Restreindre un paradigme à certains modes

Un paradigme peut s'appliquer uniquement dans certains modes via la table `paradigm_modes`. **Convention : absence de ligne = applicable à tous les modes.**

```sql
-- Paradigme actif uniquement en mode chat
INSERT INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'chat' FROM paradigms WHERE code = 'mon_paradigme';

-- Paradigme actif en chat ET vocal (deux lignes)
INSERT INTO paradigm_modes (paradigm_id, mode) VALUES
  ((SELECT id FROM paradigms WHERE code='mon_paradigme'), 'chat'),
  ((SELECT id FROM paradigms WHERE code='mon_paradigme'), 'vocal');
```

Cas typiques :
- Un paradigme de relance conversationnelle (« propose 2-3 axes de creusage ») → `chat` seulement.
- Un paradigme de concision agressive (« réponse en moins de 4 phrases ») → `vocal` seulement.
- Un paradigme demandant de la profondeur d'analyse → `analyse` + `chat`, exclu de `vocal`.

### Choix du rôle : `specialist` vs `finalizer`

| Rôle | Reçoit `delegate_to` | Reçoit `ask_human` | Reçoit `return_to_user` | Cas d'usage |
|---|:-:|:-:|:-:|---|
| `router` | ✓ | ✓ | ✓ | Réservé à jean-michel |
| `specialist` | ✓ | ✓ | ✓ | Tout agent métier qui peut déléguer ou demander une clarification |
| `finalizer` | ✗ | ✗ | ✓ | Agent purement mécanique : reçoit des inputs, produit un livrable final |

Le filtrage est appliqué dans `prompts.py:tools_payload_for_agent` selon `agent.role`. Un finalizer ne voit donc que `return_to_user` dans son prompt — il ne peut techniquement pas déléguer ou demander à l'humain, et son `OUTPUT CONTRACT` rendu reflète cette restriction.

Exemples actuels :
- `synthesizer` est un finalizer : il fusionne plusieurs réponses de spécialistes en une réponse cohérente. Pas de raison de déléguer.
- `archivist` est un finalizer : il met à jour le `summary.md` à partir d'inputs fournis par l'orchestrateur.

### Agent invoqué uniquement par l'orchestrateur (pas via delegate_to)

Si un nouvel agent ne doit jamais être appelé par un autre LLM (uniquement par du code orchestrateur, comme l'archivist), deux mécanismes le garantissent :

1. **Liste blanche dans l'orchestrateur** : l'agent doit être ajouté à la liste des codes refusés dans `_run_request` quand reçus via `delegate_to`. Aujourd'hui codé en dur pour `archivist`.
2. **Exclusion de la liste `## Available specialists`** : `prompts.py:render_system_prompt` filtre l'agent de la liste injectée dans le contexte des autres agents.

Ces deux protections sont complémentaires. Modifier `prompts.py` et `orchestrator.py` ensemble pour ajouter un nouvel agent de ce type.

### Accès workspace et sandbox Docker

Un agent peut manipuler des fichiers dans le `workspace/` de sa conversation et exécuter des commandes dans un sandbox Docker isolé. L'accès est entièrement contrôlé par la BDD — rien n'est hardcodé en Python.

**Workspace (lecture seule)** — `workspace_view` et `workspace_list` sont disponibles sans grant spécial, du moment que l'agent les a dans `agent_tools`.

**Workspace (écriture)** — `workspace_create_file` et `workspace_str_replace` requièrent en plus une ligne dans `agent_workspace_grants`.

**Sandbox Docker** — `bash_sandbox` requiert :
- une ligne dans `agent_tools` avec `tool_code='bash_sandbox'`,
- une ligne dans `agent_sandbox_grants` par binaire autorisé (premier mot de la commande vérifié avant tout `docker exec`).

Prérequis système : image Docker buildée (`./jm.sh --build-docker`, une seule fois).

Exemple complet — créer un agent `code-runner` avec workspace write et sandbox Python :

```sql
-- 1. Agent
INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES (
  'code-runner', 'Code Runner', 'specialist',
  'Execute Python code inside the Docker sandbox and return results.',
  1, 0.1, 1, datetime('now'), datetime('now')
);

-- 2. Grants outils
INSERT INTO agent_tools (agent_id, tool_code)
SELECT a.id, t.tool_code
FROM agents a,
     (VALUES ('workspace_create_file'), ('workspace_str_replace'),
             ('workspace_view'), ('workspace_list'), ('bash_sandbox')) AS t(tool_code)
WHERE a.code = 'code-runner';

-- 3. Grant workspace write
INSERT INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'code-runner';

-- 4. Grants sandbox (un binaire par ligne)
INSERT INTO agent_sandbox_grants (agent_id, command) VALUES
  ((SELECT id FROM agents WHERE code='code-runner'), 'python3'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'cat'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'ls'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'jq');
```

Reporter ces INSERTs dans `db/schema.sql` (convention projet : le schema est la source de vérité, pas la BDD live).

### Choisir l'image Docker sandbox

Par défaut, `bash_sandbox` utilise `jeanmichel-sandbox:py-alpine` (Python 3.13 Alpine). Pour un agent qui a besoin d'un runtime différent, renseigner la colonne `sandbox_image` de la table `agents` :

```sql
-- Exemple : agent Node.js utilisant l'image node-alpine
UPDATE agents SET sandbox_image = 'jeanmichel-sandbox:node-alpine' WHERE code = 'mon-agent-node';
```

Images disponibles :
- `jeanmichel-sandbox:py-alpine` — Python 3.13, jq, requests/numpy/tabulate (défaut)
- `jeanmichel-sandbox:node-alpine` — Node 22, TypeScript, ts-node

Build : `./jm.sh --build-docker all` (une seule fois, ou après modification d'un Dockerfile).

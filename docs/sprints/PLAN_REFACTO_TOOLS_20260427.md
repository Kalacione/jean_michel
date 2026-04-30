# Plan — Refactoring outils + grants DB

## Contexte

`tools.py` est un monolithe qui mélange la définition des outils, leur registre,
et l'attribution hardcodée par agent (`AGENT_TOOL_GRANTS`). Ce plan le transforme
en sous-package `tools/`, déplace les grants en DB, et renomme le dossier de debug.

---

## Arborescence cible

```
src/jeanmichel/
└── tools/                  ← ex tools.py (supprimé)
    ├── __init__.py          exporte ToolSpec + build_registry
    ├── _base.py             ToolSpec dataclass
    ├── clock.py             SPEC: ToolSpec
    └── conv_read_file.py    make_spec(conv_folder) → ToolSpec
                             (ex read_file — renommé pour lever l'ambiguité)

debug/                      ← ex tools/ (racine) — renommé
├── inspect_conv.py          (inchangé, sauf path)
└── export_db.py             nouveau

db/schema.sql               + table agent_tools + seeds
jeanmichel.db (live)        migration ALTER + INSERT
```

---

## Modifications fichier par fichier

| # | Fichier | Nature |
|---|---------|--------|
| 1 | `tools/` → `debug/` | renommage dossier racine |
| 2 | `src/jeanmichel/tools/` | création sous-package (4 fichiers) |
| 3 | `src/jeanmichel/tools.py` | suppression |
| 4 | `db/schema.sql` | + table `agent_tools` + seeds |
| 5 | `jeanmichel.db` | migration live |
| 6 | `src/jeanmichel/db.py` | + `load_tool_grants(conn, agent_id)` |
| 7 | `src/jeanmichel/prompts.py` | `tools_payload_for_agent(grants, registry)` — suppression import `AGENT_TOOL_GRANTS` |
| 8 | `src/jeanmichel/orchestrator.py` | fetch grants DB, passe à `tools_payload_for_agent` |
| 9 | `debug/export_db.py` | nouveau script |
| 10 | `README.md` | mise à jour arborescence + debug/ |

---

## Schéma DB — nouvelle table

```sql
CREATE TABLE agent_tools (
  agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  tool_code   TEXT NOT NULL,
  PRIMARY KEY (agent_id, tool_code)
);
CREATE INDEX idx_agent_tools_agent ON agent_tools(agent_id);

-- Seeds
INSERT INTO agent_tools (agent_id, tool_code) VALUES
  ((SELECT id FROM agents WHERE code='jean-michel'), 'clock'),
  ((SELECT id FROM agents WHERE code='jean-michel'), 'conv_read_file'),
  ((SELECT id FROM agents WHERE code='summarizer'),  'conv_read_file');
-- synthesizer : aucun outil natif
```

---

## Convention outils

- Outil **stateless** : expose `SPEC: ToolSpec` au niveau module.
- Outil **context-dependent** (besoin de `conv_folder`) : expose
  `make_spec(conv_folder: Path) -> ToolSpec`.
- `build_registry(conv_folder)` dans `__init__.py` assemble les deux.

---

## Breaking change intentionnel

`read_file` → `conv_read_file` dans le nom d'outil LLM et en DB.
Justification : lever l'ambiguité "lire n'importe quel fichier" vs
"lire un fichier dans le dossier de la conversation courante".

---

## Périmètre exclu (prochain sprint)

- Tests unitaires des outils individuels.
- UI pour gérer les grants en BDD (sqlite_web suffit pour l'instant).

# PLAN — Paradigm Matrix Viewer/Editor

## Objectif

Un outil de visualisation et d'édition des associations paradigme↔agent, accessible via navigateur, sans dépendances nouvelles.

Commande : `./jm.sh --paradigm-matrix` → ouvre `http://localhost:8765`

---

## Architecture générale

```
debug/paradigm_matrix.py
├── SQLiteReader          # requêtes lecture seule (GET /)
├── ApplyHandler          # transaction batch (POST /apply)
├── HTML_TEMPLATE         # string constante — page entière
└── MatrixHTTPServer      # BaseHTTPRequestHandler stdlib
```

**Un seul fichier Python.** HTML/JS entièrement inline dans une constante `HTML_TEMPLATE`. Aucune dépendance externe (stdlib : `http.server`, `json`, `sqlite3`, `urllib.parse`).

---

## Contrat d'interface entre tâches (JSON embarqué)

La page HTML reçoit ses données via un bloc `<script>` injecté dans `HTML_TEMPLATE` au moment du GET :

```js
const INITIAL_DATA = {
  agents: [
    { id: 1, code: "jean-michel", name: "Jean-Michel", role: "router" },
    ...
  ],
  sections: [
    {
      code: "communication", title: "Communication",
      categories: [
        {
          code: "precision", title: "Precision",
          paradigms: [
            {
              id: 3, code: "no_speculation", title: "No speculation",
              is_global: 1, active: 1, order_priority: 10,
              category_id: 1,
              bound_agent_ids: []          // agent_paradigms explicites uniquement
            },
            ...
          ]
        }
      ]
    }
  ],
  categories_flat: [                       // pour le formulaire d'ajout
    { id: 1, key: "communication.precision", section: "communication", code: "precision" },
    ...
  ]
}
```

> **Règle** : `bound_agent_ids` contient uniquement les IDs des bindings explicites (`agent_paradigms`). Le flag `is_global` est séparé. Le JS calcule lui-même si une case est "active" : `is_global == 1 || bound_agent_ids.includes(agent.id)`.

---

## Tâche 1 — Backend Python (`debug/paradigm_matrix.py`)

**Fichier à créer** : `debug/paradigm_matrix.py`

### 1a. Requête GET `/`

Construit `INITIAL_DATA` via SQLite et l'injecte dans `HTML_TEMPLATE` :

```python
def _build_initial_data(db_path: Path) -> dict:
    # Requête principale :
    # sections → categories → paradigms (ORDER BY order_priority)
    # + pour chaque paradigme : SELECT agent_id FROM agent_paradigms WHERE paradigm_id=?
    # + agents actifs
    # + categories_flat pour le formulaire
    ...
```

Substitution dans le template :

```python
html = HTML_TEMPLATE.replace("__INITIAL_DATA__", json.dumps(data))
```

### 1b. Endpoint POST `/apply`

Reçoit un JSON avec le diff complet :

```json
{
  "bindings_add":    [[agent_id, paradigm_id], ...],
  "bindings_remove": [[agent_id, paradigm_id], ...],
  "global_updates":  [[paradigm_id, 0_or_1], ...],
  "active_updates":  [[paradigm_id, 0_or_1], ...],
  "new_paradigms":   [
    {
      "category_id": 2,
      "code": "my_paradigm",
      "title": "My Paradigm",
      "content": "- bullet\n- bullet",
      "is_global": 0,
      "order_priority": 100
    }
  ]
}
```

Logique de transaction (tout ou rien) :

```python
def _apply(db_path: Path, payload: dict) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Nouveaux paradigmes
        for p in payload["new_paradigms"]:
            conn.execute(
                "INSERT INTO paradigms (category_id, code, title, content, is_global, "
                "order_priority, active, created_at, modified_at) VALUES (?,?,?,?,?,?,1,?,?)",
                (p["category_id"], p["code"], p["title"], p["content"],
                 p["is_global"], p["order_priority"], now, now)
            )

        # 2. Updates active
        for paradigm_id, val in payload["active_updates"]:
            conn.execute(
                "UPDATE paradigms SET active=?, modified_at=? WHERE id=?",
                (val, now, paradigm_id)
            )

        # 3. Updates is_global
        for paradigm_id, val in payload["global_updates"]:
            conn.execute(
                "UPDATE paradigms SET is_global=?, modified_at=? WHERE id=?",
                (val, now, paradigm_id)
            )
            if val == 1:
                # Nettoyage des bindings explicites devenus redondants
                conn.execute(
                    "DELETE FROM agent_paradigms WHERE paradigm_id=?",
                    (paradigm_id,)
                )

        # 4. Suppressions de bindings
        for agent_id, paradigm_id in payload["bindings_remove"]:
            conn.execute(
                "DELETE FROM agent_paradigms WHERE agent_id=? AND paradigm_id=?",
                (agent_id, paradigm_id)
            )

        # 5. Ajouts de bindings
        for agent_id, paradigm_id in payload["bindings_add"]:
            conn.execute(
                "INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (?,?)",
                (agent_id, paradigm_id)
            )
        conn.commit()
```

Réponse :
- Succès : `{"ok": true}` HTTP 200
- Erreur (ex. code dupliqué) : `{"error": "...message..."}` HTTP 400

### 1c. Serveur HTTP

```python
class _Handler(BaseHTTPRequestHandler):
    db_path: Path  # injecté via closure ou attribut de classe

    def do_GET(self):
        if self.path == "/":
            data = _build_initial_data(self.db_path)
            html = HTML_TEMPLATE.replace('"__INITIAL_DATA__"', json.dumps(data))
            self._respond(200, "text/html", html.encode())
        else:
            self._respond(404, "text/plain", b"Not found")

    def do_POST(self):
        if self.path == "/apply":
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            try:
                _apply(self.db_path, payload)
                self._respond(200, "application/json", json.dumps({"ok": True}).encode())
            except Exception as e:
                self._respond(400, "application/json",
                              json.dumps({"error": str(e)}).encode())

    def log_message(self, *_): pass  # silencieux


def main():
    db_path = Path(__file__).parent.parent / "jeanmichel.db"
    # ... parsing args --db, --port
    HandlerClass = type("H", (_Handler,), {"db_path": db_path})
    with HTTPServer(("localhost", 8765), HandlerClass) as srv:
        print(f"Paradigm Matrix → http://localhost:8765  (Ctrl+C pour quitter)")
        srv.serve_forever()
```

---

## Tâche 2 — Frontend HTML/JS (`HTML_TEMPLATE`)

**Constante Python dans le même fichier**, valeur assignée après la définition de `main`.

### 2a. Squelette HTML

```html
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paradigm Matrix — Jean-Michel</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css">
  <style>
    /* Overrides minimes — WaterCSS gère le reste */
    table { width: 100%; }
    th, td { text-align: center; vertical-align: middle; }
    td:first-child { text-align: left; padding-left: 1rem; }
    tr.section-header td { font-weight: bold; font-size: 1.1em;
                           background: var(--background-alt); }
    tr.category-header td { font-style: italic; opacity: 0.8;
                            background: var(--background-alt); }
    tr.inactive td { opacity: 0.4; text-decoration: line-through; }
    tr.new-paradigm td { font-style: italic; color: var(--links); }
    .badge-new { font-size: 0.7em; background: var(--links);
                 color: white; border-radius: 3px; padding: 0 4px; }
    #add-form { margin-top: 2rem; }
    #btn-apply { margin-top: 1rem; }
    #status-msg { margin-left: 1rem; }
  </style>
</head>
<body>
  <h1>Paradigm Matrix</h1>
  <p><em>Modifiez les cases, puis cliquez <strong>Valider</strong>. Fermez l'onglet pour annuler.</em></p>

  <div id="matrix-container"></div>

  <button id="btn-apply" disabled>Valider les modifications</button>
  <button id="btn-reset" onclick="resetPending()">Annuler</button>
  <span id="status-msg"></span>

  <details id="add-form">
    <summary>Ajouter un paradigme</summary>
    <!-- formulaire inline -->
  </details>

  <script>
    const INITIAL_DATA = "__INITIAL_DATA__";
    // ... reste du JS
  </script>
</body>
</html>
```

### 2b. État JS

```js
// Snapshot initial (jamais muté)
const DB = INITIAL_DATA;

// Diff en cours — rien n'est écrit en DB avant /apply
const pending = {
  bindings_add:    new Set(),  // "agentId:paradigmId"
  bindings_remove: new Set(),
  global_updates:  new Map(),  // paradigmId → 0|1
  active_updates:  new Map(),  // paradigmId → 0|1
  new_paradigms:   [],
};

function hasPending() {
  return pending.bindings_add.size > 0
      || pending.bindings_remove.size > 0
      || pending.global_updates.size > 0
      || pending.active_updates.size > 0
      || pending.new_paradigms.length > 0;
}

function resetPending() {
  pending.bindings_add.clear();
  pending.bindings_remove.clear();
  pending.global_updates.clear();
  pending.active_updates.clear();
  pending.new_paradigms = [];
  renderMatrix();
}
```

### 2c. Rendu de la matrice

Fonction `renderMatrix()` appelée à chaque changement d'état. Reconstruit le `<table>` complet à partir de `DB` + `pending` (pure render, pas de DOM diff).

Colonnes : `Paradigme | [G] | On | agent_1 | agent_2 | ...`

Logique par case agent :
- `is_global == 1` (après résolution des `pending.global_updates`) → `checked + disabled`
- `bound_agent_ids.includes(agent.id)` OU dans `pending.bindings_add` → `checked`
- Sinon → unchecked

### 2d. Gestionnaires d'événements

```js
// Case agent (bind/unbind)
function onBindingToggle(agentId, paradigmId, checked) {
  const key = `${agentId}:${paradigmId}`;
  const wasInDb = DB_BOUND_SET.has(key);  // lookup snapshot
  if (checked) {
    pending.bindings_remove.delete(key);
    if (!wasInDb) pending.bindings_add.add(key);
  } else {
    pending.bindings_add.delete(key);
    if (wasInDb) pending.bindings_remove.add(key);
  }
  updateApplyButton();
}

// Case [G]
function onGlobalToggle(paradigmId, checked) {
  const wasInDb = /* snapshot */ DB.sections.flatMap(...)...is_global;
  const changed = checked !== wasInDb;
  if (changed) pending.global_updates.set(paradigmId, checked ? 1 : 0);
  else pending.global_updates.delete(paradigmId);
  renderMatrix();  // re-render pour griser/dégriser les cases
  updateApplyButton();
}

// Case On/Off (active)
function onActiveToggle(paradigmId, checked) { /* même logique */ }
```

### 2e. Bouton Valider

```js
async function applyChanges() {
  const payload = {
    bindings_add:    [...pending.bindings_add].map(k => k.split(":").map(Number)),
    bindings_remove: [...pending.bindings_remove].map(k => k.split(":").map(Number)),
    global_updates:  [...pending.global_updates.entries()],
    active_updates:  [...pending.active_updates.entries()],
    new_paradigms:   pending.new_paradigms,
  };
  const res = await fetch("/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (data.ok) {
    location.reload();  // recharge depuis DB — état propre
  } else {
    document.getElementById("status-msg").textContent = "Erreur : " + data.error;
  }
}
```

### 2f. Formulaire "Ajouter un paradigme"

Champs : `category_id` (select), `code`, `title`, `content` (textarea), `is_global` (checkbox), `order_priority` (number).

Clic **Ajouter** → valide localement (code non vide, code non dupliqué dans `DB` + `pending.new_paradigms`) → push dans `pending.new_paradigms` → `renderMatrix()` affiche la ligne avec badge `[nouveau]` → bouton Valider s'active.

---

## Tâche 3 — Intégration `jm.sh`

Ajouter dans `jm.sh` :

### Dans `usage()` :
```bash
  --paradigm-matrix           Open the paradigm matrix editor at http://localhost:8765
```

### Nouvelle fonction :
```bash
cmd_paradigm_matrix() {
  ensure_venv
  if [ ! -f "${DB_PATH}" ]; then
    echo "Error: database not found at ${DB_PATH}" >&2
    echo "Run ./jm.sh --install first." >&2
    exit 1
  fi
  export JEANMICHEL_HOME="${PROJECT_ROOT}"
  exec python "${PROJECT_ROOT}/debug/paradigm_matrix.py" "$@"
}
```

### Dans le `case` de dispatch :
```bash
  --paradigm-matrix)
    shift
    cmd_paradigm_matrix "$@"
    ;;
```

À insérer après le bloc `--browse-db`.

---

## Ordre de mise en oeuvre

Les tâches 1, 2 et 3 sont **indépendantes**. Ordre recommandé pour agents parallèles :

| Agent | Tâche | Fichier(s) |
|---|---|---|
| A | Tâche 1 (backend Python) | `debug/paradigm_matrix.py` (squelette + handlers) |
| B | Tâche 2 (HTML/JS template) | constante `HTML_TEMPLATE` à intégrer dans le fichier de A |
| C | Tâche 3 (jm.sh) | `jm.sh` |

> **Point de synchronisation** : A et B convergent dans `debug/paradigm_matrix.py`. B peut écrire le template dans un fichier séparé `debug/_matrix_template.html` puis A l'intègre comme string Python, ou ils travaillent directement sur le fichier final si la séquence est A → B.

---

## Contraintes et points d'attention

- **Pas de dépendances nouvelles** — stdlib uniquement (`http.server`, `json`, `sqlite3`, `urllib.parse`, `threading` si besoin).
- **WaterCSS via CDN** : `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css">` — thème auto clair/sombre selon préférence système.
- **Transaction atomique** : tout le payload `/apply` passe dans une seule transaction SQLite. Si une INSERT échoue (ex. code dupliqué), rien n'est écrit.
- **Cocher `is_global=1`** → DELETE automatique des `agent_paradigms` pour ce paradigme (bindings devenus redondants).
- **Décocher `is_global=0`** → aucun binding auto-inséré. La case redevient vide pour tous les agents.
- Le serveur est purement local (`localhost`), aucune exposition réseau.
- Pas de rechargement automatique : l'utilisateur clique Valider → `location.reload()`.
- `log_message` désactivé dans le handler pour ne pas polluer le terminal.
- Le fichier doit fonctionner standalone (`python debug/paradigm_matrix.py`) ET via `jm.sh`.

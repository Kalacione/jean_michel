#!/usr/bin/env python3
"""Paradigm Matrix — visual editor for paradigm↔agent associations.

Usage:
    python debug/paradigm_matrix.py [--db PATH] [--port PORT]

Opens http://localhost:8765 (default port) in the browser.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_initial_data(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        agents = [
            {"id": r["id"], "code": r["code"], "name": r["name"], "role": r["role"]}
            for r in conn.execute(
                "SELECT id, code, name, role FROM agents WHERE active=1 ORDER BY id"
            ).fetchall()
        ]

        # All explicit bindings: {paradigm_id: [agent_id, ...]}
        bindings: dict[int, list[int]] = {}
        for row in conn.execute("SELECT agent_id, paradigm_id FROM agent_paradigms").fetchall():
            bindings.setdefault(row["paradigm_id"], []).append(row["agent_id"])

        # sections → categories → paradigms
        sections_raw = conn.execute(
            "SELECT id, code, title FROM sections WHERE active=1 ORDER BY order_priority, id"
        ).fetchall()

        sections = []
        for sec in sections_raw:
            cats_raw = conn.execute(
                "SELECT id, code, title FROM categories "
                "WHERE section_id=? AND active=1 ORDER BY order_priority, id",
                (sec["id"],),
            ).fetchall()
            categories = []
            for cat in cats_raw:
                paradigms_raw = conn.execute(
                    "SELECT id, code, title, is_global, active, order_priority, category_id "
                    "FROM paradigms WHERE category_id=? ORDER BY order_priority, id",
                    (cat["id"],),
                ).fetchall()
                paradigms = [
                    {
                        "id": p["id"],
                        "code": p["code"],
                        "title": p["title"],
                        "is_global": p["is_global"],
                        "active": p["active"],
                        "order_priority": p["order_priority"],
                        "category_id": p["category_id"],
                        "bound_agent_ids": bindings.get(p["id"], []),
                    }
                    for p in paradigms_raw
                ]
                if paradigms:
                    categories.append({
                        "id": cat["id"],
                        "code": cat["code"],
                        "title": cat["title"],
                        "paradigms": paradigms,
                    })
            if categories:
                sections.append({
                    "code": sec["code"],
                    "title": sec["title"],
                    "categories": categories,
                })

        # Flat list for add-paradigm form (includes inactive sections/categories)
        cats_all = conn.execute(
            "SELECT c.id, c.code AS cat_code, c.title AS cat_title, "
            "s.code AS sec_code, s.title AS sec_title "
            "FROM categories c JOIN sections s ON s.id=c.section_id "
            "WHERE c.active=1 AND s.active=1 "
            "ORDER BY s.order_priority, c.order_priority, c.id"
        ).fetchall()
        categories_flat = [
            {
                "id": r["id"],
                "key": f"{r['sec_code']}.{r['cat_code']}",
                "label": f"{r['sec_title']} / {r['cat_title']}",
            }
            for r in cats_all
        ]

    return {"agents": agents, "sections": sections, "categories_flat": categories_flat}


def _apply(db_path: Path, payload: dict) -> None:
    now = _now()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        # 1. New paradigms
        for p in payload.get("new_paradigms", []):
            conn.execute(
                "INSERT INTO paradigms "
                "(category_id, code, title, content, is_global, order_priority, "
                "active, created_at, modified_at) VALUES (?,?,?,?,?,?,1,?,?)",
                (
                    p["category_id"], p["code"], p["title"], p["content"],
                    int(p.get("is_global", 0)), int(p.get("order_priority", 100)),
                    now, now,
                ),
            )

        # 2. active updates
        for paradigm_id, val in payload.get("active_updates", []):
            conn.execute(
                "UPDATE paradigms SET active=?, modified_at=? WHERE id=?",
                (int(val), now, int(paradigm_id)),
            )

        # 3. is_global updates
        for paradigm_id, val in payload.get("global_updates", []):
            conn.execute(
                "UPDATE paradigms SET is_global=?, modified_at=? WHERE id=?",
                (int(val), now, int(paradigm_id)),
            )
            if int(val) == 1:
                # Remove explicit bindings that are now redundant
                conn.execute(
                    "DELETE FROM agent_paradigms WHERE paradigm_id=?",
                    (int(paradigm_id),),
                )

        # 4. Remove bindings
        for agent_id, paradigm_id in payload.get("bindings_remove", []):
            conn.execute(
                "DELETE FROM agent_paradigms WHERE agent_id=? AND paradigm_id=?",
                (int(agent_id), int(paradigm_id)),
            )

        # 5. Add bindings
        for agent_id, paradigm_id in payload.get("bindings_add", []):
            conn.execute(
                "INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (?,?)",
                (int(agent_id), int(paradigm_id)),
            )

        conn.commit()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    db_path: Path  # set on the class before instantiation

    def do_GET(self) -> None:
        if self.path != "/":
            self._respond(404, "text/plain", b"Not found")
            return
        try:
            data = _build_initial_data(self.db_path)
            html = HTML_TEMPLATE.replace('"__INITIAL_DATA__"', json.dumps(data))
            self._respond(200, "text/html; charset=utf-8", html.encode())
        except Exception as exc:
            self._respond(500, "text/plain", str(exc).encode())

    def do_POST(self) -> None:
        if self.path != "/apply":
            self._respond(404, "text/plain", b"Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            _apply(self.db_path, payload)
            self._respond(200, "application/json", json.dumps({"ok": True}).encode())
        except Exception as exc:
            self._respond(400, "application/json",
                          json.dumps({"error": str(exc)}).encode())

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_) -> None:  # silence access log
        pass


# ---------------------------------------------------------------------------
# HTML + JS template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paradigm Matrix — Jean-Michel</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css">
  <style>
    body { max-width: 100%; padding: 1rem 2rem; }
    h1 { margin-bottom: 0.25rem; }
    .subtitle { opacity: 0.6; margin-top: 0; margin-bottom: 1.5rem; font-size: 0.9em; }

    table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
    th, td { padding: 0.35rem 0.5rem; text-align: center; vertical-align: middle; }
    td.label { text-align: left; white-space: nowrap; max-width: 260px;
               overflow: hidden; text-overflow: ellipsis; }

    tr.section-row td { font-weight: bold; font-size: 1em;
                        background: var(--background-alt, #eee);
                        border-top: 2px solid var(--border, #ccc); }
    tr.category-row td { font-style: italic; opacity: 0.75;
                         background: var(--background-alt, #eee); }
    tr.inactive-row { opacity: 0.4; }
    tr.inactive-row td.label { text-decoration: line-through; }
    tr.new-row td.label { font-style: italic; }
    .badge-new { font-size: 0.65em; background: var(--links, steelblue);
                 color: #fff; border-radius: 3px; padding: 1px 4px;
                 margin-left: 4px; vertical-align: middle; }

    input[type=checkbox] { cursor: pointer; width: 1rem; height: 1rem; }
    input[type=checkbox]:disabled { cursor: default; opacity: 0.5; }

    .toolbar { display: flex; align-items: center; gap: 0.75rem;
               margin: 1.5rem 0 1rem; flex-wrap: wrap; }
    #status-msg { font-size: 0.9em; opacity: 0.8; }
    #status-msg.error { color: var(--error, crimson); }

    details { margin-top: 2rem; }
    details summary { cursor: pointer; font-weight: bold; }
    #add-form-inner { margin-top: 1rem; display: grid;
                      grid-template-columns: 1fr 1fr; gap: 0.5rem 1rem; }
    #add-form-inner label { display: flex; flex-direction: column; gap: 0.2rem;
                             font-size: 0.9em; }
    #add-form-inner label.full { grid-column: 1 / -1; }
    #add-form-inner textarea { min-height: 80px; font-family: monospace;
                                font-size: 0.85em; }
    #add-paradigm-btn { margin-top: 0.75rem; }
    #add-error { color: var(--error, crimson); font-size: 0.85em; min-height: 1.2em; }
  </style>
</head>
<body>
  <h1>Paradigm Matrix</h1>
  <p class="subtitle">Modifiez les cases, puis cliquez <strong>Valider</strong>.
     Fermez l'onglet pour annuler sans sauvegarder.</p>

  <div id="matrix-container"></div>

  <div class="toolbar">
    <button id="btn-apply" disabled onclick="applyChanges()">Valider les modifications</button>
    <button onclick="resetPending()">Annuler les modifications</button>
    <span id="status-msg"></span>
  </div>

  <details id="add-section">
    <summary>Ajouter un paradigme</summary>
    <div id="add-form-inner">
      <label class="full">
        Catégorie
        <select id="f-category"></select>
      </label>
      <label>
        Code (snake_case)
        <input type="text" id="f-code" placeholder="mon_paradigme">
      </label>
      <label>
        Titre
        <input type="text" id="f-title" placeholder="Mon paradigme">
      </label>
      <label class="full">
        Contenu (markdown bullets)
        <textarea id="f-content" placeholder="- Première directive&#10;- Deuxième directive"></textarea>
      </label>
      <label>
        Order priority
        <input type="number" id="f-order" value="100" min="0">
      </label>
      <label style="justify-content: center; flex-direction: row; align-items: center; gap: 0.5rem;">
        <input type="checkbox" id="f-global"> Global (tous les agents)
      </label>
      <div class="full">
        <button id="add-paradigm-btn" onclick="addPendingParadigm()">Ajouter</button>
        <span id="add-error"></span>
      </div>
    </div>
  </details>

  <script>
  const INITIAL_DATA = "__INITIAL_DATA__";

  // ---- state ----
  const DB = INITIAL_DATA;

  const pending = {
    bindings_add:    new Set(),   // "agentId:paradigmId"
    bindings_remove: new Set(),   // "agentId:paradigmId"
    global_updates:  new Map(),   // paradigmId (number) → 0|1
    active_updates:  new Map(),   // paradigmId (number) → 0|1
    new_paradigms:   [],
  };

  // Quick lookup: "agentId:paradigmId" for initial DB bindings
  const DB_BOUND = new Set();
  DB.sections.forEach(sec =>
    sec.categories.forEach(cat =>
      cat.paradigms.forEach(p =>
        p.bound_agent_ids.forEach(aid => DB_BOUND.add(`${aid}:${p.id}`))
      )
    )
  );

  // Quick lookup: initial is_global per paradigm id
  const DB_GLOBAL = new Map();
  const DB_ACTIVE = new Map();
  DB.sections.forEach(sec =>
    sec.categories.forEach(cat =>
      cat.paradigms.forEach(p => {
        DB_GLOBAL.set(p.id, p.is_global);
        DB_ACTIVE.set(p.id, p.active);
      })
    )
  );

  function hasPending() {
    return pending.bindings_add.size > 0
        || pending.bindings_remove.size > 0
        || pending.global_updates.size > 0
        || pending.active_updates.size > 0
        || pending.new_paradigms.length > 0;
  }

  function updateApplyButton() {
    document.getElementById("btn-apply").disabled = !hasPending();
  }

  function resetPending() {
    pending.bindings_add.clear();
    pending.bindings_remove.clear();
    pending.global_updates.clear();
    pending.active_updates.clear();
    pending.new_paradigms = [];
    document.getElementById("status-msg").textContent = "";
    document.getElementById("status-msg").className = "";
    renderMatrix();
    updateApplyButton();
  }

  // ---- effective state helpers ----

  function effectiveGlobal(paradigmId) {
    return pending.global_updates.has(paradigmId)
      ? pending.global_updates.get(paradigmId) === 1
      : DB_GLOBAL.get(paradigmId) === 1;
  }

  function effectiveActive(paradigmId) {
    return pending.active_updates.has(paradigmId)
      ? pending.active_updates.get(paradigmId) === 1
      : DB_ACTIVE.get(paradigmId) === 1;
  }

  function effectiveBound(agentId, paradigmId) {
    const key = `${agentId}:${paradigmId}`;
    if (pending.bindings_add.has(key)) return true;
    if (pending.bindings_remove.has(key)) return false;
    return DB_BOUND.has(key);
  }

  // ---- event handlers ----

  function onBindingToggle(agentId, paradigmId, checked) {
    const key = `${agentId}:${paradigmId}`;
    const wasInDb = DB_BOUND.has(key);
    if (checked) {
      pending.bindings_remove.delete(key);
      if (!wasInDb) pending.bindings_add.add(key);
    } else {
      pending.bindings_add.delete(key);
      if (wasInDb) pending.bindings_remove.add(key);
    }
    updateApplyButton();
  }

  function onGlobalToggle(paradigmId, checked) {
    const wasInDb = DB_GLOBAL.get(paradigmId) === 1;
    if (checked !== wasInDb) {
      pending.global_updates.set(paradigmId, checked ? 1 : 0);
    } else {
      pending.global_updates.delete(paradigmId);
    }
    renderMatrix();
    updateApplyButton();
  }

  function onActiveToggle(paradigmId, checked) {
    const wasInDb = DB_ACTIVE.get(paradigmId) === 1;
    if (checked !== wasInDb) {
      pending.active_updates.set(paradigmId, checked ? 1 : 0);
    } else {
      pending.active_updates.delete(paradigmId);
    }
    renderMatrix();
    updateApplyButton();
  }

  // ---- render ----

  function renderMatrix() {
    const agents = DB.agents;
    const container = document.getElementById("matrix-container");

    // Gather all paradigms to render: DB ones + pending new
    // Build combined sections structure with pending new appended
    const sections = DB.sections;

    let html = '<table><thead><tr>';
    html += '<th style="text-align:left">Paradigme</th>';
    html += '<th title="Global — appliqué à tous les agents">G</th>';
    html += '<th title="Actif">On</th>';
    agents.forEach(a => {
      html += `<th title="${escHtml(a.code)}">${escHtml(a.name)}</th>`;
    });
    html += '</tr></thead><tbody>';

    sections.forEach(sec => {
      html += `<tr class="section-row"><td class="label" colspan="${3 + agents.length}">`
            + `&#9654; ${escHtml(sec.title.toUpperCase())}</td></tr>`;

      sec.categories.forEach(cat => {
        html += `<tr class="category-row"><td class="label" colspan="${3 + agents.length}">`
              + `&nbsp;&nbsp;&#8212; ${escHtml(cat.title)}</td></tr>`;

        cat.paradigms.forEach(p => {
          const isGlobal  = effectiveGlobal(p.id);
          const isActive  = effectiveActive(p.id);
          const rowClass  = isActive ? "" : " inactive-row";

          html += `<tr class="${rowClass}">`;
          html += `<td class="label" title="${escHtml(p.code)}">&nbsp;&nbsp;&nbsp;&nbsp;${escHtml(p.title)}</td>`;

          // [G] checkbox
          const gChecked  = isGlobal ? "checked" : "";
          html += `<td><input type="checkbox" ${gChecked} `
               + `onchange="onGlobalToggle(${p.id}, this.checked)" `
               + `title="Global"></td>`;

          // [On] checkbox
          const aChecked  = isActive ? "checked" : "";
          html += `<td><input type="checkbox" ${aChecked} `
               + `onchange="onActiveToggle(${p.id}, this.checked)" `
               + `title="Actif / inactif"></td>`;

          // Per-agent checkboxes
          agents.forEach(a => {
            if (isGlobal) {
              html += `<td><input type="checkbox" checked disabled title="Via global"></td>`;
            } else {
              const bound   = effectiveBound(a.id, p.id);
              const chk     = bound ? "checked" : "";
              html += `<td><input type="checkbox" ${chk} `
                   + `onchange="onBindingToggle(${a.id}, ${p.id}, this.checked)"></td>`;
            }
          });

          html += "</tr>";
        });
      });
    });

    // Pending new paradigms
    if (pending.new_paradigms.length > 0) {
      html += `<tr class="section-row"><td class="label" colspan="${3 + agents.length}">`
            + `&#9654; EN ATTENTE D'AJOUT</td></tr>`;
      pending.new_paradigms.forEach((p, idx) => {
        html += `<tr class="new-row">`;
        html += `<td class="label">&nbsp;&nbsp;&nbsp;&nbsp;${escHtml(p.title)}`
              + `<span class="badge-new">nouveau</span></td>`;
        const gChecked = p.is_global ? "checked" : "";
        html += `<td><input type="checkbox" ${gChecked} disabled title="Défini à la création"></td>`;
        html += `<td><input type="checkbox" checked disabled title="Sera actif"></td>`;
        agents.forEach(() => {
          html += p.is_global
            ? `<td><input type="checkbox" checked disabled title="Via global"></td>`
            : `<td><input type="checkbox" disabled title="Bindable après validation"></td>`;
        });
        html += `<td><button onclick="removePendingParadigm(${idx})" `
              + `style="padding:0 6px;font-size:0.8em">✕</button></td>`;
        html += "</tr>";
      });
    }

    html += "</tbody></table>";
    container.innerHTML = html;
  }

  function removePendingParadigm(idx) {
    pending.new_paradigms.splice(idx, 1);
    renderMatrix();
    updateApplyButton();
  }

  // ---- add paradigm form ----

  function populateCategorySelect() {
    const sel = document.getElementById("f-category");
    DB.categories_flat.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.label;
      sel.appendChild(opt);
    });
  }

  function addPendingParadigm() {
    const errEl = document.getElementById("add-error");
    errEl.textContent = "";

    const category_id   = parseInt(document.getElementById("f-category").value, 10);
    const code          = document.getElementById("f-code").value.trim();
    const title         = document.getElementById("f-title").value.trim();
    const content       = document.getElementById("f-content").value.trim();
    const is_global     = document.getElementById("f-global").checked ? 1 : 0;
    const order_priority = parseInt(document.getElementById("f-order").value, 10) || 100;

    if (!code) { errEl.textContent = "Le code est requis."; return; }
    if (!title) { errEl.textContent = "Le titre est requis."; return; }
    if (!content) { errEl.textContent = "Le contenu est requis."; return; }

    // Check for duplicate code in DB
    const allDbCodes = new Set();
    DB.sections.forEach(sec =>
      sec.categories.forEach(cat =>
        cat.paradigms.forEach(p => allDbCodes.add(p.code))
      )
    );
    const pendingCodes = new Set(pending.new_paradigms.map(p => p.code));
    if (allDbCodes.has(code) || pendingCodes.has(code)) {
      errEl.textContent = `Code "${code}" déjà utilisé.`;
      return;
    }

    pending.new_paradigms.push({ category_id, code, title, content, is_global, order_priority });

    // Reset form
    document.getElementById("f-code").value = "";
    document.getElementById("f-title").value = "";
    document.getElementById("f-content").value = "";
    document.getElementById("f-global").checked = false;
    document.getElementById("f-order").value = "100";

    renderMatrix();
    updateApplyButton();
  }

  // ---- apply ----

  async function applyChanges() {
    const statusEl = document.getElementById("status-msg");
    statusEl.textContent = "Envoi…";
    statusEl.className = "";

    const payload = {
      bindings_add:    [...pending.bindings_add].map(k => k.split(":").map(Number)),
      bindings_remove: [...pending.bindings_remove].map(k => k.split(":").map(Number)),
      global_updates:  [...pending.global_updates.entries()],
      active_updates:  [...pending.active_updates.entries()],
      new_paradigms:   pending.new_paradigms,
    };

    try {
      const res = await fetch("/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        location.reload();
      } else {
        statusEl.textContent = "Erreur : " + data.error;
        statusEl.className = "error";
      }
    } catch (e) {
      statusEl.textContent = "Erreur réseau : " + e.message;
      statusEl.className = "error";
    }
  }

  // ---- utils ----

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- init ----
  populateCategorySelect();
  renderMatrix();
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Paradigm Matrix editor")
    parser.add_argument("--db", type=Path, default=None, help="Path to jeanmichel.db")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    args = parser.parse_args()

    db_path = args.db or ROOT / "jeanmichel.db"
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        print("Run ./jm.sh --install first.", file=sys.stderr)
        sys.exit(1)

    HandlerClass = type("_H", (_Handler,), {"db_path": db_path})
    url = f"http://localhost:{args.port}"
    print(f"Paradigm Matrix → {url}  (Ctrl+C to quit)")
    webbrowser.open(url)
    with HTTPServer(("localhost", args.port), HandlerClass) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nBye.")


if __name__ == "__main__":
    main()

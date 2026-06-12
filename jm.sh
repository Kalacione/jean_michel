#!/usr/bin/env bash
# jm — Jean-Michel unified entry point.
#
# Usage:
#   ./jm.sh                              Launch the interactive CLI (default)
#   ./jm.sh --install                    Setup venv and database
#   ./jm.sh --test [PYTEST_ARGS ...]      Run the test suite
#   ./jm.sh --export-db [--out FILE]     Export the DB to backups/db_TIMESTAMP.sql
#   ./jm.sh --browse-db                  Open the DB in sqlite_web (port 8080)
#   ./jm.sh --inspect-conv ID [...]      Inspect a conversation's artifacts
#   ./jm.sh --clean [--days N] [--yes]   Delete conversations older than N days
#   ./jm.sh --admin [CMD ...]            Manage agents, tools, and paradigms (REPL or one-shot)
#   ./jm.sh --meta-analysis              Run a meta-analysis turn (self-improvement)
#   ./jm.sh --help                       Show this help
#
# Extra args after a command are forwarded to the underlying tool.
# Examples:
#   ./jm.sh --export-db
#   ./jm.sh --export-db --out /tmp/db.sql
#   ./jm.sh --clean --days 30
#   ./jm.sh --inspect-conv abc123 --kind thought response
#   ./jm.sh --inspect-conv abc123 --list

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DB_PATH="${PROJECT_ROOT}/jeanmichel.db"
SCHEMA_PATH="${PROJECT_ROOT}/db/schema.sql"
PYTHON_BIN="${PYTHON_BIN:-python3.14}"

# Anchor the Python config layer (config.REPO_ROOT reads JEANMICHEL_HOME, else
# falls back to the caller's CWD). Exported ONCE so every command — and every
# subprocess it execs — resolves the DB / conversations/ / user_profile from
# the repo, regardless of where ./jm.sh was invoked from.
export JEANMICHEL_HOME="${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: ./jm.sh [COMMAND] [OPTIONS]

Commands:
  (default)                   Launch the interactive CLI
  --install                   Create venv, install deps, initialize the DB
  --test [PYTEST_ARGS ...]    Run the test suite (extra args forwarded to pytest)
  --serve                     Launch the web daemon (FastAPI) at http://0.0.0.0:8000
  --create-user <username>    Create a web frontend user (prompts for password)
  --build-docker [VARIANT]    Build sandbox Docker image (py-alpine|node-alpine|all; default: py-alpine)
  --export-db [--out FILE]    Dump DB to backups/db_TIMESTAMP.sql (or FILE)
                              (alias: --backup-db)
  --browse-db                 Open the database in sqlite_web at http://localhost:8080
  --paradigm-matrix           Open the paradigm matrix editor at http://localhost:8770
  --synoptic [--stdout]       Generate the agent synoptic diagram from the DB (docs/agents_synoptic.md)
  --orchestrator-map [--stdout]  Generate the orchestrator determinism reference (docs/orchestrator_determinism.md)
  --inspect-conv ID [...]     Inspect artifacts of a conversation (by ID prefix)
  --clean [--days N] [--yes]  Delete conversations older than N days (default: 7)
  --reap-sandboxes [--idle-minutes N]  Stop lingering jm-sandbox-* containers (default: all)
  --admin [CMD ...]           Manage agents, tools, and paradigms (interactive REPL or one-shot)
  --meta-analysis             Run a meta-analysis: inspect system state and produce improvement proposals
  --help                      Show this help

CLI pass-through:
  Unknown flags/args are forwarded to the jean-michel CLI.

Examples:
  ./jm.sh
  ./jm.sh --install
  ./jm.sh --export-db
  ./jm.sh --export-db --out /tmp/db.sql
  ./jm.sh --browse-db
  ./jm.sh --inspect-conv abc123
  ./jm.sh --inspect-conv abc123 --agent jean-michel --kind thought response
  ./jm.sh --inspect-conv abc123 --list
  ./jm.sh --clean
  ./jm.sh --clean --days 30 --yes
EOF
}

# Ensure the venv exists and is activated.
# If it doesn't exist, run the install step first.
ensure_venv() {
  if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "venv not found — running install first."
    cmd_install
  fi
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_install() {
  # ---- Python version check -----------------------------------------------
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Error: ${PYTHON_BIN} not found in PATH." >&2
    echo "Install Python 3.14 or set PYTHON_BIN to a 3.14+ interpreter." >&2
    exit 1
  fi

  PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print("{}.{}".format(sys.version_info.major, sys.version_info.minor))')"
  PY_MAJOR="${PY_VERSION%%.*}"
  PY_MINOR="${PY_VERSION##*.}"
  if [ "${PY_MAJOR}" -lt 3 ] || { [ "${PY_MAJOR}" -eq 3 ] && [ "${PY_MINOR}" -lt 14 ]; }; then
    echo "Error: Python 3.14+ required (found ${PY_VERSION})." >&2
    exit 1
  fi

  # ---- venv ---------------------------------------------------------------
  if [ ! -d "${VENV_DIR}" ]; then
    echo "[1/4] Creating venv at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  else
    echo "[1/4] venv already exists at ${VENV_DIR}"
  fi

  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  # ---- dependencies -------------------------------------------------------
  echo "[2/4] Installing dependencies"
  pip install --upgrade pip >/dev/null
  pip cache purge >/dev/null 2>&1 || true
  pip install -e ".[dev,web,mcp]"

  # ---- database -----------------------------------------------------------
  echo "[3/4] Initializing SQLite database"
  if [ -f "${DB_PATH}" ]; then
    echo "  ${DB_PATH} already exists — skipping schema load."
    echo "  Delete it and re-run if you want a fresh seed."
  else
    python -c "
import sqlite3
conn = sqlite3.connect('${DB_PATH}')
with open('${SCHEMA_PATH}') as f:
    conn.executescript(f.read())
conn.close()
print('  Database created at ${DB_PATH}')
"
  fi

  # ---- sandbox Docker images (optional — requires Docker) -----------------
  echo "[4/4] Building sandbox Docker images"
  if command -v docker >/dev/null 2>&1; then
    cmd_build_docker all || echo "  (image build failed — run ./jm.sh --build-docker all once Docker is running)"
  else
    echo "  Docker not found — skipping. Build later with: ./jm.sh --build-docker all"
  fi

  # ---- post-install : vocal-mode prerequisites (warn only) ----------------
  # Vocal mode needs the user to be in the `audio` group so PipeWire /
  # ALSA expose real sinks. We don't run sudo from here — just flag the
  # missing config so it's not forgotten on first --mode vocal use.
  if id -nG | tr ' ' '\n' | grep -qx audio; then
    : # OK, nothing to say
  else
    echo
    echo "⚠  Vocal mode prerequisite missing : your user is not in the 'audio' group."
    echo "   Without this, PipeWire falls back to a 'null' sink and no sound plays."
    echo "   Fix once and for all :"
    echo
    echo "       sudo usermod -aG audio \$USER"
    echo
    echo "   Then close your session completely and log back in (or reboot)."
    echo "   Verify : 'groups' should list 'audio', 'aplay -l' should list cards,"
    echo "   'pactl get-default-sink' should NOT return 'auto_null'."
    echo
    echo "   You can keep using non-vocal modes without this — it's vocal-mode only."
  fi

  # ---- post-install : MCP client (optional, opt-in) -----------------------
  if [ ! -f "${PROJECT_ROOT}/mcp_servers.toml" ]; then
    echo
    echo "ℹ  MCP client ready (the 'mcp' extra is installed). To connect hosted"
    echo "   MCP servers (Vuetify, GitHub…) as native tools, enable it with:"
    echo "       cp mcp_servers.example.toml mcp_servers.toml"
  fi

  echo
  echo "Done."
}

# Read KEY from the environment, else from .env (KEY=value), else empty.
dotenv_val() {
  local key="$1" v="${!1:-}"
  if [ -z "$v" ] && [ -f "${PROJECT_ROOT}/.env" ]; then
    v="$(sed -n "s/^${key}=//p" "${PROJECT_ROOT}/.env" | tail -1 | tr -d '"')"
  fi
  printf '%s' "$v"
}

_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;; *) return 1 ;;
  esac
}

# Optionally auto-start the local graphify MCP server (opt-in, idempotent).
# Gated by JEANMICHEL_GRAPHIFY_ENABLED (env or .env) ; no-op otherwise. When the
# port is already listening we reuse it ; the server is a persistent local dev
# service (stop it with: pkill -f graphify.serve).
maybe_start_graphify() {
  _truthy "$(dotenv_val JEANMICHEL_GRAPHIFY_ENABLED)" || return 0
  local port; port="$(dotenv_val GRAPHIFY_MCP_PORT)"; port="${port:-8765}"
  if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
    echo "✓ graphify : serveur MCP déjà en écoute (127.0.0.1:${port})"
    return 0
  fi
  if [ ! -f "${PROJECT_ROOT}/graphify-out/graph.json" ]; then
    echo "ℹ graphify activé mais pas de graphe — lance ./graphify.sh build (serveur non démarré)." >&2
    return 0
  fi
  echo "▶ graphify : démarrage du serveur MCP en arrière-plan (127.0.0.1:${port}, log: graphify-out/serve.log)"
  nohup "${PROJECT_ROOT}/graphify.sh" serve "${port}" >"${PROJECT_ROOT}/graphify-out/serve.log" 2>&1 &
  disown 2>/dev/null || true
  # Attend que le port réponde (cold start uv) avant que le client MCP se connecte.
  for _ in 1 2 3 4 5 6 7 8; do
    (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && break
    sleep 1
  done
}

cmd_cli() {
  ensure_venv
  maybe_start_graphify
  exec jean-michel "$@"
}

cmd_serve() {
  # Launch the web daemon (FastAPI + uvicorn) consumed by the Vue frontend.
  # Runs in the foreground — "un daemon python à la main". Binds 0.0.0.0:8000
  # by default (override via JEANMICHEL_API_HOST / JEANMICHEL_API_PORT).
  ensure_venv
  maybe_start_graphify
  exec jean-michel-serve "$@"
}

cmd_create_user() {
  # Create a web frontend user (prompts for a password). Usage:
  #   ./jm.sh --create-user <username>
  ensure_venv
  exec python -m jeanmichel.api.auth create-user "$@"
}

cmd_export_db() {
  ensure_venv
  # If --out is already in the args, forward as-is; otherwise default to
  # exports/db_TIMESTAMP.json.
  local has_out=0
  for arg in "$@"; do
    [[ "${arg}" == "--out" ]] && has_out=1 && break
  done
  if [[ "${has_out}" -eq 0 ]]; then
    local exports_dir="${PROJECT_ROOT}/backups"
    mkdir -p "${exports_dir}"
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local out_path="${exports_dir}/db_${ts}.sql"
    python "${PROJECT_ROOT}/debug/export_db.py" --db "${DB_PATH}" --out "${out_path}" "$@"
  else
    exec python "${PROJECT_ROOT}/debug/export_db.py" --db "${DB_PATH}" "$@"
  fi
}

cmd_clean() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/clean_convs.py" "$@"
}

cmd_reap_sandboxes() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/reap_sandboxes.py" "$@"
}

cmd_browse_db() {
  ensure_venv
  if [ ! -f "${DB_PATH}" ]; then
    echo "Error: database not found at ${DB_PATH}" >&2
    echo "Run ./jm.sh --install first." >&2
    exit 1
  fi
  echo "Opening ${DB_PATH} at http://localhost:8080"
  exec sqlite_web "${DB_PATH}"
}

cmd_paradigm_matrix() {
  ensure_venv
  if [ ! -f "${DB_PATH}" ]; then
    echo "Error: database not found at ${DB_PATH}" >&2
    echo "Run ./jm.sh --install first." >&2
    exit 1
  fi
  exec python "${PROJECT_ROOT}/debug/paradigm_matrix.py" "$@"
}

cmd_synoptic() {
  # Generate the agent synoptic diagram (mermaid + roster) from the live DB.
  ensure_venv
  if [ ! -f "${DB_PATH}" ]; then
    echo "Error: database not found at ${DB_PATH}" >&2
    echo "Run ./jm.sh --install first." >&2
    exit 1
  fi
  exec python -m jeanmichel.synoptic "$@"
}

cmd_orchestrator_map() {
  # Generate the orchestrator determinism reference (live config values + anchors).
  ensure_venv
  exec python -m jeanmichel.orchestrator_map "$@"
}

cmd_inspect_conv() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/inspect_conv.py" "$@"
}

cmd_admin() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/admin.py" "$@"
}

cmd_test() {
  ensure_venv
  exec python -m pytest "${PROJECT_ROOT}/tests" -v "$@"
}

cmd_meta_analysis() {
  ensure_venv
  # Note (v2 update): self_inspect was split into three scoped tools in
  # migration 015 — self_inspect_config / _activity / _architecture.
  # The v1 prompt referenced a non-existent self_inspect(scope=...) tool.
  local prompt="Run a full meta-analysis of your own configuration and recent activity."
  prompt+=' Use the three scoped introspection tools:'
  prompt+=' - self_inspect_config to review agent roster, tool grants, and paradigm bindings,'
  prompt+=' - self_inspect_activity for conversation stats, sandbox audit, and recent summaries,'
  prompt+=' - self_inspect_architecture to read the README and DB schema before writing any SQL or code.'
  prompt+=' Then produce a structured improvement proposal document in the workspace covering:'
  prompt+=' 1) Agent / tool gap analysis,'
  prompt+=' 2) Paradigm effectiveness observations,'
  prompt+=' 3) Behavioural patterns from recent activity,'
  prompt+=' 4) Concrete SQL proposals with rationale.'
  jean-michel --mode analyse --once "${prompt}"
}

cmd_build_docker() {
  # Usage:
  #   ./jm.sh --build-docker              — build default Python Alpine image
  #   ./jm.sh --build-docker py-alpine    — same, explicit
  #   ./jm.sh --build-docker node-alpine  — build Node Alpine image
  #   ./jm.sh --build-docker all          — build all images
  local variant="${1:-py-alpine}"

  build_one() {
    local tag="$1"
    local dockerfile="$2"
    echo "Building jeanmichel-sandbox:${tag} from ${dockerfile}..."
    docker build -t "jeanmichel-sandbox:${tag}" -f "${PROJECT_ROOT}/docker/sandbox/${dockerfile}" "${PROJECT_ROOT}/docker/sandbox/"
    echo "  → jeanmichel-sandbox:${tag} ready."
  }

  case "${variant}" in
    py-alpine|python)
      build_one "py-alpine" "Dockerfile"
      ;;
    node-alpine|node)
      build_one "node-alpine" "Dockerfile.node"
      ;;
    all)
      build_one "py-alpine"   "Dockerfile"
      build_one "node-alpine" "Dockerfile.node"
      ;;
    *)
      # Legacy: treat the argument as a full image tag for the default Dockerfile.
      echo "Building jeanmichel-sandbox:${variant}..."
      docker build -t "jeanmichel-sandbox:${variant}" "${PROJECT_ROOT}/docker/sandbox/"
      echo "  → jeanmichel-sandbox:${variant} ready."
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMAND="${1:-}"

case "${COMMAND}" in
  --help|-h)
    usage
    ;;
  --install)
    shift
    cmd_install "$@"
    ;;
  --export-db | --backup-db)
    shift
    cmd_export_db "$@"
    ;;
  --browse-db)
    shift
    cmd_browse_db "$@"
    ;;
  --paradigm-matrix)
    shift
    cmd_paradigm_matrix "$@"
    ;;
  --synoptic)
    shift
    cmd_synoptic "$@"
    ;;
  --orchestrator-map)
    shift
    cmd_orchestrator_map "$@"
    ;;
  --inspect-conv)
    shift
    cmd_inspect_conv "$@"
    ;;
  --clean)
    shift
    cmd_clean "$@"
    ;;
  --reap-sandboxes)
    shift
    cmd_reap_sandboxes "$@"
    ;;
  --admin)
    shift
    cmd_admin "$@"
    ;;
  --test)
    shift
    cmd_test "$@"
    ;;
  --build-docker)
    shift
    cmd_build_docker "$@"
    ;;
  --meta-analysis)
    shift
    cmd_meta_analysis "$@"
    ;;
  --serve)
    shift
    cmd_serve "$@"
    ;;
  --create-user)
    shift
    cmd_create_user "$@"
    ;;
  "")
    cmd_cli
    ;;
  *)
    # Unknown flag or positional — pass everything to the CLI
    cmd_cli "$@"
    ;;
esac

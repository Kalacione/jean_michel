#!/usr/bin/env bash
# jm — Jean-Michel unified entry point.
#
# Usage:
#   ./jm.sh                          Launch the interactive CLI (default)
#   ./jm.sh --install                Setup venv and database
#   ./jm.sh --export-db [--out FILE] Export the database to JSON
#   ./jm.sh --browse-db              Open the database in sqlite_web (port 8080)
#   ./jm.sh --inspect-conv ID [...]  Inspect a conversation's artifacts
#   ./jm.sh --help                   Show this help
#
# Extra args after a command are forwarded to the underlying tool.
# Examples:
#   ./jm.sh --export-db --out /tmp/db.json
#   ./jm.sh --inspect-conv abc123 --kind thought response
#   ./jm.sh --inspect-conv abc123 --list

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DB_PATH="${PROJECT_ROOT}/jeanmichel.db"
SCHEMA_PATH="${PROJECT_ROOT}/db/schema.sql"
PYTHON_BIN="${PYTHON_BIN:-python3.14}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: ./jm.sh [COMMAND] [OPTIONS]

Commands:
  (default)              Launch the interactive CLI
  --install              Create venv, install deps, initialize the DB
  --export-db            Export the database to JSON (stdout or --out FILE)
  --browse-db            Open the database in sqlite_web at http://localhost:8080
  --inspect-conv ID      Inspect artifacts of a conversation (by ID prefix)
  --help                 Show this help

CLI pass-through:
  Any extra arguments after the command are forwarded to the underlying tool.

Examples:
  ./jm.sh
  ./jm.sh --install
  ./jm.sh --export-db --out /tmp/db.json
  ./jm.sh --browse-db
  ./jm.sh --inspect-conv abc123
  ./jm.sh --inspect-conv abc123 --agent jean-michel --kind thought response
  ./jm.sh --inspect-conv abc123 --list
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
    echo "[1/3] Creating venv at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  else
    echo "[1/3] venv already exists at ${VENV_DIR}"
  fi

  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  # ---- dependencies -------------------------------------------------------
  echo "[2/3] Installing dependencies"
  pip install --upgrade pip >/dev/null
  pip cache purge >/dev/null 2>&1 || true
  pip install -e ".[dev]"

  # ---- database -----------------------------------------------------------
  echo "[3/3] Initializing SQLite database"
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

  echo
  echo "Done."
}

cmd_cli() {
  ensure_venv
  export JEANMICHEL_HOME="${PROJECT_ROOT}"
  exec jean-michel "$@"
}

cmd_export_db() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/export_db.py" --db "${DB_PATH}" "$@"
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

cmd_inspect_conv() {
  ensure_venv
  exec python "${PROJECT_ROOT}/debug/inspect_conv.py" "$@"
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
  --export-db)
    shift
    cmd_export_db "$@"
    ;;
  --browse-db)
    shift
    cmd_browse_db "$@"
    ;;
  --inspect-conv)
    shift
    cmd_inspect_conv "$@"
    ;;
  "")
    cmd_cli
    ;;
  *)
    # Unknown flag or positional — pass everything to the CLI
    cmd_cli "$@"
    ;;
esac

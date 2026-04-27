#!/usr/bin/env bash
# Jean-Michel — local install script.
# Creates a Python 3.14 venv, installs deps, initializes the SQLite DB.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DB_PATH="${PROJECT_ROOT}/jeanmichel.db"
SCHEMA_PATH="${PROJECT_ROOT}/db/schema.sql"

# ---- Python version check -------------------------------------------------

PYTHON_BIN="${PYTHON_BIN:-python3.14}"
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

# ---- venv -----------------------------------------------------------------

if [ ! -d "${VENV_DIR}" ]; then
  echo "[1/3] Creating venv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "[1/3] venv already exists at ${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# ---- dependencies ---------------------------------------------------------

echo "[2/3] Installing dependencies"
pip install --upgrade pip >/dev/null
pip cache purge >/dev/null 2>&1 || true
pip install -e ".[dev]"

# ---- database -------------------------------------------------------------

echo "[3/3] Initializing SQLite database"
if [ -f "${DB_PATH}" ]; then
  echo "  ${DB_PATH} already exists — skipping schema load."
  echo "  Delete it and re-run if you want a fresh seed."
else
  python -c "
import sqlite3, sys
conn = sqlite3.connect('${DB_PATH}')
with open('${SCHEMA_PATH}') as f:
    conn.executescript(f.read())
conn.close()
print('  Database created at ${DB_PATH}')
"
fi

echo
echo "Done. Activate the venv with:"
echo "  source ${VENV_DIR}/bin/activate"
echo
echo "Tip: browse the SQLite database with:"
echo "  sqlite_web ${DB_PATH}"

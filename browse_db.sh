#!/usr/bin/env bash
# Open the SQLite database in sqlite_web (http://localhost:8080)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DB_PATH="${PROJECT_ROOT}/jeanmichel.db"

if [ ! -f "${DB_PATH}" ]; then
  echo "Error: database not found at ${DB_PATH}" >&2
  echo "Run ./install.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "Opening ${DB_PATH} at http://localhost:8080"
sqlite_web "${DB_PATH}"

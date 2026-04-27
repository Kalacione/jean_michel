#!/usr/bin/env bash
# Jean-Michel — interactive launcher.
# Activates the venv and starts the CLI. Forwards extra args to `jean-michel`.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
  echo "Error: venv not found. Run ./install.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# JEANMICHEL_HOME locks the runtime paths (DB, conversations/, user_profile.toml)
# to the repo root regardless of the current working directory.
export JEANMICHEL_HOME="${PROJECT_ROOT}"

exec jean-michel "$@"

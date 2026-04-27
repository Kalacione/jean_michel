"""Configuration: paths, constants, user_profile loading."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# ---- Paths ----------------------------------------------------------------

REPO_ROOT = Path(os.environ.get("JEANMICHEL_HOME", Path.cwd())).resolve()
DB_PATH = REPO_ROOT / "jeanmichel.db"
CONVERSATIONS_DIR = REPO_ROOT / "conversations"
USER_PROFILE_PATH = REPO_ROOT / "user_profile.toml"

# ---- Runtime constants ----------------------------------------------------

MAX_RECURSION_DEPTH = 5
DEFAULT_OLLAMA_MODEL = os.environ.get("JEANMICHEL_MODEL", "gemma4:latest")
DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ---- User profile ---------------------------------------------------------

@dataclass(frozen=True)
class UserProfile:
    description: str

    @staticmethod
    def load(path: Path = USER_PROFILE_PATH) -> "UserProfile":
        if not path.exists():
            return UserProfile(description="No user profile provided.")
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return UserProfile(description=data.get("description", "").strip() or "No user profile provided.")


def ensure_dirs() -> None:
    """Create runtime directories if missing."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

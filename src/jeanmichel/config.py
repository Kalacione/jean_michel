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

# Hard cap on delegation chain depth. Enforced by the orchestrator.
MAX_RECURSION_DEPTH = 10

# Hard cap on tool-call iterations within a single agent request. Prevents
# tool-loop runaway when an agent gets stuck (e.g. retrying a failing tool).
# 15 allows a router to make 5-6 delegations + follow-up reads without
# exhausting budget on multi-step research tasks.
MAX_STEPS_PER_REQUEST = 15


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Wall-clock timeouts (configurable via env vars).
LLM_CALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_LLM_TIMEOUT", 120)
REQUEST_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_REQUEST_TIMEOUT", 900)
TURN_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_TURN_TIMEOUT", 1800)

# Workspace soft quota per conversation, in bytes.
WORKSPACE_QUOTA_BYTES = 256 * 1024 * 1024  # 256 MB

MODES = ("analyse", "chat", "vocal")
DEFAULT_OLLAMA_MODEL = os.environ.get(
    "JEANMICHEL_MODEL",
    "gemma4:26b",
)
DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ---- User profile ---------------------------------------------------------

@dataclass(frozen=True)
class UserProfile:
    name: str = ""
    birthdate: str = ""
    city: str = ""
    country: str = ""
    language: str = ""
    interests: str = ""
    notes: str = ""

    def render(self) -> str:
        lines = []
        for key in ("name", "birthdate", "city", "country", "language", "interests"):
            val = getattr(self, key)
            if val:
                lines.append(f"{key}: {val}")
        if self.notes:
            if lines:
                lines.append("")
            lines.append(self.notes)
        return "\n".join(lines) if lines else "No user profile provided."

    @staticmethod
    def load(path: Path = USER_PROFILE_PATH) -> UserProfile:
        if not path.exists():
            return UserProfile()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return UserProfile(
            name=data.get("name", "").strip(),
            birthdate=data.get("birthdate", "").strip(),
            city=data.get("city", "").strip(),
            country=data.get("country", "").strip(),
            language=data.get("language", "").strip(),
            interests=data.get("interests", "").strip(),
            notes=data.get("notes", "").strip(),
        )


def ensure_dirs() -> None:
    """Create runtime directories if missing."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

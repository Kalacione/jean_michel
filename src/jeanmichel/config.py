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

# Baseline cap on tool-call iterations within a single agent request.
# Prevents tool-loop runaway when an agent gets stuck. Specialists earn extra
# steps by persisting findings (see WRITE_STEP_BONUS), so this is a floor, not
# a hard ceiling.
MAX_STEPS_PER_REQUEST = 20

# Each successful workspace write (workspace_create_file / workspace_str_replace)
# extends the step budget by this many steps. Rewards real progression and
# discourages info-loops that never persist anything. Capped by MAX_STEP_BONUS.
WRITE_STEP_BONUS = 3
MAX_STEP_BONUS = 15


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Hard cap on total delegations per turn. Prevents "aspirating the whole
# internet" on a single user request.
MAX_DELEGATIONS = _int_env("JEANMICHEL_MAX_DELEGATIONS", 8)

# Wall-clock timeouts (configurable via env vars).
#
# Three nested scopes, checked at every iteration of the orchestrator loop:
#
# - LLM_CALL_TIMEOUT_SECONDS  → one individual `llm.chat()` call. Raised as
#   LLMTimeoutError; the orchestrator yields WallClockExceeded(scope="llm_call")
#   and retries once with a "conclude with what you have" hint (soft recovery)
#   if the turn budget still allows it.
# - REQUEST_WALL_CLOCK_SECONDS → total time spent inside ONE agent request
#   (one agent's step loop, from its first LLM call to return/report_findings).
#   Each delegated child gets its own fresh request budget.
# - TURN_WALL_CLOCK_SECONDS   → total time for the WHOLE user turn, shared by
#   the router and every (recursively delegated) child. The upper safety net.
#
# Hitting any of these triggers a hard cut: the in-flight request is failed,
# a partial report is synthesised from recorded tool calls, and
# WallClockExceeded + OrchestrationFailed are yielded.
#
# To avoid that brutal cut, SOFT_DEADLINE_RATIO (below) fires earlier and
# forces a graceful wrap-up by restricting the tool payload to the agent's
# conclusion tool only.
LLM_CALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_LLM_TIMEOUT", 120)
REQUEST_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_REQUEST_TIMEOUT", 900)
TURN_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_TURN_TIMEOUT", 1800)
# Soft deadline ratio: once elapsed/budget crosses this fraction, the
# orchestrator forces a graceful wrap-up (restrict tools to the agent's
# conclusion tool and inject a "conclude now with partial results" message).
# 0.75 means at 75 % of the wall-clock the agent must conclude with whatever
# it has. Set to 1.0 to disable (hard cut only).
SOFT_DEADLINE_RATIO = float(os.environ.get("JEANMICHEL_SOFT_DEADLINE_RATIO", "0.75"))

# Workspace soft quota per conversation, in bytes.
WORKSPACE_QUOTA_BYTES = 256 * 1024 * 1024  # 256 MB

MODES = ("analyse", "chat", "vocal")
DEFAULT_OLLAMA_MODEL = os.environ.get(
    "JEANMICHEL_MODEL",
    "qwen3:14b",
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

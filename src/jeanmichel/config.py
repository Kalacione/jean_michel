"""Configuration: paths, constants, cli profile loading."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---- Paths ----------------------------------------------------------------

REPO_ROOT = Path(os.environ.get("JEANMICHEL_HOME", Path.cwd())).resolve()
DB_PATH = REPO_ROOT / "jeanmichel.db"
CONVERSATIONS_DIR = REPO_ROOT / "conversations"
# The CLI runs as the reserved `cli` user ; its profile lives in this file.
# Web users keep their profile in the `web_users` columns (migrate_113).
CLI_PROFILE_PATH = REPO_ROOT / "cli_profile.toml"
ENV_FILE_PATH = REPO_ROOT / ".env"

# EXPLICIT global target repo for `code` mode (CLI use). **No silent default**:
# unset ⇒ None ⇒ no repo (a code-mode conversation without an attached project
# repo gets NO worktree). We deliberately do NOT fall back to the jean-michel
# repo itself — that would be a security hole (the system editing its own source
# unintentionally). The web flow attaches a repo per PROJECT (projects.code_repo);
# this env is only for an explicit CLI global. Cf. src/jeanmichel/worktree.py.
_project_root_env = os.environ.get("JEANMICHEL_PROJECT_ROOT", "").strip()
PROJECT_ROOT: Path | None = Path(_project_root_env).resolve() if _project_root_env else None

# Paths (relative to a worktree) that repo_edit / repo_write must NEVER touch,
# even inside an isolated worktree: live DB, secrets, runtime data, vendored/
# generated trees. Enforced as a hard deny in the PreToolUse hook (P1).
REPO_PROTECTED_PATHS = (
    "jeanmichel.db",
    ".env",
    ".api_secret",
    "conversations/",
    "backups/",
    "voice_models/",
    ".venv/",
    ".git/",
)


# ---- .env loader (minimal, no dependency) --------------------------------
#
# Loads `KEY=value` pairs from `.env` at the repo root into `os.environ`.
# Existing shell variables WIN — we never overwrite them. This means a
# `NEWSDATA_API_KEY=…` exported in your shell takes precedence over the
# value in `.env`, which is the principle of least surprise.
#
# Format (intentionally minimal — no python-dotenv dep) :
#   - One `KEY=value` per line.
#   - Blank lines and lines starting with `#` are ignored.
#   - Optional surrounding single or double quotes are stripped.
#   - No interpolation (`$VAR`), no multi-line values, no `export` prefix.
#
# Missing `.env` = no-op. The file is gitignored by default.


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ENV_FILE_PATH)


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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Per-conversation git snapshots (one local repo per conversation, never
# pushed) — enables "rewind to a turn" and "fork a new conversation from a
# point". Opt-in: off by default so existing installs and the test suite are
# untouched. Cf. src/jeanmichel/snapshot.py.
CONVERSATION_SNAPSHOT_ENABLED = _bool_env("JEANMICHEL_CONVERSATION_SNAPSHOT_ENABLED", False)


# `code`-mode git worktree: when enabled, a conversation in `code` mode gets an
# isolated git worktree of PROJECT_ROOT on a dedicated branch (jm/conv-<id>), so
# the system edits real files in place without ever touching the live tree (git
# is the undo). Opt-in: off by default (test suite + non-code installs untouched).
# Cf. src/jeanmichel/worktree.py.
CODE_WORKTREE_ENABLED = _bool_env("JEANMICHEL_CODE_WORKTREE_ENABLED", False)

# repo_test (code mode): command run IN the worktree to exercise the project's
# own tests. Default is EMPTY = AUTO-DETECT (cf. tools/repo_test.py): prefer
# <PROJECT_ROOT>/.venv/bin/python, else the interpreter running jean-michel
# (sys.executable) — both ship pytest in the common cases, so the dogfood needs
# NO config. Set this only to force a different runner (other stack / non-pytest).
# A relative path-like first token (e.g. ".venv/bin/python") is resolved against
# PROJECT_ROOT; the worktree's src/ is prepended to PYTHONPATH so tests exercise
# the EDITED code, not the live install.
REPO_TEST_CMD = os.environ.get("JEANMICHEL_REPO_TEST_CMD", "")
REPO_TEST_TIMEOUT = _int_env("JEANMICHEL_REPO_TEST_TIMEOUT", 300)


# MCP (Model Context Protocol) client — connect to hosted MCP servers and
# expose their tools to agents natively. Enabled when `mcp_servers.toml` exists
# and lists ≥1 server (copy mcp_servers.example.toml). MCP_DISABLED is a
# force-off kill-switch. Cf. src/jeanmichel/mcp_client.py.
MCP_SERVERS_PATH = REPO_ROOT / "mcp_servers.toml"
MCP_DISABLED = _bool_env("JEANMICHEL_MCP_DISABLED", False)
MCP_CALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_MCP_CALL_TIMEOUT", 25)
MCP_MAX_TOOLS_PER_SERVER = _int_env("JEANMICHEL_MCP_MAX_TOOLS_PER_SERVER", 30)


# Hard cap on total delegations per turn. Prevents "aspirating the whole
# internet" on a single user request.
MAX_DELEGATIONS = _int_env("JEANMICHEL_MAX_DELEGATIONS", 8)

# Hard cap on distinct search-tool calls within a single specialist request.
# Counts web_search, wikipedia_search, wikipedia_fetch, wikipedia_get_page.
# When reached the agent is restricted to its conclusion tool (report_findings)
# so it must synthesize what it already has instead of keep searching.
MAX_SEARCH_CALLS_PER_REQUEST = _int_env("JEANMICHEL_MAX_SEARCH_CALLS", 10)

# Wall-clock timeouts (configurable via env vars).
#
# Three nested scopes, checked at every iteration of the orchestrator loop:
#
# - LLM_STALL_TIMEOUT_SECONDS → max seconds WITHOUT a new streamed token before we
#   declare the model hung ("aux fraises"). This is the PRIMARY guard : a model that
#   genuinely works keeps emitting tokens (resets the stall), so a slow-but-productive
#   call on old hardware runs as long as it needs. Only a true stall trips it.
# - LLM_CALL_TIMEOUT_SECONDS  → hard-cap backstop on one streamed `llm.chat()` call.
#   Set LARGE : qwen-coder on this old GPU is legitimately slow ; we never want to cut
#   a productive generation. Only bounds a degenerate that trickles just enough to
#   dodge the stall guard. Raised as LLMTimeoutError; the orchestrator yields
#   WallClockExceeded(scope="llm_call") and retries once with a "conclude with what you
#   have" hint (soft recovery) if the turn budget still allows it.
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
LLM_STALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_LLM_STALL_TIMEOUT", 60)
LLM_CALL_TIMEOUT_SECONDS = _int_env("JEANMICHEL_LLM_TIMEOUT", 600)
REQUEST_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_REQUEST_TIMEOUT", 1800)
TURN_WALL_CLOCK_SECONDS = _int_env("JEANMICHEL_TURN_TIMEOUT", 3600)
# Soft deadline ratio: once elapsed/budget crosses this fraction, the
# orchestrator forces a graceful wrap-up (restrict tools to the agent's
# conclusion tool and inject a "conclude now with partial results" message).
# 0.75 means at 75 % of the wall-clock the agent must conclude with whatever
# it has. Set to 1.0 to disable (hard cut only).
SOFT_DEADLINE_RATIO = float(os.environ.get("JEANMICHEL_SOFT_DEADLINE_RATIO", "0.75"))

# Workspace soft quota per conversation, in bytes.
WORKSPACE_QUOTA_BYTES = 256 * 1024 * 1024  # 256 MB

# Max size of a single file uploaded through the web workspace, in bytes.
# Overridable with JEANMICHEL_UPLOAD_MAX_BYTES (raw bytes). The per-conversation
# WORKSPACE_QUOTA_BYTES still caps the cumulative total.
WORKSPACE_UPLOAD_MAX_BYTES = _int_env("JEANMICHEL_UPLOAD_MAX_BYTES", 22 * 1024 * 1024)  # 22 MB

# Longest-side cap (px) for the normalized image derivative — used BOTH for the
# inline thumbnail and as the format-safe, bandwidth-bounded input fed to Gemma
# vision. One size, one derivative (cf. DevNotes/WEBUI/03). Env-overridable.
IMAGE_MAX_PX = _int_env("JEANMICHEL_IMAGE_MAX_PX", 1024)

MODES = ("analyse", "chat", "vocal", "code")
DEFAULT_OLLAMA_MODEL = os.environ.get(
    "JEANMICHEL_MODEL",
    #"qwen3:14b",
    "gemma4:26b",
)
DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ---- models.toml — foyer unique de la config modèle ----------------------
#
# TOUTE la config modèle vit ici : rôles (dispatch/main/code/…), fenêtres de
# contexte, et voice. Les défauts génériques sont dans `models.example.toml`
# (committé) ; `models.toml` (gitignored) override LOCALEMENT, par clé (merge,
# pas tout-ou-rien : un models.toml partiel n'écrase que ce qu'il déclare). Une
# env var gagne toujours quand elle est présente (test rapide d'un modèle, CI).
MODELS_CONFIG_PATH = REPO_ROOT / "models.toml"
MODELS_EXAMPLE_PATH = REPO_ROOT / "models.example.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_models_config() -> dict[str, Any]:
    """Exemple (défauts) comme base, models.toml local par-dessus (merge 1 niveau)."""
    base = _read_toml(MODELS_EXAMPLE_PATH)
    over = _read_toml(MODELS_CONFIG_PATH)
    merged = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


_MODELS_CONFIG = _load_models_config()


def _models_section(name: str) -> dict[str, Any]:
    sec = _MODELS_CONFIG.get(name)
    return sec if isinstance(sec, dict) else {}


def _role_model(env_name: str, toml_key: str, default: str) -> str:
    """Résout un modèle de rôle : env → models.toml [roles] → ultime défaut.

    Le défaut intégré n'est qu'un filet de sécurité (models.example.toml absent
    ou corrompu) — la source réelle des défauts est le fichier exemple.
    """
    env = os.environ.get(env_name)
    if env is not None and env.strip():
        return env.strip()
    val = _models_section("roles").get(toml_key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return default


# =============================================================================
# v2 — paramètres de la nouvelle architecture (cf. DevNotes/REVOLUCION/06)
# =============================================================================
# Les noms évitent les collisions avec les constantes v1 (MAX_RECURSION_DEPTH,
# MAX_SEARCH_CALLS_PER_REQUEST, TURN_WALL_CLOCK_SECONDS) qui restent en place
# pour la rétrocompat tant que le code legacy n'est pas retiré (Phase 6).
# Tous ces paramètres sont overridables par env var pour tuning sans recompile.

# Modèles — 5 slots (cf. §1.3 doc 06). Chaîne d'override : env > models.toml > default.
DISPATCH_MODEL = _role_model("JEANMICHEL_DISPATCH_MODEL", "dispatch", "granite4.1:8b")
MAIN_MODEL = _role_model("JEANMICHEL_MAIN_MODEL", "main", "cogito:32b")
COMPACTOR_MODEL = _role_model("JEANMICHEL_COMPACTOR_MODEL", "compactor", "gemma4:26b")
SUBAGENT_DEFAULT_MODEL = _role_model("JEANMICHEL_SUBAGENT_MODEL", "subagent", "gemma4:26b")
# Router (jean-michel) model used in the `code` interaction mode — a stronger
# model for methodical decomposition over a codebase. Other modes use the agent
# default (MAIN_MODEL / gemma4, vision-capable). Env-overridable.
CODE_MODEL = _role_model("JEANMICHEL_CODE_MODEL", "code", "qwen3:14b")
# Per-mode router-model overrides (mode → Ollama model). A mode absent here uses
# the agent's resolved default. Consumed in service.turn_runner._run_deep_turn.
MODE_ROUTER_MODEL = {"code": CODE_MODEL}
# Slot dédié aux agents dont le métier EST le raisonnement (strategist,
# critical-thinker, comparator-specialist, meta-analyst). Aujourd'hui chacun
# pointe dur sur 'gemma4:26b' via `agents.model_override` ; ce slot existe
# pour qu'un futur switch global (changer de modèle de raisonnement) se fasse
# par env var, sans migration DB. Le résolveur actuel (orchestrator_v2) ne le
# lit pas encore — il sera consommé si on bascule de model_override sur un
# flag d'agent (cognitive_tier='high', par exemple). Documenté ici comme
# point d'extension stable.
REASONER_MODEL = _role_model("JEANMICHEL_REASONER_MODEL", "reasoner", "gemma4:26b")

# Budget de contexte partitionné (cf. §1.7 et §7 doc 06).
# 4 seuils d'escalade pour la compaction sur le WORKING : snip / microcompact /
# context_collapse / autocompact.
COMPACTION_THRESHOLDS = (0.70, 0.80, 0.90, 0.95)
# Ratio du contexte total réservé pour la réponse finale (cf. §1.7 et §12 doc 06).
# 15 % parce que les outputs longs sont persistés au workspace, la réponse
# finale est toujours un résumé court.
OUTPUT_RESERVE_RATIO = float(os.environ.get("JEANMICHEL_OUTPUT_RESERVE_RATIO", "0.15"))
# Seuil au-dessus duquel un tool result devient candidat à la microcompaction
# (cf. §7 doc 06). ~1500 tokens ≈ 6000 chars ≈ une page de prose.
MICROCOMPACT_TOKEN_THRESHOLD = _int_env("JEANMICHEL_MICROCOMPACT_THRESHOLD", 1500)

# Garde-fous structurels v2 (cf. §8 doc 06).
MAX_DEPTH = _int_env("JEANMICHEL_MAX_DEPTH", 5)
MAX_SEARCH_CALLS_PER_TURN = _int_env("JEANMICHEL_MAX_SEARCH_TURN", 10)
WALL_CLOCK_TURN_SECONDS = _int_env("JEANMICHEL_TURN_WALL_CLOCK", 900)
# Max time the web daemon blocks a turn waiting for a human reply to an
# ask_human prompt before giving up and letting the orchestrator conclude (S4).
ASK_HUMAN_TIMEOUT_SECONDS = _int_env("JEANMICHEL_ASK_HUMAN_TIMEOUT", 300)

# Mémoire long-terme (cf. §10 doc 06). Seuil d'alerte sur la mémoire user.
USER_MEMORY_INDEX_LIMIT = _int_env("JEANMICHEL_USER_MEMORY_LIMIT", 100)
USER_MEMORY_WARN_AT = _int_env("JEANMICHEL_USER_MEMORY_WARN_AT", 90)

# Caps d'injection par scope dans le system prompt (index uniquement, jamais le
# content). Bornent le budget de contexte ; l'inclusion reste déterministe.
MEMORY_WORLD_CAP = _int_env("JEANMICHEL_MEMORY_WORLD_CAP", 20)
MEMORY_USER_CAP = _int_env("JEANMICHEL_MEMORY_USER_CAP", 40)
MEMORY_PROJECT_CAP = _int_env("JEANMICHEL_MEMORY_PROJECT_CAP", 30)
MEMORY_TOOL_CAP_PER_TOOL = _int_env("JEANMICHEL_MEMORY_TOOL_CAP", 5)

# Ratio du budget WORKING du parent alloué à un subagent par défaut.
# 40 % par défaut, à tuner empiriquement (§12 doc 06).
SUBAGENT_BUDGET_RATIO = float(os.environ.get("JEANMICHEL_SUBAGENT_BUDGET_RATIO", "0.40"))

# Fenêtre de contexte par modèle (en tokens). Utilisée pour calculer
# SYSTEM_RESERVE + WORKING + OUTPUT_RESERVE, et ÉPINGLÉE comme `num_ctx` Ollama.
# La valeur = le BESOIN RÉEL du modèle dans son rôle, PAS son contexte natif max :
# granite (dispatcher, natif 128k) n'a besoin que de ~8k, qwen3-coder (code, natif
# 256k) a besoin de 128k. On épingle num_ctx = cette valeur ; un KV cache 128k sur
# un 8B ≈ 54 GB → OOM sur 2×32 GB quand on chaîne les modèles. D'où le plafond dur.
#
# Résolution (cf. model_context_window) : env `JEANMICHEL_CTX_WINDOW_<slug>` →
# `models.toml [context_window]` → défaut ; puis min(plafond). Les valeurs par
# modèle (générique) vivent dans models.example.toml, pas en dur ici.
MODEL_CONTEXT_CEILING = _int_env("JEANMICHEL_CTX_CEILING", 128_000)
DEFAULT_MODEL_CONTEXT_WINDOW = _int_env("JEANMICHEL_DEFAULT_CTX_WINDOW", 32_768)

# How long Ollama keeps a model resident after a call. We CHAIN different models
# within one turn (dispatch → router → analyst → coder), so the old hardcoded "30m"
# pinned every one for 30 min → VRAM pile-up (a 256K-ctx coder alone is ~45 GB), and
# left them resident long after the turn. Short by default (éco) : nothing sits idle
# in VRAM for nothing, it reloads on demand. The client ALSO evicts the previous
# model the moment it switches to a different one (cf. llm._maybe_evict_on_switch).
# Override via env ("0" = unload right after every call, "30m" = keep hot).
OLLAMA_KEEP_ALIVE = os.environ.get("JEANMICHEL_OLLAMA_KEEP_ALIVE", "30s")

# Where the streamed LLM output ("slop") is dumped, one .jsonl per call, for debug
# and post-mortem analysis of slow/looping generations. Empty/"none" disables it.
LLM_STREAM_DIR = os.environ.get("JEANMICHEL_LLM_STREAM_DIR", str(REPO_ROOT / "llm_streams"))

# Daemon logging : rotating file (+ stderr) configured at `run()`. The daemon used
# to log nowhere persistent (terminal only) → silent hangs were undiagnosable.
LOG_DIR = Path(os.environ.get("JEANMICHEL_LOG_DIR", str(REPO_ROOT / "logs")))
LOG_LEVEL = os.environ.get("JEANMICHEL_LOG_LEVEL", "INFO").upper()


def _ctx_ceiling() -> int:
    raw = _MODELS_CONFIG.get("ceiling")
    return int(raw) if isinstance(raw, int) and raw > 0 else MODEL_CONTEXT_CEILING


def _ctx_default() -> int:
    raw = _MODELS_CONFIG.get("default")
    return int(raw) if isinstance(raw, int) and raw > 0 else DEFAULT_MODEL_CONTEXT_WINDOW


def model_context_window(model: str) -> int:
    """Context window (tokens) pinned as Ollama `num_ctx` for a given model.

    Resolution, most specific first, then capped at the ceiling :
      1. env `JEANMICHEL_CTX_WINDOW_<slug>` (slug = non-alnum → '_'),
      2. models.toml/example `[context_window]` (exact model name),
      3. `default` (models config) / `DEFAULT_MODEL_CONTEXT_WINDOW`.
    The cap (`MODEL_CONTEXT_CEILING`, 128k) keeps a single model's KV cache from
    blowing VRAM (a 256k window on a coder ≈ 45 GB) regardless of the source.
    """
    slug = "".join(c if c.isalnum() else "_" for c in model)
    env = os.environ.get(f"JEANMICHEL_CTX_WINDOW_{slug}")
    if env is not None:
        try:
            resolved = max(1, int(env))
        except ValueError:
            resolved = _ctx_default()
    else:
        table = _models_section("context_window")
        resolved = table[model] if isinstance(table.get(model), int) and table[model] > 0 else _ctx_default()
    return min(resolved, _ctx_ceiling())


# Audit sandbox cross-conversation : fichier JSONL global (cf. §6 bis doc 06).
SANDBOX_AUDIT_LOG = Path(
    os.environ.get(
        "JEANMICHEL_SANDBOX_AUDIT_LOG",
        str(Path.home() / ".jean-michel" / "sandbox_audit.jsonl"),
    )
)

# Vocal mode — Piper TTS model. Resolved like the model roles : env
# `JEANMICHEL_VOICE_MODEL` → models config `[voice].model` → `voice_models/
# default.onnx`. The matching .onnx.json is auto-discovered by Piper ; vocal mode
# degrades gracefully (text-only) when the model file doesn't resolve.
def _voice_setting(env_name: str, toml_key: str, default: str) -> str:
    env = os.environ.get(env_name)
    if env is not None and env.strip():
        return env.strip()
    val = _models_section("voice").get(toml_key)
    return val.strip() if isinstance(val, str) and val.strip() else default


VOICE_MODEL_PATH = Path(
    _voice_setting("JEANMICHEL_VOICE_MODEL", "model", str(REPO_ROOT / "voice_models" / "default.onnx"))
)
# Audio player command to feed the synthesised WAV. Empty = auto-detect
# in this order : paplay → aplay → ffplay.
VOICE_AUDIO_PLAYER = _voice_setting("JEANMICHEL_AUDIO_PLAYER", "audio_player", "")


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
    def load(path: Path = CLI_PROFILE_PATH) -> UserProfile:
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

    @staticmethod
    def from_row(row: Any) -> UserProfile:
        """Build a profile from a ``web_users`` row (sqlite3.Row / mapping).

        Used for web users (their profile lives in DB columns). ``None`` → empty.
        """
        if row is None:
            return UserProfile()

        def _get(key: str) -> str:
            try:
                return (row[key] or "").strip()
            except (KeyError, IndexError, TypeError):
                return ""

        return UserProfile(
            name=_get("name"),
            birthdate=_get("birthdate"),
            city=_get("city"),
            country=_get("country"),
            language=_get("language"),
            interests=_get("interests"),
            notes=_get("notes"),
        )


def ensure_dirs() -> None:
    """Create runtime directories if missing."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

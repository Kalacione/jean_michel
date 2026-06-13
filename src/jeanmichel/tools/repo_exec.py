"""Tool: repo_exec — run a command in the PROJECT SANDBOX (the repo, confined).

The "project sandbox" is a per-conversation Docker container that mounts the repo
worktree at ``/app`` and runs OFFLINE (``--network=none``) as the host uid, with
dropped capabilities and memory/cpu caps. Unlike ``bash_sandbox`` — which mounts
ONLY the scratch workspace and exists to run generated/throwaway code — ``repo_exec``
runs commands AGAINST the attached repo: build, lint, run a script, move/rename/
delete files, etc.

No per-command allowlist: the confinement IS the container (no network, no host
home/keys, only the repo mounted), and the worktree is git-isolated and disposable
— a destructive command stays inside the checkout. Git history/status/diff is read
read-only on the HOST via ``repo_git``; ``repo_exec`` is for what must RUN.

The image is the agent's ``sandbox_image`` (py-alpine / node-alpine today),
falling back to the shared default. A per-PROJECT image (built from the project's
own Dockerfile) is a later step — this tool already accepts whatever image it is
given, so that upgrade is transparent.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path

from .. import worktree
from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from .bash_sandbox import _DEFAULT_SANDBOX_IMAGE, _container_running

_log = logging.getLogger(__name__)

_EXEC_TIMEOUT_S = 300
_BUILD_TIMEOUT_S = 600
_MAX_OUTPUT_BYTES = 16_000
_MOUNT = "/app"
_PROJECT_DOCKERFILE = ".jm/Dockerfile"


def _container_name(conv_id: str) -> str:
    return f"jm-repo-{conv_id}"


def _resolve_image(conv_folder: Path, default_image: str) -> str:
    """Per-project image from ``<source_repo>/.jm/Dockerfile`` — the OWNER's
    committed Dockerfile (read from the source repo, NOT the agent-editable
    worktree). Built on demand, tagged by content hash (so it rebuilds only when
    the Dockerfile changes). Falls back to ``default_image`` when there is no
    Dockerfile or the build fails (graceful degradation, never raises)."""
    src = worktree.source_repo(conv_folder)
    if src is None:
        return default_image
    dockerfile = src / _PROJECT_DOCKERFILE
    if not dockerfile.is_file():
        return default_image
    try:
        digest = hashlib.sha1(dockerfile.read_bytes()).hexdigest()[:12]
    except OSError:
        return default_image
    tag = f"jeanmichel-sandbox:project-{digest}"
    # Already built for this Dockerfile content ?
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
        return tag
    # Build (network is allowed AT BUILD only; the run is --network=none). The
    # context is the source repo so the Dockerfile can COPY requirements/lockfiles.
    try:
        r = subprocess.run(
            ["docker", "build", "-t", tag, "-f", str(dockerfile), str(src)],
            capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("project image build failed (%s) — falling back to %s", exc, default_image)
        return default_image
    if r.returncode != 0:
        _log.warning("project image build failed (exit %s) — falling back to %s", r.returncode, default_image)
        return default_image
    return tag


def _start_repo_container(name: str, worktree: Path, image: str) -> None:
    """Start the project sandbox: the repo worktree mounted at /app, offline,
    as the host uid, capabilities dropped, resource-capped."""
    current_user = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "--network=none",
            "--cap-drop=ALL",
            "--memory=1g",
            "--cpus=2",
            "--user", current_user,
            "-v", f"{worktree}:{_MOUNT}:rw",
            "-w", _MOUNT,
            image,
            "tail", "-f", "/dev/null",
        ],
        check=True,
        capture_output=True,
    )


def make_spec(conv_folder: Path, conv_id: str = "", image: str | None = None) -> ToolSpec:
    """Return a ToolSpec bound to this conversation's repo worktree."""
    root = _repo.worktree_root(conv_folder)
    container = _container_name(conv_id or Path(conv_folder).name)
    img = image or _DEFAULT_SANDBOX_IMAGE

    def _handler(command: str) -> str:
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if not (command or "").strip():
            return tool_error("empty_command", "command is required.")
        if not _container_running(container):
            chosen = _resolve_image(conv_folder, img)
            try:
                _start_repo_container(container, root, chosen)
            except subprocess.CalledProcessError as e:
                return tool_error(
                    "sandbox_start_failed",
                    f"could not start project sandbox: {e.stderr}", exit_code=None,
                )
            except (OSError, subprocess.SubprocessError) as e:
                return tool_error("sandbox_start_failed", f"could not start project sandbox: {e}", exit_code=None)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["docker", "exec", container, "bash", "-lc", command],
                capture_output=True, text=True, timeout=_EXEC_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return tool_error("sandbox_timeout", f"command timed out after {_EXEC_TIMEOUT_S}s.", exit_code=None)
        except (OSError, subprocess.SubprocessError) as e:
            return tool_error("sandbox_failed", f"docker exec failed: {e}", exit_code=None)
        dur = int((time.monotonic() - start) * 1000)
        out, err = proc.stdout or "", proc.stderr or ""
        truncated = False
        if len(out.encode()) > _MAX_OUTPUT_BYTES:
            out = out.encode()[:_MAX_OUTPUT_BYTES].decode(errors="replace")
            truncated = True
        return tool_ok(
            f"exec exit={proc.returncode} ({dur}ms)" + (" [out truncated]" if truncated else ""),
            exit_code=proc.returncode, stdout=out, stderr=err, duration_ms=dur, truncated=truncated,
        )

    return ToolSpec(
        name="repo_exec",
        description=(
            "SIGNATURE: repo_exec(command). "
            "Run a shell command in the PROJECT SANDBOX: a per-conversation container that mounts the "
            "attached repo at /app, OFFLINE and confined (no network, no host access). Use it to RUN things "
            "against the repo — build, lint, run a script, move/rename/delete files. NOT for reading/searching "
            "(use repo_read/grep/glob) nor git history (use repo_git) — and never bash_sandbox for the repo "
            "(it cannot see it). Returns exit_code, stdout, stderr."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run in the repo (cwd=/app)."},
            },
            "required": ["command"],
        },
        handler=_handler,
    )

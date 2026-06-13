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

The image comes from the PROJECT's Dockerfile (configured in the project settings,
stored in ``projects.dockerfile``, threaded in here): built once, tagged by content
hash, run offline. An empty Dockerfile ⇒ the shared ``repo-default`` image
(alpine + bash + git). The same builder serves the eager build at project-save
(``service/project.py``) and this lazy build (safety net).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .. import worktree
from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import workspace_root_for
from .bash_sandbox import _container_running

_log = logging.getLogger(__name__)

_EXEC_TIMEOUT_S = 300
_BUILD_TIMEOUT_S = 600
_MAX_OUTPUT_BYTES = 16_000
_MOUNT = "/app"
_REPO_DEFAULT_IMAGE = "jeanmichel-sandbox:repo-default"

# Footgun tripwire. NOT the security boundary — the container is (network=none,
# only /app mounted, no host access). These just refuse a few obviously
# catastrophic commands before they waste a sandbox round.
_DANGEROUS = (
    re.compile(r"\brm\s+-\w*[rf]\w*\s+(-\w+\s+)*(/|/\*|~|~/\*|\$HOME)(\s|$)"),
    re.compile(r":\(\)\s*\{.*\}\s*;\s*:"),          # fork bomb
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/"),
    re.compile(r">\s*/dev/(sd|nvme|hd|mapper)"),
)


def project_image_tag(project_id: int | None, dockerfile_content: str) -> str:
    """Deterministic image tag for a project's Dockerfile: keyed by project id
    (no cross-project collision) + content hash (rebuild when the content changes).
    Shared by the eager (save) and lazy (_resolve_image) build paths."""
    digest = hashlib.sha1((dockerfile_content or "").encode("utf-8")).hexdigest()[:12]
    pid = project_id if project_id is not None else "x"
    return f"jeanmichel-sandbox:project-{pid}-{digest}"


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def build_image(dockerfile_content: str, context_dir: Path, tag: str) -> tuple[bool, str]:
    """Build ``tag`` from inline Dockerfile content, with ``context_dir`` as the
    build context (network allowed AT BUILD only; the run is --network=none).
    Returns ``(ok, error_tail)``. Never raises."""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as tf:
            tf.write(dockerfile_content)
            df_path = tf.name
    except OSError as e:
        return False, f"could not write Dockerfile: {e}"
    try:
        r = subprocess.run(
            ["docker", "build", "-t", tag, "-f", df_path, str(context_dir)],
            capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:600]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(df_path)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout or "").splitlines()[-15:])
        return False, tail[:600]
    return True, ""


def _container_name(conv_id: str) -> str:
    return f"jm-repo-{conv_id}"


def _resolve_image(conv_folder: Path, project_id: int | None, dockerfile: str) -> str:
    """The image to run the project sandbox in. Empty Dockerfile ⇒ the shared
    ``repo-default`` (bash+git). Otherwise the per-project image (``project-<pid>-
    <hash>``), built on demand if absent (context = the source repo), falling back
    to ``repo-default`` on build failure. Never raises."""
    if not (dockerfile or "").strip():
        return _REPO_DEFAULT_IMAGE
    tag = project_image_tag(project_id, dockerfile)
    if _image_exists(tag):
        return tag
    context = worktree.source_repo(conv_folder)
    if context is None:
        return _REPO_DEFAULT_IMAGE
    ok, err = build_image(dockerfile, context, tag)
    if not ok:
        _log.warning("project image build failed — falling back to repo-default: %s", err)
        return _REPO_DEFAULT_IMAGE
    return tag


def _start_repo_container(name: str, worktree_path: Path, workspace_path: Path, image: str) -> None:
    """Start the project sandbox: the repo at /app (cwd, the target) AND the
    conversation scratch at /workspace (action scripts + outputs that must not
    pollute the repo). Offline, host uid, capabilities dropped, resource-capped."""
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
            "-v", f"{worktree_path}:{_MOUNT}:rw",       # /app = the repo (cwd, read/modify target)
            "-v", f"{workspace_path}:/workspace:rw",     # /workspace = scratch (action scripts + outputs)
            "-w", _MOUNT,
            image,
            "tail", "-f", "/dev/null",
        ],
        check=True,
        capture_output=True,
    )


def make_spec(
    conv_folder: Path, conv_id: str = "", project_id: int | None = None, dockerfile: str = "",
) -> ToolSpec:
    """Return a ToolSpec bound to this conversation's repo worktree + the project's
    Dockerfile (from the project settings; empty ⇒ repo-default image)."""
    root = _repo.worktree_root(conv_folder)
    ws_root = workspace_root_for(conv_folder)  # mounted alongside the repo for action scripts
    container = _container_name(conv_id or Path(conv_folder).name)

    def _handler(command: str) -> str:
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if not (command or "").strip():
            return tool_error("empty_command", "command is required.")
        if any(p.search(command) for p in _DANGEROUS):
            return tool_error(
                "dangerous_command",
                "Refused: this matches a blocked destructive pattern. The project sandbox already "
                "confines commands to /app (offline, no host access) — scope your command to the repo.",
            )
        if not _container_running(container):
            chosen = _resolve_image(conv_folder, project_id, dockerfile)
            try:
                _start_repo_container(container, root, ws_root, chosen)
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

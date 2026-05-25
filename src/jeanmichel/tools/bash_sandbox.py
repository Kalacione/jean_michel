"""Tool: bash_sandbox — run shell commands inside an isolated Docker container.

The container is named jm-sandbox-{conv_id}, one per conversation.
It is started lazily on first call and persists across turns.
The conversation workspace/ folder is mounted read-write at /workspace.

Security guarantees (enforced at container start):
- --network=none         No internet access
- --cap-drop=ALL         No Linux capabilities
- --memory=512m          Memory limit
- --cpus=1               CPU limit
- Non-root user (sandbox, uid=1000)

Per-agent grant checks (two layers):
1. The orchestrator only includes bash_sandbox in the tools payload if the
   agent has the 'bash_sandbox' grant in agent_tools (enforced before this
   handler is ever called).
2. This handler checks that the first word of the command is in the agent's
   sandbox_grants list — a defense-in-depth check.

Audit: every execution attempt (including refused ones) is recorded in the
sandbox_executions table via db.record_sandbox_execution.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..db import connect as db_connect
from ..db import record_sandbox_execution
from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import workspace_root_for

# Default image tag — used when the agent has no sandbox_image configured.
# Must match what ./jm.sh --build-docker produces.
_DEFAULT_SANDBOX_IMAGE = "jeanmichel-sandbox:py-alpine"
_SANDBOX_TIMEOUT_S = 30
_MAX_OUTPUT_BYTES = 50_000


def _container_name(conv_id: str) -> str:
    return f"jm-sandbox-{conv_id}"


def _container_running(name: str) -> bool:
    """Return True if a container with this name is running."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _start_container(name: str, workspace_path: Path, image: str) -> None:
    """Start the sandbox container, mounting workspace_path at /workspace.

    Run as the current process's uid:gid so the container can write to the
    host-mounted workspace without requiring world-writable permissions.
    """
    current_user = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "--network=none",
            "--cap-drop=ALL",
            "--memory=512m",
            "--cpus=1",
            "--user", current_user,
            "-v", f"{workspace_path}:/workspace:rw",
            "-w", "/workspace",
            image,
            "tail", "-f", "/dev/null",
        ],
        check=True,
        capture_output=True,
    )


def make_spec(
    conv_folder: Path,
    conv_id: str,
    request_id_provider: Callable[[], str],
    sandbox_grants: list[str],
    sandbox_image: str | None = None,
) -> ToolSpec:
    """Return a ToolSpec bound to this conversation.

    Args:
        conv_folder: The conversation folder path.
        conv_id: The 12-char conversation ID (used to name the container).
        request_id_provider: Callable returning the current request_id (injected
            by the orchestrator so the tool can record the audit row).
        sandbox_grants: List of authorized command names for this agent.
        sandbox_image: Docker image tag to use. Defaults to _DEFAULT_SANDBOX_IMAGE.
    """
    ws_root = workspace_root_for(conv_folder)
    container_name = _container_name(conv_id)
    image = sandbox_image or _DEFAULT_SANDBOX_IMAGE

    def _handler(command: str) -> str:
        request_id = request_id_provider()

        # Check that the first word of the command is in the granted list.
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word not in sandbox_grants:
            with db_connect() as conn:
                record_sandbox_execution(conn, request_id, command, None, 0)
            return tool_error(
                "command_not_allowed",
                (
                    f"Command '{first_word}' is not in the allowed list for this agent. "
                    f"Allowed: {sorted(sandbox_grants)}"
                ),
                exit_code=None,
            )

        # Ensure container is running.
        if not _container_running(container_name):
            try:
                _start_container(container_name, ws_root, image)
            except subprocess.CalledProcessError as e:
                return tool_error(
                    "sandbox_start_failed",
                    f"Failed to start sandbox container: {e.stderr}",
                    exit_code=None,
                )

        # Execute the command.
        start = time.monotonic()
        try:
            result = subprocess.run(
                ["docker", "exec", container_name, "bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=_SANDBOX_TIMEOUT_S,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            stdout = result.stdout
            stderr = result.stderr
            truncated = False
            if len(stdout.encode()) > _MAX_OUTPUT_BYTES:
                stdout = stdout.encode()[:_MAX_OUTPUT_BYTES].decode(errors="replace")
                truncated = True
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            duration_ms = _SANDBOX_TIMEOUT_S * 1000
            with db_connect() as conn:
                record_sandbox_execution(conn, request_id, command, None, duration_ms)
            return tool_error(
                "sandbox_timeout",
                f"Command timed out after {_SANDBOX_TIMEOUT_S}s.",
                exit_code=None,
            )

        with db_connect() as conn:
            record_sandbox_execution(conn, request_id, command, exit_code, duration_ms)

        return tool_ok(
            f"{first_word!r} exit={exit_code} ({duration_ms}ms)"
            + (" [out truncated]" if truncated else ""),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    return ToolSpec(
        name="bash_sandbox",
        description=(
            "Run a shell command inside an isolated Docker sandbox. "
            "The sandbox has no network access. "
            "The /workspace directory is shared with the conversation workspace. "
            "Only pre-authorized commands are allowed (first word checked against grants). "
            f"Timeout: {_SANDBOX_TIMEOUT_S}s."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to execute. Must start with an authorized binary. "
                        "Example: 'python3 -c \"print(1+1)\"'"
                    ),
                },
            },
            "required": ["command"],
        },
        handler=_handler,
    )

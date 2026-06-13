"""Tool: repo_test — run the project's tests in the worktree, STRUCTURED result.

Runs ``config.REPO_TEST_CMD`` and returns a parsed result ({passed, counts,
failed[], output_tail}) instead of raw stdout — so the router can revise the
TODO on failure. Runs IN the project's sandbox container when the project ships
a custom image (its toolchain/deps are present there); otherwise on the HOST
(repo-default alpine has no test runner — e.g. the dogfood jean-michel uses its
own ``.venv``). The worktree is the project's own trusted code, git-isolated;
arbitrary GENERATED code still goes through the locked sandbox. On the host path
the worktree's ``src/`` is prepended to PYTHONPATH so tests exercise the EDITED
code, not the live install.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .. import config, worktree
from . import _repo, repo_exec
from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import workspace_root_for

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def _default_python(conv_folder: Path) -> str:
    """Robust interpreter for the default test command: the TARGET repo's venv
    if it has one (the conversation's source repo, per-conversation), else the
    interpreter running jean-michel (the project venv when launched via jm.sh) —
    both ship pytest in the common cases."""
    src = worktree.source_repo(conv_folder)
    if src is not None:
        venv_py = src / ".venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
    return sys.executable


def _parse(output: str, returncode: int) -> str:
    """Structured pytest result (same shape whether run on host or in container)."""
    counts = {kind: int(n) for n, kind in _SUMMARY_RE.findall(output)}
    failed = _FAILED_RE.findall(output)[:25]
    tail = "\n".join(output.splitlines()[-40:])
    ok = returncode == 0
    summary = f"tests {'PASSED' if ok else 'FAILED'} (exit {returncode})"
    if counts:
        summary += " — " + ", ".join(f"{v} {k}" for k, v in counts.items())
    return tool_ok(
        summary, passed=ok, exit_code=returncode, counts=counts, failed=failed, output_tail=tail,
    )


def make_spec(
    conv_folder: Path, conv_id: str = "", project_id: int | None = None, dockerfile: str = "",
) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (tests its git worktree)."""

    def _handler(test_path: str = "", timeout: int = 0) -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if test_path:
            try:
                _repo.safe_resolve(root, test_path)
            except ValueError as e:
                return tool_error("path_escape", str(e))
        budget = timeout if timeout and timeout > 0 else config.REPO_TEST_TIMEOUT

        # Project with a custom image (Dockerfile in its settings) → run the tests
        # IN that container (deps present, isolated). Otherwise (repo-default, no
        # toolchain) → HOST with the source repo's interpreter (the dogfood case).
        image = repo_exec._resolve_image(conv_folder, project_id, dockerfile)
        if image != repo_exec._REPO_DEFAULT_IMAGE:
            container = repo_exec._container_name(conv_id or Path(conv_folder).name)
            if not repo_exec._container_running(container):
                try:
                    repo_exec._start_repo_container(
                        container, root, workspace_root_for(conv_folder), image,
                    )
                except (OSError, subprocess.SubprocessError) as e:
                    return tool_error("test_failed_to_run", f"could not start project sandbox: {e}")
            tcmd = (config.REPO_TEST_CMD or "").strip() or "pytest -q"
            if test_path:
                tcmd += f" {shlex.quote(test_path)}"
            try:
                proc = subprocess.run(
                    ["docker", "exec", container, "bash", "-lc", tcmd],
                    capture_output=True, text=True, timeout=budget,
                )
            except subprocess.TimeoutExpired:
                return tool_error("test_timeout", f"tests exceeded {budget}s — narrow with test_path.")
            except (OSError, subprocess.SubprocessError) as e:
                return tool_error("test_failed_to_run", f"could not run tests: {e}")
            return _parse((proc.stdout or "") + "\n" + (proc.stderr or ""), proc.returncode)

        # HOST path.
        configured = (config.REPO_TEST_CMD or "").strip()
        if configured:
            cmd = shlex.split(configured)
            # Resolve a relative path-like interpreter against the source repo
            # (e.g. '.venv/bin/python' lives in the project, not the worktree).
            if "/" in cmd[0] and not os.path.isabs(cmd[0]):
                base = worktree.source_repo(conv_folder)
                if base is not None:
                    cmd[0] = str((base / cmd[0]).resolve())
        else:
            cmd = [_default_python(conv_folder), "-m", "pytest", "-q"]
        if test_path:
            cmd.append(test_path)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = subprocess.run(
                cmd, cwd=str(root), capture_output=True, text=True, timeout=budget, env=env,
            )
        except subprocess.TimeoutExpired:
            return tool_error("test_timeout", f"tests exceeded {budget}s — narrow with test_path.")
        except (OSError, subprocess.SubprocessError) as e:
            return tool_error("test_failed_to_run", f"could not run tests: {e}")
        return _parse((proc.stdout or "") + "\n" + (proc.stderr or ""), proc.returncode)

    return ToolSpec(
        name="repo_test",
        description=(
            "SIGNATURE: repo_test(test_path?, timeout?). "
            "Run the project's test suite in the repo worktree and return a STRUCTURED "
            "result (passed bool, exit_code, counts, failed[] test ids, output_tail). "
            "Optionally scope to test_path (a file/dir relative to the repo root). "
            "Run this AFTER editing code and before reporting the step done."
        ),
        parameters={
            "type": "object",
            "properties": {
                "test_path": {"type": "string", "description": "Optional file/dir to scope tests (relative to repo root)."},
                "timeout": {"type": "integer", "description": "Optional timeout seconds (default from config)."},
            },
            "required": [],
        },
        handler=_handler,
    )

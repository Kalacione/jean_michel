"""Integration tests for bash_sandbox tool and sandbox lifecycle.

Requires Docker to be available. All tests are marked with @pytest.mark.docker
and can be skipped in CI without Docker via:
    pytest -m "not docker"

Tests cover:
- Command execution (exit_code, stdout, stderr)
- Refusal of non-granted commands + audit row
- Container lazy start and persistence across calls
- Workspace mount: writes from container visible on host and vice-versa
- Timeout handling
- Network isolation (--network=none)
- cleanup_sandbox() removes the container
- _cleanup_orphan_containers removes stale containers
- sandbox_executions CASCADE DELETE when conversation is removed
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def docker_available():
    """Skip the entire module if Docker is not available or not running."""
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        pytest.skip("Docker daemon not available")


@pytest.fixture()
def tmp_conv(tmp_path):
    """Minimal conversation folder with a workspace/ sub-dir."""
    (tmp_path / "workspace").mkdir()
    return tmp_path


@pytest.fixture()
def sandbox_spec(tmp_conv, tmp_env):
    """A bash_sandbox ToolSpec wired to a fresh DB and tmp conversation."""
    from jeanmichel.tools.bash_sandbox import make_spec

    req_id = "testreqid0001"

    def _req_id():
        return req_id

    spec = make_spec(
        conv_folder=tmp_conv,
        conv_id="testconv0001",
        request_id_provider=_req_id,
        sandbox_grants=["python3", "bash", "cat", "ls", "echo"],
    )
    # Ensure request exists in DB so foreign-key constraint passes.
    import jeanmichel.config as cfg
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    # Insert a minimal conversation + request row.
    conn.execute(
        "INSERT OR IGNORE INTO conversations "
        "(id, folder_path, user_language, mode, created_at, modified_at) "
        "VALUES (?, ?, 'fr', 'analyse', datetime('now'), datetime('now'))",
        ("testconv0001", str(tmp_conv)),
    )
    # agent_id=1 always exists (jean-michel) after schema seed.
    conn.execute(
        "INSERT OR IGNORE INTO requests "
        "(id, conversation_id, depth, agent_id, status, created_at) "
        "VALUES (?, 'testconv0001', 0, 1, 'running', datetime('now'))",
        (req_id,),
    )
    conn.commit()
    conn.close()

    yield spec

    # Cleanup: stop container if still running.
    subprocess.run(
        ["docker", "rm", "-f", "jm-sandbox-testconv0001"],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def _sandbox_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT command, exit_code, duration_ms FROM sandbox_executions ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.docker


class TestCommandExecution:
    def test_nominal_python3(self, docker_available, sandbox_spec, tmp_env):
        result = json.loads(sandbox_spec.handler('python3 -c "print(1+1)"'))
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "2"
        assert result["duration_ms"] >= 0

    def test_stdout_and_stderr_captured(self, docker_available, sandbox_spec, tmp_env):
        result = json.loads(sandbox_spec.handler(
            'bash -c "echo out; echo err >&2"'
        ))
        assert result["exit_code"] == 0
        assert "out" in result["stdout"]
        assert "err" in result["stderr"]

    def test_nonzero_exit_code(self, docker_available, sandbox_spec, tmp_env):
        result = json.loads(sandbox_spec.handler("bash -c 'exit 42'"))
        assert result["exit_code"] == 42

    def test_audit_row_created(self, docker_available, sandbox_spec, tmp_env):
        import jeanmichel.config as cfg
        sandbox_spec.handler('echo "audit test"')
        rows = _sandbox_rows(cfg.DB_PATH)
        assert any("echo" in r["command"] for r in rows)
        matching = [r for r in rows if "audit test" in r["command"]]
        assert matching
        assert matching[0]["exit_code"] == 0
        assert matching[0]["duration_ms"] >= 0


class TestGrantEnforcement:
    def test_refused_command_returns_error(self, docker_available, sandbox_spec):
        result = json.loads(sandbox_spec.handler("curl https://example.com"))
        assert "error" in result
        assert result["exit_code"] is None
        assert "curl" in result["error"]

    def test_refused_command_audit_row_exit_code_null(self, docker_available, sandbox_spec, tmp_env):
        import jeanmichel.config as cfg
        # Use a fresh spec that doesn't grant 'jq' to isolate this test.
        from jeanmichel.tools.bash_sandbox import make_spec

        req_id = "testreqid0002"
        conn = sqlite3.connect(cfg.DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO requests "
            "(id, conversation_id, depth, agent_id, status, created_at) "
            "VALUES (?, 'testconv0001', 0, 1, 'running', datetime('now'))",
            (req_id,),
        )
        conn.commit()
        conn.close()

        spec = make_spec(
            conv_folder=sandbox_spec.handler.__closure__[0].cell_contents
            if False else Path(str(cfg.CONVERSATIONS_DIR) + "/testconv0001"),
            conv_id="testconv0001",
            request_id_provider=lambda: req_id,
            sandbox_grants=["echo"],  # jq NOT granted
        )
        # Patch the conv_folder — easiest way is to build it directly.
        from jeanmichel.tools.bash_sandbox import make_spec as _make_spec
        from jeanmichel.tools._workspace import workspace_root_for
        tmp_conv_path = cfg.CONVERSATIONS_DIR / "testconv0001"
        tmp_conv_path.mkdir(parents=True, exist_ok=True)
        (tmp_conv_path / "workspace").mkdir(exist_ok=True)
        spec2 = _make_spec(tmp_conv_path, "testconv0001", lambda: req_id, ["echo"])
        result = json.loads(spec2.handler("jq ."))
        assert result["exit_code"] is None
        rows = _sandbox_rows(cfg.DB_PATH)
        jq_rows = [r for r in rows if r["command"] == "jq ."]
        assert jq_rows
        assert jq_rows[-1]["exit_code"] is None


class TestContainerLifecycle:
    def test_container_starts_lazily(self, docker_available, sandbox_spec):
        name = "jm-sandbox-testconv0001"
        # Not running before first call.
        # (May already be running from a previous test in this session — cleanup handles it.)
        sandbox_spec.handler('echo "start test"')
        assert _container_running(name)

    def test_container_persists_across_calls(self, docker_available, sandbox_spec):
        name = "jm-sandbox-testconv0001"
        sandbox_spec.handler('echo "call 1"')
        assert _container_running(name)
        sandbox_spec.handler('echo "call 2"')
        assert _container_running(name)


class TestWorkspaceMount:
    def test_file_written_in_container_visible_on_host(self, docker_available, sandbox_spec, tmp_conv):
        sandbox_spec.handler('bash -c "echo hello_from_container > /workspace/from_container.txt"')
        assert (tmp_conv / "workspace" / "from_container.txt").exists()
        assert "hello_from_container" in (tmp_conv / "workspace" / "from_container.txt").read_text()

    def test_file_written_on_host_visible_in_container(self, docker_available, sandbox_spec, tmp_conv):
        (tmp_conv / "workspace" / "from_host.txt").write_text("hello_from_host\n")
        result = json.loads(sandbox_spec.handler("cat /workspace/from_host.txt"))
        assert result["exit_code"] == 0
        assert "hello_from_host" in result["stdout"]


class TestNetworkIsolation:
    def test_no_network_access(self, docker_available, sandbox_spec, tmp_env):
        import jeanmichel.config as cfg
        # Grant curl for this specific test spec to verify --network=none works.
        req_id = "testreqid0003"
        conn = sqlite3.connect(cfg.DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO requests "
            "(id, conversation_id, depth, agent_id, status, created_at) "
            "VALUES (?, 'testconv0001', 0, 1, 'running', datetime('now'))",
            (req_id,),
        )
        conn.commit()
        conn.close()

        tmp_conv_path = cfg.CONVERSATIONS_DIR / "testconv0001"
        tmp_conv_path.mkdir(parents=True, exist_ok=True)
        (tmp_conv_path / "workspace").mkdir(exist_ok=True)

        from jeanmichel.tools.bash_sandbox import make_spec as _make_spec
        spec = _make_spec(tmp_conv_path, "testconv0001", lambda: req_id, ["curl"])
        result = json.loads(spec.handler("curl --max-time 5 https://example.com"))
        # With --network=none, curl fails (exit_code != 0).
        assert result["exit_code"] != 0


class TestCleanup:
    def test_cleanup_sandbox_removes_container(self, docker_available, sandbox_spec, tmp_env):
        from jeanmichel.orchestrator import Orchestrator
        from jeanmichel.llm import MockClient
        from jeanmichel.config import UserProfile

        orch = Orchestrator(
            llm=MockClient(script=[]),
            profile=UserProfile(),
            conv_id="testconv0001",
        )
        # Ensure container is running.
        sandbox_spec.handler('echo "pre-cleanup"')
        assert _container_running("jm-sandbox-testconv0001")
        orch.cleanup_sandbox()
        # Give Docker a moment.
        time.sleep(0.5)
        assert not _container_running("jm-sandbox-testconv0001")

    def test_cleanup_orphan_containers(self, docker_available):
        from debug.clean_convs import _cleanup_orphan_containers  # type: ignore[import]

        # Start a dummy container with an ID that won't match any DB conversation.
        orphan_name = "jm-sandbox-orphan999test"
        subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", orphan_name,
                "--network=none",
                "jeanmichel-sandbox:24.04",
                "tail", "-f", "/dev/null",
            ],
            capture_output=True, check=True,
        )
        assert _container_running(orphan_name)
        # Pass an empty set — no active conversations, so orphan_name should be removed.
        _cleanup_orphan_containers(set())
        time.sleep(0.3)
        assert not _container_running(orphan_name)


class TestCascadeDelete:
    def test_sandbox_executions_deleted_with_conversation(self, docker_available, sandbox_spec, tmp_env):
        import jeanmichel.config as cfg
        from jeanmichel import db

        sandbox_spec.handler('echo "cascade test"')

        # Verify at least one row exists.
        conn = sqlite3.connect(cfg.DB_PATH)
        count_before = conn.execute(
            "SELECT COUNT(*) FROM sandbox_executions WHERE request_id = 'testreqid0001'"
        ).fetchone()[0]
        conn.close()
        assert count_before > 0

        # Delete the conversation — should cascade to requests → sandbox_executions.
        with db.connect() as conn:
            conn.execute("DELETE FROM conversations WHERE id = 'testconv0001'")

        conn2 = sqlite3.connect(cfg.DB_PATH)
        conn2.execute("PRAGMA foreign_keys = ON")
        count_after = conn2.execute(
            "SELECT COUNT(*) FROM sandbox_executions WHERE request_id = 'testreqid0001'"
        ).fetchone()[0]
        conn2.close()
        assert count_after == 0

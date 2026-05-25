---
name: testing
description: "Write pytest tests for the jean-michel project. Use when: creating unit or integration tests, refactoring tests, validating DB logic, testing tools, testing orchestrator flows with MockClient. Covers test setup pattern (temp dir + JEANMICHEL_HOME), MockClient scripting, DB fixture, and which modules are independently testable."
---

# Testing — Jean-Michel

## When to Use

- Writing or updating tests in `tests/`
- Validating a new tool, DB helper, or orchestrator behaviour
- Refactoring existing test infrastructure

## Key Constraints

- **No Ollama required**: all tests use `MockClient(script=[LLMResponse, ...])`
- **No global state**: always isolate via `JEANMICHEL_HOME` + a temp dir
- Tests live in `tests/` and are run with `pytest`
- `tests/demo_cli.py` is a visual demo — never add assertions to it

---

## Standard Test Setup

Every test that touches DB, config, or orchestrator needs this fixture:

```python
import os, sqlite3, tempfile
from pathlib import Path
import pytest

HERE = Path(__file__).parent
ROOT = HERE.parent

@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """Isolated environment: temp dir, fresh DB, no global state."""
    monkeypatch.setenv("JEANMICHEL_HOME", str(tmp_path))

    # Force reload of config module paths
    import jeanmichel.config as cfg
    cfg.REPO_ROOT = tmp_path
    cfg.DB_PATH = tmp_path / "jeanmichel.db"
    cfg.CONVERSATIONS_DIR = tmp_path / "conversations"
    cfg.USER_PROFILE_PATH = tmp_path / "user_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Init fresh DB from schema
    schema = (ROOT / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    return tmp_path
```

---

## MockClient

Script the LLM's responses in order. Each `LLMResponse` corresponds to one `llm.chat()` call:

```python
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall

script = [
    LLMResponse(
        thinking="I'll return immediately.",
        content="",
        tool_calls=[ToolCall(name="return_to_user", arguments={"answer": "Bonjour."})],
    ),
]
llm = MockClient(script=script)
```

If the script is exhausted and the orchestrator requests another turn, `MockClient` raises `IndexError`. Ensure the script covers all turns including delegated agents.

Control tool calls the orchestrator intercepts (never reach `llm.chat`):
- `return_to_user` — ends the request
- `delegate_to` — spawns a sub-request (consumes next script entries)
- `ask_human` — calls `ask_human_callback`

---

## Running the Orchestrator in Tests

```python
from jeanmichel.config import UserProfile
from jeanmichel.orchestrator import Orchestrator, FinalAnswer, AgentStarted

def test_simple_answer(tmp_env):
    profile = UserProfile(description="test user")
    llm = MockClient(script=[
        LLMResponse(
            thinking="",
            content="",
            tool_calls=[ToolCall(name="return_to_user", arguments={"answer": "42"})],
        ),
    ])
    orch = Orchestrator(llm=llm, profile=profile,
                        ask_human_callback=lambda q, w: "n/a")
    events = list(orch.run("What is the answer?"))
    answer = next(e for e in events if isinstance(e, FinalAnswer))
    assert answer.text == "42"
```

---

## What Is Independently Testable (No Orchestrator)

### Tools

```python
from jeanmichel.tools.clock import SPEC as clock_spec
from jeanmichel.tools.conv_read_file import make_spec

def test_clock_utc():
    import json
    result = json.loads(clock_spec.handler())
    assert "utc" in result
    assert "local" in result

def test_conv_read_file_path_traversal(tmp_path):
    spec = make_spec(tmp_path / "conv")
    result = spec.handler("../../etc/passwd")
    assert "error" in result

def test_conv_read_file_reads(tmp_path):
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "hello.txt").write_text("world")
    spec = make_spec(conv)
    assert spec.handler("hello.txt") == "world"
```

### DB helpers

```python
from jeanmichel import db

def test_load_tool_grants(tmp_env):
    with db.connect() as conn:
        agents = db.list_active_agents(conn)
        jm = next(a for a in agents if a.code == "jean-michel")
        grants = db.load_tool_grants(conn, jm.id)
    assert "clock" in grants
    assert "conv_read_file" in grants

def test_unknown_agent_raises(tmp_env):
    with db.connect() as conn:
        with pytest.raises(KeyError):
            db.get_agent_by_code(conn, "nonexistent")
```

### Persistence

```python
from jeanmichel.persistence import write_artifact, conversation_folder_name
from datetime import datetime, UTC

def test_write_artifact_creates_file(tmp_path):
    folder = tmp_path / "conv"
    folder.mkdir()
    filename = write_artifact(folder, conversation_id="conv1",
                              request_id="req1", agent="jean-michel",
                              kind="thought", body="I am thinking.")
    assert (folder / filename).exists()
    content = (folder / filename).read_text()
    assert "agent: jean-michel" in content
    assert "I am thinking." in content

def test_conversation_folder_name():
    dt = datetime(2026, 4, 27, 14, 30, tzinfo=UTC)
    name = conversation_folder_name("abc123", dt)
    assert name == "2026-04-27_14-30_abc123"
```

### Prompts

```python
from jeanmichel.prompts import tools_payload_for_agent, render_directives
from jeanmichel.tools.clock import SPEC as clock_spec

def test_tools_payload_includes_granted_tools():
    registry = {"clock": clock_spec}
    payload = tools_payload_for_agent(["clock"], registry)
    names = [e["function"]["name"] for e in payload]
    assert "clock" in names
    assert "return_to_user" in names   # always present (control tool)

def test_tools_payload_skips_unknown_grant():
    registry = {}
    payload = tools_payload_for_agent(["nonexistent"], registry)
    names = [e["function"]["name"] for e in payload]
    assert "nonexistent" not in names
```

---

## Module Layout

```
tests/
  conftest.py          # shared fixtures (tmp_env, etc.)
  test_tools.py        # unit tests for tools/
  test_db.py           # unit tests for db.py helpers
  test_persistence.py  # unit tests for persistence.py
  test_prompts.py      # unit tests for prompts.py
  test_orchestrator.py # integration tests via MockClient
  smoke.py             # legacy end-to-end (kept, no pytest)
  demo_cli.py          # visual demo — NO assertions
```

---

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check src/ tests/
```

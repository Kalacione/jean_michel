# Jean-Michel — Project Guidelines

## Architecture

Python 3.14 local AI assistant. Key components:

- **Orchestrator** (`src/jeanmichel/orchestrator.py`): pure-Python generator state machine. Drives agent turns, delegates recursively, persists everything. Yields typed event dataclasses consumed by the CLI.
- **Agents**: defined in SQLite (`agents` table). Three roles: `router` (jean-michel), `specialist` (summarizer), `finalizer` (synthesizer).
- **Tools**: `src/jeanmichel/tools/` sub-package. Stateless tools expose `SPEC: ToolSpec`; context-bound tools expose `make_spec(conv_folder)`. Grants are stored in `agent_tools` table (DB source of truth, not hardcoded).
- **Prompts**: `prompts.py` renders system prompts from `PromptContext`. All paradigms/directives come from the DB (`paradigms` table), not from code.
- **Persistence**: flat `.md` files per artifact (thought, tool_call, response…) with YAML frontmatter, inside `conversations/<id>/`. DB records metadata only.
- **LLM**: Ollama via `OllamaClient`; `MockClient(script=[LLMResponse, ...])` for tests.
- **CLI**: Rich + prompt_toolkit. Multi-line input (Enter = newline, Alt+Enter = send). Spinner between events.

## Stack

- Python 3.14, SQLite (`jeanmichel.db`), Ollama 0.21+
- `rich`, `prompt_toolkit`, `langdetect`, `ollama` (Python client)
- pytest + ruff (dev)
- venv in `.venv/`, package installed editable via `pip install -e ".[dev]"`

## Entry Points

```
./jm.sh                  # launch CLI (auto-installs if needed)
./jm.sh --install        # setup venv + DB
./jm.sh --export-db      # → backups/db_TIMESTAMP.json
./jm.sh --clean [--days N]  # purge old conversations
./jm.sh --inspect-conv ID   # debug artifacts
```

## Build & Test

```bash
.venv/bin/python -m pytest tests/ -v    # run all tests
.venv/bin/python tests/smoke.py         # legacy integration smoke (keeps working)
.venv/bin/ruff check src/ tests/        # lint
```

Tests use `MockClient(script=[...])` — no Ollama required. Always redirect config paths to a temp dir via `os.environ["JEANMICHEL_HOME"]`.

## DB Schema

Core tables: `agents`, `paradigms`, `categories`, `sections`, `agent_paradigms`, `agent_tools`, `conversations`, `requests`, `artifacts`. Schema + seeds in `db/schema.sql`.

## Conventions

- Tool names are the LLM-facing `name` in `ToolSpec` (e.g. `conv_read_file`, not `read_file`).
- Events yielded by the orchestrator are frozen `@dataclass` instances — do not add logic to them.
- All DB writes go through `db.py` helpers; never raw SQL in orchestrator/prompts.
- Artifact filenames: `HHMMSSMMM_<agent>_<kind>.md`.
- `JEANMICHEL_HOME` env var overrides all runtime paths (used in tests and `jm.sh`).
- No runtime dependencies beyond what's in `pyproject.toml`; no optional feature flags.

## What Not To Do

- Do not hardcode tool grants in Python — always use `agent_tools` table.
- Do not add docstrings/comments to untouched code.
- Do not create helpers for one-off operations.
- Do not add error handling for impossible scenarios.
- `tests/demo_cli.py` is a visual demo, not a test — do not add assertions to it.

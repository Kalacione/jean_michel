"""Verify v2 source code doesn't reference dropped legacy tables.

Lightweight grep-based audit. We don't AST-parse — just match the
common SQL idioms (FROM/INTO/JOIN/UPDATE/DELETE FROM <table>).
A failing test means a v2 module would crash at runtime against a
v2-migrated DB.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent

# Files that constitute the v2 surface. Legacy files (orchestrator.py,
# plan_writer.py, the old tools, etc.) are deliberately excluded — they're
# slated for removal in Phase 8.
V2_SOURCE_FILES: list[str] = [
    "src/jeanmichel/orchestrator_v2.py",
    "src/jeanmichel/dispatcher.py",
    "src/jeanmichel/hooks.py",
    "src/jeanmichel/compaction.py",
    "src/jeanmichel/events.py",
    "src/jeanmichel/tokens.py",
    "src/jeanmichel/bootstrap.py",
    "src/jeanmichel/tools/manage_memory.py",
    "src/jeanmichel/tools/delegate_to.py",
    "src/jeanmichel/tools/report_back.py",
]

# Tables dropped by migrate_102. v2 code must not SQL-reference these.
LEGACY_TABLES: list[str] = [
    "requests",
    "artifacts",
    "conversation_phases",
    "sandbox_executions",
]


def _sql_patterns_for_table(table: str) -> list[str]:
    """Build regex patterns that match SQL clauses touching `table`."""
    return [
        rf"\bFROM\s+{table}\b",
        rf"\bINTO\s+{table}\b",
        rf"\bJOIN\s+{table}\b",
        rf"\bUPDATE\s+{table}\b",
        rf"\bDELETE\s+FROM\s+{table}\b",
    ]


@pytest.mark.parametrize("source_file", V2_SOURCE_FILES)
@pytest.mark.parametrize("legacy_table", LEGACY_TABLES)
def test_v2_source_does_not_query_legacy_table(source_file: str, legacy_table: str):
    """No v2 source file should query a dropped table via SQL."""
    src = (_ROOT / source_file).read_text(encoding="utf-8")
    for pattern in _sql_patterns_for_table(legacy_table):
        match = re.search(pattern, src, re.IGNORECASE)
        assert match is None, (
            f"{source_file} contains SQL reference to legacy table "
            f"{legacy_table!r} (pattern {pattern!r}, match: {match.group(0)!r})"
        )


# ---- Tool name references ------------------------------------------------

# Tools deleted in Phase 6. v2 source should not import them or grant them.
DEAD_TOOL_MODULES: list[str] = [
    "tools.set_task_class",
    "tools.manage_todo_list",
    # signal_convergence : was never its own module, just a string identifier.
]


@pytest.mark.parametrize("source_file", V2_SOURCE_FILES)
@pytest.mark.parametrize("dead_tool", DEAD_TOOL_MODULES)
def test_v2_source_does_not_import_dead_tool(source_file: str, dead_tool: str):
    """v2 code must not import legacy tool modules slated for removal."""
    src = (_ROOT / source_file).read_text(encoding="utf-8")
    import_patterns = [
        rf"\bimport\s+jeanmichel\.{re.escape(dead_tool)}\b",
        rf"\bfrom\s+jeanmichel\.{re.escape(dead_tool)}\s+import\b",
        # Relative imports inside src/jeanmichel/.
        rf"\bfrom\s+\.{re.escape(dead_tool)}\s+import\b",
        rf"\bfrom\s+\.\.{re.escape(dead_tool)}\s+import\b",
    ]
    for pattern in import_patterns:
        assert re.search(pattern, src) is None, (
            f"{source_file} imports {dead_tool!r} (pattern {pattern!r})"
        )


# ---- Sanity : v2 surface files actually exist -----------------------------


@pytest.mark.parametrize("source_file", V2_SOURCE_FILES)
def test_v2_source_file_exists(source_file: str):
    """If a file in V2_SOURCE_FILES is missing, the test list is stale."""
    assert (_ROOT / source_file).exists(), f"missing v2 source file: {source_file}"

"""Smoke test: drives the orchestrator with a scripted MockClient.

Flow exercised:
  1. user input
  2. jean-michel emits a clock tool_call (native tool execution)
  3. jean-michel delegates to summarizer
  4. summarizer returns_to_user
  5. jean-michel returns_to_user with the final answer

Asserts all artifacts and DB rows are written correctly.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Bootstrap path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

from jeanmichel import config  # noqa: E402
from jeanmichel.llm import MockClient  # noqa: E402
from jeanmichel.models import LLMResponse, ToolCall  # noqa: E402
from jeanmichel.orchestrator import (  # noqa: E402
    AgentStarted,
    ConversationStarted,
    DelegationStarted,
    FinalAnswer,
    Orchestrator,
    ThoughtCaptured,
    ToolCallEmitted,
    ToolResponseRecorded,
)


def _init_db(tmpdir: Path) -> Path:
    schema = (HERE.parent / "db" / "schema.sql").read_text()
    db_path = tmpdir / "jeanmichel.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()
    return db_path


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="jm-test-"))
    try:
        # Redirect config to the temp dir.
        os.environ["JEANMICHEL_HOME"] = str(tmpdir)
        # Force reload of paths since they were captured at import time.
        config.REPO_ROOT = tmpdir
        config.DB_PATH = tmpdir / "jeanmichel.db"
        config.CONVERSATIONS_DIR = tmpdir / "conversations"
        config.USER_PROFILE_PATH = tmpdir / "user_profile.toml"

        _init_db(tmpdir)

        profile = config.UserProfile(city="Montréal", language="french", notes="test user")

        # Scripted LLM responses.
        script = [
            # Turn 1 — jean-michel: think + call clock + delegate to summarizer
            LLMResponse(
                thinking="The user asked for a summary. I'll first check the time, "
                         "then delegate to summarizer.",
                content="",
                tool_calls=[
                    ToolCall(name="clock", arguments={"timezone": "America/Montreal"}),
                    ToolCall(name="delegate_to", arguments={
                        "agent_code": "summarizer",
                        "briefing": "Summarize this text in 2 sentences: 'The cat sat on the mat. "
                                    "It was a sunny day. The dog watched from afar.'",
                        "expected": "A 2-sentence summary, English.",
                        "support_files": [],
                    }),
                ],
            ),
            # Turn 2 — summarizer: think + return_to_user
            LLMResponse(
                thinking="Three short sentences, low information density. Compress to 2.",
                content="",
                tool_calls=[
                    ToolCall(name="return_to_user", arguments={
                        "answer": "A cat rested on a mat under sunny weather. A dog watched from a distance.",
                    }),
                ],
            ),
            # Turn 3 — jean-michel resumes after tool responses, returns final
            LLMResponse(
                thinking="Summarizer delivered. Returning the result in the user's language.",
                content="",
                tool_calls=[
                    ToolCall(name="return_to_user", arguments={
                        "answer": "Voici le résumé : un chat se reposait sur un tapis sous un soleil radieux, "
                                  "un chien observait de loin.",
                    }),
                ],
            ),
        ]

        llm = MockClient(script=script)
        orch = Orchestrator(llm=llm, profile=profile,
                            ask_human_callback=lambda question, why: "n/a")

        events = list(orch.run("Résume ce texte: The cat sat on the mat. "
                               "It was a sunny day. The dog watched from afar."))

        # ---- Assertions ----
        kinds = [type(e).__name__ for e in events]
        print("Events:", kinds)

        assert any(isinstance(e, ConversationStarted) for e in events), "missing ConversationStarted"
        assert sum(isinstance(e, AgentStarted) for e in events) == 2, "expected 2 AgentStarted (jean-michel + summarizer)"
        assert sum(isinstance(e, ThoughtCaptured) for e in events) == 3, "expected 3 thoughts captured"
        assert any(isinstance(e, ToolCallEmitted) and e.tool_name == "clock" for e in events)
        assert any(isinstance(e, ToolResponseRecorded) and e.tool_name == "clock" for e in events)
        assert any(isinstance(e, DelegationStarted) and e.child_agent == "summarizer" for e in events)

        finals = [e for e in events if isinstance(e, FinalAnswer)]
        assert len(finals) == 1
        assert "chat" in finals[0].text.lower()
        print("Final answer:", finals[0].text)

        # Verify artifacts on disk
        conv_folders = list((tmpdir / "conversations").iterdir())
        assert len(conv_folders) == 1
        artifacts = sorted(p.name for p in conv_folders[0].iterdir())
        print("Artifacts written:")
        for a in artifacts:
            print(" -", a)

        # Verify DB state
        conn = sqlite3.connect(tmpdir / "jeanmichel.db")
        conn.row_factory = sqlite3.Row
        reqs = conn.execute(
            "SELECT r.id, a.code AS agent, r.depth, r.status, r.parent_request_id "
            "FROM requests r JOIN agents a ON a.id = r.agent_id "
            "ORDER BY r.created_at"
        ).fetchall()
        print("Requests in DB:")
        for r in reqs:
            print(f"  agent={r['agent']} depth={r['depth']} status={r['status']} "
                  f"parent={r['parent_request_id']}")
        assert len(reqs) == 2
        assert reqs[0]["agent"] == "jean-michel" and reqs[0]["depth"] == 0
        assert reqs[1]["agent"] == "summarizer" and reqs[1]["depth"] == 1
        assert reqs[1]["parent_request_id"] == reqs[0]["id"]
        assert all(r["status"] == "completed" for r in reqs)

        artifacts_db = conn.execute("SELECT kind, COUNT(*) c FROM artifacts GROUP BY kind").fetchall()
        print("Artifacts by kind:")
        for r in artifacts_db:
            print(f"  {r['kind']}: {r['c']}")
        conn.close()

        print("\n✓ smoke test passed")
        return 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

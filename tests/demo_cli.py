"""Demo: render the CLI output with a scripted MockClient (no Ollama required)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

from rich.console import Console  # noqa: E402

from jeanmichel import config  # noqa: E402
from jeanmichel.cli import make_ask_human, render_events, render_splash  # noqa: E402
from jeanmichel.llm import MockClient  # noqa: E402
from jeanmichel.models import LLMResponse, ToolCall  # noqa: E402
from jeanmichel.orchestrator import Orchestrator  # noqa: E402


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="jm-demo-"))
    try:
        os.environ["JEANMICHEL_HOME"] = str(tmpdir)
        config.REPO_ROOT = tmpdir
        config.DB_PATH = tmpdir / "jeanmichel.db"
        config.CONVERSATIONS_DIR = tmpdir / "conversations"
        config.USER_PROFILE_PATH = tmpdir / "user_profile.toml"

        schema = (HERE.parent / "db" / "schema.sql").read_text()
        conn = sqlite3.connect(config.DB_PATH)
        conn.executescript(schema)
        conn.commit()
        conn.close()

        profile = config.UserProfile(city="Montréal", language="french", notes="L'humain est francophone.")

        script = [
            LLMResponse(
                thinking=("Je dois résumer le texte fourni. Je vérifie d'abord l'heure "
                          "(pour le suivi) puis je délègue au summarizer."),
                content="",
                tool_calls=[
                    ToolCall(name="clock", arguments={"timezone": "America/Montreal"}),
                    ToolCall(name="delegate_to", arguments={
                        "agent_code": "summarizer",
                        "briefing": "Summarize: 'The cat sat on the mat. It was a sunny day. The dog watched from afar.'",
                        "expected": "A 2-sentence English summary.",
                        "support_files": [],
                    }),
                ],
            ),
            LLMResponse(
                thinking="Three sentences, low density. Compress to two.",
                content="",
                tool_calls=[
                    ToolCall(name="return_to_user", arguments={
                        "answer": "A cat rested on a mat under sunny weather. A dog watched from a distance.",
                    }),
                ],
            ),
            LLMResponse(
                thinking="Translate the summary back to French and deliver.",
                content="",
                tool_calls=[
                    ToolCall(name="return_to_user", arguments={
                        "answer": ("**Résumé**\n\nUn chat se reposait sur un tapis sous un "
                                   "soleil radieux. Un chien l'observait de loin."),
                    }),
                ],
            ),
        ]

        console = Console(force_terminal=True, width=100)
        render_splash(console, "gemma4:e4b (mock)")

        console.print("[bold cyan]you[/]: Résume ce texte: The cat sat on the mat. "
                      "It was a sunny day. The dog watched from afar.\n")

        llm = MockClient(script=script)
        orch = Orchestrator(llm=llm, profile=profile,
                            ask_human_callback=make_ask_human(console))
        render_events(
            console,
            orch.run("Résume ce texte: The cat sat on the mat. It was a sunny day. The dog watched from afar."),
            show_thoughts=True,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

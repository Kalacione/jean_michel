"""v2 — first-start bootstrap.

Mirror the static ``cli_profile.toml`` into the new ``user_memory`` table
on the first run (cf. §11 ter A doc 06). Idempotent : if the table is
non-empty, the function does nothing.

This is a transitional helper. Once the user has saved their own entries
via ``manage_user_memory``, ``cli_profile.toml`` becomes vestigial.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from .config import UserProfile

_log = logging.getLogger(__name__)


_BOOTSTRAP_TYPE = "user"
_BOOTSTRAP_CODE = "personal-profile"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bootstrap_user_memory_from_profile(
    conn: sqlite3.Connection,
    profile: UserProfile,
    user_id: int | None = None,
) -> bool:
    """Create a `user/personal-profile` entry for a user, from a profile.

    Scoped to ``user_id`` (``None`` → the reserved ``cli`` user). No-op when :
    - the ``user_memory`` table / ``cli`` user is missing (migrations not applied),
    - this user already holds at least one entry,
    - the profile is empty (default `UserProfile()` with no fields set).

    Returns ``True`` when a new entry was inserted, ``False`` otherwise.
    """
    from .db import cli_user_id

    # Defensive : if the migrations haven't run, the table / cli user don't exist.
    try:
        uid = user_id if user_id is not None else cli_user_id(conn)
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM user_memory WHERE user_id=?", (uid,)
        ).fetchone()
    except (sqlite3.OperationalError, KeyError):
        _log.info(
            "bootstrap skipped : user_memory/web_users missing (migrations not applied)"
        )
        return False

    if existing is not None and int(existing["c"]) > 0:
        return False

    rendered = profile.render().strip()
    if not rendered or rendered == "No user profile provided.":
        return False

    # Compose description : the user's own notes if present, else a short
    # synthesised line from the structured fields.
    if profile.notes.strip():
        description_src = profile.notes.strip()
    else:
        parts: list[str] = []
        if profile.name:
            parts.append(profile.name)
        if profile.city or profile.country:
            loc = ", ".join(p for p in (profile.city, profile.country) if p)
            parts.append(loc)
        if profile.language:
            parts.append(f"speaks {profile.language}")
        description_src = "; ".join(parts) if parts else "User profile bootstrap entry."

    description = _truncate(description_src, 150)
    title = _truncate("Personal profile (bootstrap)", 60)
    content = _truncate(rendered, 1_000)

    now = _now()
    conn.execute(
        "INSERT INTO user_memory "
        "(user_id, type, code, title, description, content, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, _BOOTSTRAP_TYPE, _BOOTSTRAP_CODE, title, description, content, now, now),
    )
    return True


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"

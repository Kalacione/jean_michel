"""Authentication for the web daemon — ultra-simple, multi-user.

- Passwords hashed with argon2 (``web_users.password_hash``).
- Stateless bearer tokens signed with itsdangerous (no server-side sessions ;
  survive daemon restarts as long as the signing secret is stable).
- FastAPI dependencies : ``current_user`` (resolve the bearer token) and
  ``require_conversation_owner`` (403 unless the user owns the conversation).

Pure helpers (hashing, tokens, ``authenticate``) have no FastAPI dependency ;
only the two dependency callables import it. The module as a whole still needs
the ``[web]`` extras (argon2-cffi, itsdangerous, fastapi).

CLI to create a user :  python -m jeanmichel.api.auth create-user <username>
"""

from __future__ import annotations

import contextlib
import os
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Header, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import config, db

_ph = PasswordHasher()

_TOKEN_SALT = "jeanmichel.api.auth.v1"
TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days


# ---- Password hashing -----------------------------------------------------


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


# ---- Token signing --------------------------------------------------------


def _secret_key() -> str:
    """Resolve the token-signing secret.

    ``JEANMICHEL_API_SECRET`` wins ; otherwise a random key is generated once
    and persisted to ``REPO_ROOT/.api_secret`` so tokens survive restarts with
    zero config. Read dynamically (``config.REPO_ROOT``) so tests pointing at a
    tmp home get an isolated secret.
    """
    env = os.environ.get("JEANMICHEL_API_SECRET")
    if env:
        return env
    secret_path = config.REPO_ROOT / ".api_secret"
    if secret_path.is_file():
        return secret_path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    secret_path.write_text(key, encoding="utf-8")
    with contextlib.suppress(OSError):
        secret_path.chmod(0o600)
    return key


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt=_TOKEN_SALT)


def make_token(user: dict[str, Any]) -> str:
    """Sign a bearer token for ``{'id', 'username'}``."""
    return _serializer().dumps({"uid": user["id"], "username": user["username"]})


def verify_token(token: str, max_age: int = TOKEN_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    """Return ``{'id', 'username'}`` for a valid token, else None."""
    try:
        payload = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return {"id": payload["uid"], "username": payload["username"]}


# ---- Authentication -------------------------------------------------------


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return ``{'id', 'username'}`` on valid credentials, else None."""
    with db.connect() as conn:
        row = db.get_web_user_by_username(conn, username)
    if row is None:
        return None
    if not verify_password(row["password_hash"], password):
        return None
    return {"id": row["id"], "username": row["username"]}


# ---- FastAPI dependencies -------------------------------------------------


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Resolve the bearer token to a user, or raise 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    user = verify_token(authorization[len("Bearer ") :].strip())
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user


def require_conversation_owner(
    conversation_id: str, user: dict[str, Any] = Depends(current_user)
) -> Any:
    """Guard : return the conversation row. 404 if unknown, 403 if not owned.

    Returning the row (with ``folder_path``) lets read endpoints avoid a second
    lookup. sqlite3.Row keeps its values after the connection closes.
    """
    with db.connect() as conn:
        row = db.get_conversation(conn, conversation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if not db.user_owns_conversation(conn, user["id"], row["id"]):
            raise HTTPException(status_code=403, detail="not your conversation")
    return row


# ---- User-creation CLI ----------------------------------------------------


def _create_user_cli(argv: list[str]) -> int:
    import getpass

    if len(argv) != 1:
        print("usage: python -m jeanmichel.api.auth create-user <username>")
        return 2
    username = argv[0]
    if username == "cli":
        print("aborted: 'cli' is reserved for the CLI user.")
        return 1
    password = getpass.getpass(f"password for {username!r}: ")
    if not password:
        print("aborted: empty password")
        return 1
    if getpass.getpass("confirm: ") != password:
        print("aborted: passwords differ")
        return 1
    print("base profile (press Enter to skip):")
    name = input("  name: ").strip()
    city = input("  city: ").strip()
    country = input("  country: ").strip()
    language = input("  language (e.g. fr): ").strip()
    try:
        with db.connect() as conn:
            uid = db.create_web_user(
                conn,
                username,
                hash_password(password),
                name=name,
                city=city,
                country=country,
                language=language,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {exc}")
        return 1
    print(f"created web user {username!r} (id={uid})")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "create-user":
        print("usage: python -m jeanmichel.api.auth create-user <username>")
        return 2
    return _create_user_cli(argv[1:])


if __name__ == "__main__":
    import sys

    sys.exit(main())

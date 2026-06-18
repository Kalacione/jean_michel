"""Memory consolidation — the end-of-turn reflection beat.

``propose()`` runs at the completion of a DEEP turn (the live reflection beat, fired
by the CLI thread / the API executor — never a wall-clock timer). The LLM proposes
candidate memories from the fresh exchange ; every proposal is verified
DETERMINISTICALLY before a human ever sees it :

  1. **Grounding** — each candidate must carry a ``grounding_quote`` that is a
     verbatim (whitespace/case-normalized) excerpt of a USER message or TOOL result.
     A candidate whose quote is not found is dropped — the anti-hallucination gate
     (the LLM cannot invent a fact "to please" without a real source).
  2. **Dedup / contradiction** — for each survivor we run a deterministic FTS
     search in its target scope and attach the existing matches (BM25-ranked),
     so the human can *extend* an existing entry rather than duplicate it.

Nothing is ever written here. Candidates accumulate in the ``pending_consolidation``
DB table (per conversation) ; the CLI ``/memo`` / web review UI applies the human's
choice via ``service.memory``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import db, persistence
from . import memory

_log = logging.getLogger(__name__)

MAX_CANDIDATES = 6
MIN_QUOTE_CHARS = 12          # too-short quotes can't be reliably grounded
MAX_TRANSCRIPT_CHARS = 8_000  # cap the transcript fed to the LLM

CONSOLIDATION_SYSTEM_PROMPT = """You extract durable, reusable facts worth remembering long-term from a conversation. Reply with strict JSON of shape:
{"candidates": [{"scope": "...", "code": "...", "title": "...", "description": "...", "content": "...", "grounding_quote": "...", "tool_code": "..."}]}

Rules:
- Only propose a fact that is DURABLE and reusable across future conversations. Skip one-off task details, transient state, and anything already obvious.
- `scope`: "user" (a stable fact/preference about the human), "project" (a decision/constraint of the current project), or "tool" (a reusable lesson on how to use a tool — set `tool_code`).
- `grounding_quote`: a VERBATIM excerpt copied from a USER message or a TOOL result that supports the fact — NEVER from the assistant's own statements (those may be unverified). If you cannot quote a user/tool source, do not propose it. Never paraphrase the quote.
- `code`: a short kebab-case slug (e.g. "prefers-terse-answers"). No spaces.
- Keep it concise: title <= 60 chars, description <= 150, content <= 1000.
- If nothing is worth remembering, return {"candidates": []}. Do not invent facts to be helpful.
Output English only."""


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace, for robust substring grounding checks."""
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _transcript(messages: list[dict[str, Any]]) -> str:
    """Render user/assistant/tool turns as a plain transcript (most recent kept).
    Tool results are included (clamped) so the LLM can cite a real SOURCE."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if not (isinstance(content, str) and content.strip()):
            continue
        if role in ("user", "assistant"):
            lines.append(f"{role}: {content.strip()}")
        elif role == "tool":
            lines.append(f"tool: {_clamp(content, 600)}")
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[-MAX_TRANSCRIPT_CHARS:]  # keep the most recent context
    return text


def _groundable(messages: list[dict[str, Any]]) -> str:
    """Normalized text the grounding gate accepts as a SOURCE : user messages +
    tool results ONLY — never the assistant's own (possibly hallucinated) claims.
    This is the anti-GIGO gate (the model can't memorize its own inventions)."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "tool") and isinstance(content, str) and content.strip():
            parts.append(content)
    return _norm("\n".join(parts))


def _clamp(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _target_for(scope: str, *, project_id: int | None, tool_code: str | None) -> dict[str, Any] | None:
    """Concrete service target for a scope, or None if it can't be satisfied here."""
    if scope == "user":
        return {}  # filled with user_id by the caller
    if scope == "project":
        return {"project_id": project_id} if project_id is not None else None
    if scope == "tool":
        return {"tool_code": tool_code} if tool_code else None
    return None


def propose(
    conn: Any,
    messages: list[dict[str, Any]],
    *,
    llm: Any,
    user_id: int,
    project_id: int | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Analyse a conversation and return verified memory candidates (unsaved).

    Deterministic guarantees : every returned candidate is grounded in the
    transcript, has a valid scope/target/code, and carries the existing FTS
    matches in its scope (``existing_matches`` + ``suggested_action``).
    """
    transcript = _transcript(messages)
    if len(_norm(transcript)) < MIN_QUOTE_CHARS:
        return []

    try:
        resp = llm.chat_messages(
            messages=[
                {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Conversation transcript:\n\n{transcript}"},
            ],
            tools=[],
            temperature=0.0,
            thinking=False,
            model=model,
            format="json",
        )
        data = json.loads(resp.content or "{}")
        raw = data.get("candidates", [])
    except Exception as exc:  # noqa: BLE001 — best-effort ; the turn already succeeded
        _log.debug("consolidation propose failed: %s", exc)
        return []

    norm_groundable = _groundable(messages)  # user + tool only (anti-GIGO)
    out: list[dict[str, Any]] = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        scope = c.get("scope")
        if scope not in memory.VALID_SCOPES:
            continue
        code = (c.get("code") or "").strip()
        title = c.get("title") or ""
        description = c.get("description") or ""
        content = c.get("content") or ""
        quote = c.get("grounding_quote") or ""
        tool_code = (c.get("tool_code") or "").strip() or None

        # Code must be a valid kebab slug.
        if not code or " " in code:
            continue
        # Grounding gate : the quote must appear in a USER message or TOOL result
        # (never the assistant's own claims → anti-hallucination at the source).
        nq = _norm(quote)
        if len(nq) < MIN_QUOTE_CHARS or nq not in norm_groundable:
            continue

        target = _target_for(scope, project_id=project_id, tool_code=tool_code)
        if target is None:
            continue  # scope can't be satisfied in this conversation (e.g. project w/o project)
        if scope == "user":
            target = {"user_id": user_id}

        title = _clamp(title, memory.MAX_TITLE_CHARS)
        description = _clamp(description, memory.MAX_DESCRIPTION_CHARS)
        content = _clamp(content, memory.MAX_CONTENT_CHARS)
        if not (title and description and content):
            continue

        # Deterministic dedup / contradiction surfacing.
        existing = memory.recall(conn, scope=scope, code=code, **target)
        try:
            matches = memory.search(
                conn, query=f"{title} {description}", scope=scope, limit=3, **target
            )
        except memory.MemoryOpError:
            matches = []
        if existing is not None:
            action = "extend"
        elif matches:
            action = "review"
        else:
            action = "new"

        out.append({
            "scope": scope,
            "code": code,
            "title": title,
            "description": description,
            "content": content,
            "grounding_quote": quote.strip(),
            "tool_code": tool_code,
            "project_id": project_id if scope == "project" else None,
            "suggested_action": action,
            "existing_matches": [
                {k: m.get(k) for k in ("code", "title", "description", "score")} for m in matches
            ],
        })
        if len(out) >= MAX_CANDIDATES:
            break
    return out


# ---- pending persistence (DB table pending_consolidation, per conversation) ----

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dedup_key(c: dict[str, Any]) -> str:
    """Stable upsert key. fact → scope/code/target ; rule → section/category/title-slug."""
    if c.get("kind") == "rule":
        slug = re.sub(r"[^a-z0-9]+", "-", (c.get("title") or "").lower()).strip("-")
        return f"rule/{c.get('section_code') or ''}/{c.get('category_code') or ''}/{slug}"
    return f"fact/{c.get('scope')}/{c.get('code')}/{c.get('project_id')}/{c.get('tool_code')}"


def load_pending(conv_id: str) -> list[dict[str, Any]]:
    """The conversation's candidates still awaiting review (status='pending'), oldest first."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM pending_consolidation "
            "WHERE conversation_id=? AND status='pending' ORDER BY id",
            (conv_id,),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def add_pending(conv_id: str, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upsert candidates (dedup by (conv, dedup_key)). A *pending* row is refreshed ; an
    already applied/dismissed one is NOT resurfaced (terminal — the WHERE guards it).
    Returns the current pending set."""
    if new:
        now = _now()
        with db.connect() as conn:
            for c in new:
                conn.execute(
                    "INSERT INTO pending_consolidation "
                    "(conversation_id, kind, dedup_key, payload, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?) "
                    "ON CONFLICT(conversation_id, dedup_key) DO UPDATE SET "
                    "payload=excluded.payload, kind=excluded.kind, created_at=excluded.created_at "
                    "WHERE status='pending'",
                    (conv_id, c.get("kind", "fact"), _dedup_key(c),
                     json.dumps(c, ensure_ascii=False), now),
                )
    return load_pending(conv_id)


def _set_status(conv_id: str, candidate: dict[str, Any], status: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        conn.execute(
            "UPDATE pending_consolidation SET status=? WHERE conversation_id=? AND dedup_key=?",
            (status, conv_id, _dedup_key(candidate)),
        )
    return load_pending(conv_id)


def remove_pending(conv_id: str, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark a candidate dismissed (the human reviewed + skipped it). Idempotent. Returns
    what remains pending."""
    return _set_status(conv_id, candidate, "dismissed")


def mark_applied(conv_id: str, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark a candidate applied (its content was written to memory/paradigms)."""
    return _set_status(conv_id, candidate, "applied")


def clear_pending(conv_id: str) -> None:
    """Delete ALL queued candidates for a conversation."""
    with db.connect() as conn:
        conn.execute("DELETE FROM pending_consolidation WHERE conversation_id=?", (conv_id,))


# ---- shadow entry point (called by the CLI / API after the response) ------

def run_shadow(
    conv_folder: Path,
    conv_id: str,
    *,
    llm: Any,
    user_id: int | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """End-of-turn reflection beat : propose + stash to the pending queue (DB). Returns NEW
    candidates.

    Best-effort : never raises (the turn already succeeded). Resolves the
    conversation's project + the memory owner itself, so callers only inject
    the conversation handle + an LLM.
    """
    try:
        messages = persistence.load_messages(conv_folder)
        with db.connect() as conn:
            uid = user_id if user_id is not None else db.cli_user_id(conn)
            row = conn.execute(
                "SELECT project_id FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
            project_id = row["project_id"] if row is not None else None
            candidates = propose(
                conn, messages, llm=llm, user_id=uid, project_id=project_id, model=model
            )
        if candidates:
            add_pending(conv_id, candidates)
        return candidates
    except Exception as exc:  # noqa: BLE001
        _log.debug("shadow consolidation failed: %s", exc)
        return []


def apply_candidate(
    conn: Any,
    candidate: dict[str, Any],
    *,
    action: str,
    user_id: int,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """Write an (optionally edited) candidate to memory. ``action`` is 'save' or 'extend'.

    Reuses ``service.memory`` (single validation source). For 'extend' it updates
    the existing entry sharing the candidate's (scope, target, code)."""
    scope = candidate["scope"]
    target: dict[str, Any] = {}
    if scope == "user":
        target = {"user_id": user_id}
    elif scope == "project":
        target = {"project_id": candidate.get("project_id")}
    elif scope == "tool":
        target = {"tool_code": candidate.get("tool_code")}
    t = title if title is not None else candidate["title"]
    d = description if description is not None else candidate["description"]
    ct = content if content is not None else candidate["content"]
    if action == "extend":
        memory.update(conn, scope=scope, code=candidate["code"],
                      title=t, description=d, content=ct, **target)
        return {"action": "extend", "scope": scope, "code": candidate["code"]}
    saved = memory.save(conn, scope=scope, code=candidate["code"],
                        title=t, description=d, content=ct, **target)
    return {"action": "save", **saved}


# ---- in-turn capture (the propose_memory tool) ----------------------------

def add_candidate(
    conv_id: str,
    *,
    scope: str,
    code: str,
    title: str,
    description: str,
    content: str,
    user_id: int,
    project_id: int | None = None,
    tool_code: str | None = None,
    grounding_quote: str = "",
    importance: int = 3,
) -> dict[str, Any]:
    """Validate + dedup + stash ONE agent-proposed fact candidate (the propose_memory tool).

    Deterministic (no LLM) : same dedup as ``propose`` (existing entry → 'extend' ;
    FTS matches → 'review' ; else 'new'). NOTHING is written to memory — the candidate
    lands in the pending queue for human review. Raises ``memory.MemoryOpError`` on an
    invalid scope / unsatisfiable target / missing fields. Returns the stored candidate."""
    if scope not in memory.VALID_SCOPES:
        raise memory.MemoryOpError(
            "invalid_scope", f"scope must be one of {sorted(memory.VALID_SCOPES)}.", received=scope
        )
    code = (code or "").strip()
    if not code or " " in code:
        raise memory.MemoryOpError(
            "invalid_code", "code is required and must be kebab-case (no spaces)."
        )
    title = _clamp(title, memory.MAX_TITLE_CHARS)
    description = _clamp(description, memory.MAX_DESCRIPTION_CHARS)
    content = _clamp(content, memory.MAX_CONTENT_CHARS)
    if not (title and description and content):
        raise memory.MemoryOpError("invalid_args", "title, description and content are required.")

    target = _target_for(scope, project_id=project_id, tool_code=tool_code)
    if target is None:
        raise memory.MemoryOpError(
            "invalid_target", f"scope '{scope}' can't be satisfied in this conversation.", scope=scope
        )
    if scope == "user":
        target = {"user_id": user_id}
    imp = max(1, min(5, int(importance or 3)))

    with db.connect() as conn:
        existing = memory.recall(conn, scope=scope, code=code, **target)
        try:
            matches = memory.search(conn, query=f"{title} {description}", scope=scope, limit=3, **target)
        except memory.MemoryOpError:
            matches = []
    action = "extend" if existing is not None else ("review" if matches else "new")

    candidate = {
        "kind": "fact", "scope": scope, "code": code, "title": title,
        "description": description, "content": content,
        "grounding_quote": grounding_quote.strip(), "tool_code": tool_code,
        "project_id": project_id if scope == "project" else None,
        "importance": imp, "suggested_action": action,
        "existing_matches": [
            {k: m.get(k) for k in ("code", "title", "description", "score")} for m in matches
        ],
    }
    add_pending(conv_id, [candidate])
    return candidate

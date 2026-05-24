"""Tool: plan_update — mechanical create/patch of workspace/plan.md."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ._base import ToolSpec
from ._workspace import safe_resolve, workspace_root_for

_PLAN_FILENAME = "plan.md"
_STATUS_MAP = {
    "pending":     "⬜ pending",
    "in_progress": "🟡 in_progress",
    "done":        "✅ done",
    "blocked":     "🔴 blocked",
}

# Regex matching any status bracket: [⬜ pending], [✅ done], etc.
_STATUS_BRACKET_RE = re.compile(r'\[[^\]]+\]$')


class _PlanError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _step_heading_re(step_id: str) -> re.Pattern[str]:
    return re.compile(
        rf'^(#{{2,}})\s+{re.escape(step_id)}\s+—\s+(.+?)\s+\[([^\]]+)\]',
        re.IGNORECASE,
    )


_ANY_STEP_HEADING_RE = re.compile(r'^#{3,}\s+([A-Za-z0-9.]+)\s+—')


def _list_step_ids(plan_path: Path) -> list[str]:
    if not plan_path.exists():
        return []
    out: list[str] = []
    for ln in plan_path.read_text(encoding="utf-8").splitlines():
        m = _ANY_STEP_HEADING_RE.match(ln)
        if m:
            out.append(m.group(1))
    return out


def _build_plan_md(title: str, steps: list[dict], created: str, updated: str) -> str:
    lines = [
        f"# Plan — {title}",
        "",
        f"_Created: {created} · Last updated: {updated}_",
        "",
        "## Steps",
        "",
    ]
    for idx, step in enumerate(steps, start=1):
        # Auto-numbered ids: S1, S2, S3, … (ignore any id supplied by the caller
        # to avoid LLMs inventing inconsistent ids like 'step_1' or 'root').
        sid = f"S{idx}"
        stitle = step.get("title", "")
        agent = step.get("agent", "")
        deliverable = step.get("deliverable", "")
        depth = sid.count(".")
        hashes = "###" + "#" * depth
        lines.append(f"{hashes} {sid} — {stitle} [{_STATUS_MAP['pending']}]")
        if agent:
            lines.append(f"- Agent: `{agent}`")
        if deliverable:
            lines.append(f"- Deliverable: `{deliverable}`")
        lines.append("")
    lines += [
        "## Revision log",
        f"- {created} · init · Created plan with {len(steps)} step(s)",
    ]
    return "\n".join(lines) + "\n"


def _update_header_timestamp(lines: list[str], now: str) -> None:
    header_re = re.compile(r'^_Created: (.+?) · Last updated: .+?_$')
    for i, line in enumerate(lines):
        m = header_re.match(line.rstrip("\n"))
        if m:
            lines[i] = f"_Created: {m.group(1)} · Last updated: {now}_\n"
            return


def _append_revision(lines: list[str], entry: str) -> None:
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "## Revision log":
            lines.insert(i + 1, entry if entry.endswith("\n") else entry + "\n")
            return
    lines.append(entry if entry.endswith("\n") else entry + "\n")


def _do_init(plan_path: Path, title: str = "", steps: list | None = None,
             new_steps: list | None = None, **_) -> str:
    if plan_path.exists():
        # Idempotent: plan already exists — return its current content so the
        # caller can proceed without looping on an error.
        existing = _list_step_ids(plan_path)
        content = plan_path.read_text(encoding="utf-8")
        return json.dumps({
            "action": "init",
            "already_exists": True,
            "step_ids": existing,
            "content": content,
        })
    if not title:
        raise _PlanError("'title' is required for action='init'.")
    # Accept new_steps as an alias for steps (LLMs frequently confuse them).
    if not steps and new_steps:
        steps = new_steps
    steps = steps or []
    if not steps:
        raise _PlanError(
            "action='init' requires at least one entry in 'steps' "
            "(an array of {title, agent?, deliverable?}). "
            "Got 0 steps — refusing to create an empty plan."
        )
    now = _now_iso()
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(_build_plan_md(title, steps, created=now, updated=now),
                         encoding="utf-8")
    step_ids = [f"S{i}" for i in range(1, len(steps) + 1)]
    return json.dumps({"action": "init", "path": _PLAN_FILENAME,
                       "steps_created": len(steps), "step_ids": step_ids})


def _do_mark(plan_path: Path, step_id: str = "", status: str = "",
             findings: str | None = None, **_) -> str:
    if not step_id:
        raise _PlanError("'step_id' is required for action='mark'.")
    if status and status not in _STATUS_MAP:
        raise _PlanError(f"Invalid status {status!r}. Valid: {list(_STATUS_MAP)}")
    if findings is not None:
        if not isinstance(findings, str) or not findings.strip():
            raise _PlanError(
                "'findings' must be a non-empty string when provided. "
                f"Got: {type(findings).__name__} = {repr(findings)[:80]}"
            )
    if not plan_path.exists():
        raise _PlanError("plan.md does not exist. Call plan_update(action='init') first.")

    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    step_re = _step_heading_re(step_id)

    step_line_idx = next(
        (i for i, ln in enumerate(lines) if step_re.match(ln)), None
    )
    if step_line_idx is None:
        raise _PlanError(
            f"Step '{step_id}' not found in plan.md. "
            f"Available step_ids: {_list_step_ids(plan_path)}."
        )

    # Update status in the heading line
    if status:
        stripped = lines[step_line_idx].rstrip("\n")
        new_heading = _STATUS_BRACKET_RE.sub(f"[{_STATUS_MAP[status]}]", stripped) + "\n"
        lines[step_line_idx] = new_heading

    # Find the end of this step's top-level section (next ## or ### heading)
    section_end = len(lines)
    for i in range(step_line_idx + 1, len(lines)):
        if re.match(r'^#{2,3}\s', lines[i]):
            section_end = i
            break

    # Inject/replace findings
    if findings:
        findings_header = f"#### Findings ({step_id})\n"
        findings_re = re.compile(rf'^#### Findings \({re.escape(step_id)}\)', re.IGNORECASE)

        findings_start = next(
            (i for i, ln in enumerate(lines) if findings_re.match(ln)), None
        )
        if findings_start is not None:
            # Replace content until next #### or end of section
            findings_end = section_end
            for i in range(findings_start + 1, section_end):
                if re.match(r'^#{4}\s', lines[i]):
                    findings_end = i
                    break
            new_block = [findings_header, findings.rstrip("\n") + "\n", "\n"]
            lines[findings_start:findings_end] = new_block
        else:
            # Insert right before section_end (end of the step's block)
            new_block = ["\n", findings_header, findings.rstrip("\n") + "\n", "\n"]
            lines[section_end:section_end] = new_block

    now = _now_iso()
    parts = []
    if status:
        parts.append(f"status={_STATUS_MAP[status]}")
    if findings:
        parts.append("findings injected")
    _append_revision(lines, f"- {now} · mark · {step_id}: {'; '.join(parts) or 'marked'}")
    _update_header_timestamp(lines, now)

    plan_path.write_text("".join(lines), encoding="utf-8")
    return json.dumps({"action": "mark", "step_id": step_id,
                       "status": status or "(unchanged)", "findings": bool(findings)})


def _do_add_substep(plan_path: Path, parent_step_id: str = "",
                    title: str = "", reason: str = "", **_) -> str:
    if not parent_step_id:
        raise _PlanError("'parent_step_id' is required for action='add_substep'.")
    if not title:
        raise _PlanError("'title' is required for action='add_substep'.")
    if not plan_path.exists():
        raise _PlanError("plan.md does not exist. Call plan_update(action='init') first.")

    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Determine next substep number
    substep_re = re.compile(
        rf'^#{{3,}}\s+{re.escape(parent_step_id)}\.(\d+)\s+—', re.IGNORECASE,
    )
    max_num = max((int(m.group(1)) for ln in lines if (m := substep_re.match(ln))), default=0)
    new_id = f"{parent_step_id}.{max_num + 1}"

    # Locate parent step heading
    parent_re = _step_heading_re(parent_step_id)
    parent_line_idx = next(
        (i for i, ln in enumerate(lines) if parent_re.match(ln)), None
    )
    if parent_line_idx is None:
        raise _PlanError(
            f"Parent step '{parent_step_id}' not found in plan.md. "
            f"Available step_ids: {_list_step_ids(plan_path)}."
        )

    # Insert before next ## or ### heading (substeps at #### stay inside the block)
    insert_idx = len(lines)
    for i in range(parent_line_idx + 1, len(lines)):
        if re.match(r'^#{2,3}\s', lines[i]):
            insert_idx = i
            break

    depth = new_id.count(".")
    hashes = "###" + "#" * depth
    new_block = [
        "\n",
        f"{hashes} {new_id} — {title} [{_STATUS_MAP['pending']}]\n",
        f"- Parent: {parent_step_id}\n",
    ]
    if reason:
        new_block.append(f"- Reason: {reason}\n")
    new_block.append("\n")
    lines[insert_idx:insert_idx] = new_block

    now = _now_iso()
    _append_revision(lines, f"- {now} · add_substep · {new_id}: {title}")
    _update_header_timestamp(lines, now)

    plan_path.write_text("".join(lines), encoding="utf-8")
    return json.dumps({"action": "add_substep", "new_step_id": new_id, "title": title})


def _do_reset(plan_path: Path, ws_root: Path,
              title: str = "", new_steps: list | None = None,
              steps: list | None = None, **_) -> str:
    if not title:
        raise _PlanError("'title' is required for action='reset'.")
    # Accept steps as alias for new_steps (symmetrical with init).
    if not new_steps and steps:
        new_steps = steps
    new_steps = new_steps or []
    if not new_steps:
        raise _PlanError(
            "action='reset' requires at least one entry in 'new_steps' "
            "(an array of {title, agent?, deliverable?}). "
            "Got 0 steps — refusing to overwrite the plan with an empty one."
        )
    archive_name: str | None = None
    if plan_path.exists():
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        archive_name = f"plan.archive.{ts}.md"
        (ws_root / archive_name).write_text(
            plan_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    now = _now_iso()
    plan_path.write_text(_build_plan_md(title, new_steps, created=now, updated=now),
                         encoding="utf-8")
    step_ids = [f"S{i}" for i in range(1, len(new_steps) + 1)]
    return json.dumps({"action": "reset", "archive": archive_name,
                       "steps_created": len(new_steps), "step_ids": step_ids})


def _do_read(plan_path: Path) -> str:
    if not plan_path.exists():
        return json.dumps({"error": "plan.md does not exist."})
    return json.dumps({"action": "read", "content": plan_path.read_text(encoding="utf-8")})


def make_spec(conv_folder: Path, has_write_grant: bool = False,
              agent_role: str = "specialist") -> ToolSpec:
    _WRITE_ACTIONS = {"init", "mark", "add_substep", "reset"}

    def _handler(action: str, **kwargs) -> str:
        if action in _WRITE_ACTIONS and agent_role != "router":
            return json.dumps({
                "error": (
                    f"action='{action}' is reserved for the router (jean-michel). "
                    "Specialists may only call plan_update(action='read'). "
                    "Use report_findings to surface findings to the router; "
                    "the router will update the plan."
                ),
                "error_code": "plan_write_forbidden_for_specialist",
            })
        if action not in ("read",) and not has_write_grant:
            return json.dumps({"error": "Write access not granted for this agent."})
        ws_root = workspace_root_for(conv_folder)
        try:
            plan_path = safe_resolve(ws_root, _PLAN_FILENAME)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        try:
            if action == "init":
                return _do_init(plan_path, **kwargs)
            if action == "mark":
                return _do_mark(plan_path, **kwargs)
            if action == "add_substep":
                return _do_add_substep(plan_path, **kwargs)
            if action == "reset":
                return _do_reset(plan_path, ws_root, **kwargs)
            if action == "read":
                return _do_read(plan_path)
            return json.dumps({"error": f"unknown action: {action!r}"})
        except _PlanError as e:
            return json.dumps({"error": str(e)})

    return ToolSpec(
        name="plan_update",
        description=(
            "Create or patch the conversation's plan.md. Mechanical, deterministic. "
            "Actions: init | mark | add_substep | reset | read. "
            "Use this instead of workspace_create_file for plan.md."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["init", "mark", "add_substep", "reset", "read"],
                },
                "title": {"type": "string"},
                "steps": {
                    "type": "array",
                    "description": (
                        "Steps for action='init'. Ids are auto-assigned as "
                        "S1, S2, S3, … — do NOT include 'id'."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "agent": {"type": "string"},
                            "deliverable": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
                "step_id": {
                    "type": "string",
                    "description": "Existing step id (e.g. 'S1' or 'S1.2').",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "blocked"],
                },
                "findings": {"type": "string"},
                "parent_step_id": {
                    "type": "string",
                    "description": "Existing parent step id (e.g. 'S1').",
                },
                "reason": {"type": "string"},
                "new_steps": {
                    "type": "array",
                    "description": (
                        "Steps for action='reset'. Same shape as 'steps'; "
                        "ids auto-assigned."
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["action"],
        },
        handler=_handler,
    )

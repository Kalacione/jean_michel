# Sprint — Router-owns-the-plan + report_findings

Author: design audit 2026-05-24 (jean-michel investigation cycle).
Target executor: Claude 4.6 (autonomous coding agent).
Estimated scope: 1 large sprint, 4 self-contained sub-sprints. ~10–12 files touched, 1 DB migration, ~20 new/changed tests.

---

## 0. Decision (validated)

After observing the failure modes in conversations `2026-05-24_04-08_ca049ab8be2a`, `2026-05-24_16-39_*`, `2026-05-24_16-50_844278d9ed9e` and `2026-05-24_17-06_9f368d1c0a86`:

- **Specialists do not have the global context** to maintain `plan.md` coherently. Empirically they:
  - Re-call `plan_update(action="init")` in a loop, ignoring `already_exists: true`.
  - Pass `findings: false` (bool) instead of a synthesis string.
  - Add a single `S1.1 — Search for structured data sources by domain` substep and stop, instead of substeps per domain found.
  - Produce excellent workspace files (e.g. `gather/potential_sources.md`) but **never inject their content into the plan**.

- **The router (`jean-michel`) is the only agent with global context**. Therefore:
  - **Specialists become read-only on `plan.md`**. Their job is to do their work, produce workspace files, and report a *structured payload* back.
  - **The router owns all writes to `plan.md`**. After each specialist returns, the router synthesises what came back into `mark`/`add_substep`.
  - The introduction of a separate `planner` role (sprints A1–A2) was **rolled back conceptually**: it created a fifth point of contention on `plan.md` without solving the ownership problem. Planner stays as a phase verb (`planner_done`) but no longer holds write-grant on `plan.md` either.

- **Communication contract specialist → router** = a new dedicated control verb `report_findings` with a rich, machine-readable schema. This **replaces** `signal_convergence` everywhere (same idea, better name, fuller schema). `return_to_user` is now reserved for **router-at-depth-0 only** (final answer to the human).

- **Deepening is first-class**: the `report_findings` payload contains a `sub_questions[]` field. The router reads sub-questions, decides whether to spawn follow-up delegations, and updates the plan accordingly (`add_substep` per sub-question selected).

---

## 1. New control verb: `report_findings`

### 1.1 Schema (prompts.py)

```jsonc
{
  "type": "function",
  "function": {
    "name": "report_findings",
    "description":
      "Specialist completion verb. Use this when you finish (or hit the limit of) the work the parent agent delegated to you. "
      "Provide a structured report so the parent can update the global plan and decide what to do next. "
      "Do NOT use return_to_user — that verb is reserved for the router answering the human at the top level.",
    "parameters": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "string",
          "description":
            "One concise paragraph (3–8 sentences) summarising what you did and what you found. "
            "This text is shown to the parent agent as your tool_response."
        },
        "files_produced": {
          "type": "array",
          "items": {"type": "string"},
          "description":
            "Workspace-relative paths of files you created or modified during this turn. "
            "Used by the parent to mark deliverables in the plan."
        },
        "sub_questions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "question":         {"type": "string"},
              "why":              {"type": "string", "description": "Why this needs follow-up."},
              "suggested_agent":  {"type": "string", "description": "Optional: which specialist would best handle it."}
            },
            "required": ["question"]
          },
          "description":
            "Unresolved questions / ambiguities / promising leads that emerged. "
            "The parent decides whether to spawn follow-up delegations. Leave empty if work is fully closed."
        },
        "blockers": {
          "type": "array",
          "items": {"type": "string"},
          "description":
            "Hard blockers preventing completion (e.g. missing tool, missing grant, external service down). "
            "Empty list if none."
        },
        "confidence": {
          "type": "string",
          "enum": ["low", "medium", "high"],
          "description":
            "Self-assessment of completeness against the briefing. "
            "low = significant gaps, medium = main goal met but some uncertainty, high = fully delivered."
        }
      },
      "required": ["summary", "confidence"]
    }
  }
}
```

### 1.2 Role grants (prompts.py `_CONTROL_TOOLS_BY_ROLE`)

```python
_CONTROL_TOOLS_BY_ROLE = {
    "router":     [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER, _PLANNER_DONE],
    "specialist": [_ASK_HUMAN, _DELEGATE_TO, _REPORT_FINDINGS,
                   _GATHER_DONE, _CRITIC_DONE, _BUILD_DONE],
    "finalizer":  [_RETURN_TO_USER],
}
```

Changes:
- Specialists **lose** `_RETURN_TO_USER` and `_SIGNAL_CONVERGENCE`.
- Specialists **gain** `_REPORT_FINDINGS`.
- Router stays unchanged (router still uses `return_to_user` to answer the human at depth 0).
- The dynamic `signal_convergence` injection at depth ≥ 2 for routers (`prompts.py` line ~388) is **deleted** — replaced by nothing (router never needs to "converge" to anyone, only specialists report up).

### 1.3 Orchestrator interception (`orchestrator.py` ~line 572–680)

Replace the `if call.name == "signal_convergence":` block. The new handler:

```python
if call.name == "report_findings":
    summary = (call.arguments.get("summary") or "").strip()
    confidence = (call.arguments.get("confidence") or "").strip()
    files_produced = list(call.arguments.get("files_produced") or [])
    sub_questions = list(call.arguments.get("sub_questions") or [])
    blockers = list(call.arguments.get("blockers") or [])

    # Validation
    if not summary:
        tool_responses.append(json.dumps({
            "tool": "report_findings",
            "error": "summary is required and must be a non-empty string."}))
        continue
    if confidence not in {"low", "medium", "high"}:
        tool_responses.append(json.dumps({
            "tool": "report_findings",
            "error": "confidence must be one of: low, medium, high."}))
        continue
    if _looks_corrupted(summary):
        tool_responses.append(json.dumps({
            "tool": "report_findings",
            "error": "summary contains tokenisation markers."}))
        continue

    # Workspace artifact guard (reuse the one already used by phase verbs)
    ws_root = self.conv_folder / "workspace"
    missing = [p for p in files_produced if not (ws_root / p).exists()]
    if missing:
        tool_responses.append(json.dumps({
            "tool": "report_findings",
            "error": f"Declared files_produced not found on disk: {missing}."}))
        continue

    # Persist report artifact
    payload = {
        "summary": summary,
        "confidence": confidence,
        "files_produced": files_produced,
        "sub_questions": sub_questions,
        "blockers": blockers,
    }
    artifact = self._write_artifact(
        req_id, agent_code, "report",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    # Build the textual response the parent will see as the tool_response of delegate_to
    parent_view = _format_report_for_parent(payload)
    # _format_report_for_parent: helper that renders the payload as a markdown block
    # with sections: ## Summary, ## Files produced, ## Sub-questions, ## Blockers, ## Confidence.
    # Crucially, the parent must see sub_questions and files_produced prominently.

    with db.connect() as conn:
        db.update_request_status(conn, req_id, "completed", completed=True)
    return parent_view, artifact, True   # True = was-convergent-style (terminates this request)
```

Also:
- Forbid `return_to_user` for non-router non-finalizer agents (specialists) — return an error tool_response steering them to `report_findings`.
- Keep `_PHASE_VERBS` (`gather_done`, `critic_done`, `build_done`) — they are orthogonal pipeline-state markers and still useful.

### 1.4 `delegate_to.expected.completion_verb` (prompts.py ~line 95–105)

Update the enum:
```python
"enum": ["gather_done", "critic_done", "build_done",
         "return_to_user", "report_findings"]
```
Remove `signal_convergence`. Add `report_findings`. Update the description of the parameter to recommend `report_findings` as the default completion verb for specialist delegations.

### 1.5 Helper `_format_report_for_parent`

Place in `orchestrator.py` near other private helpers. Example output:

```markdown
## Report from web-search-specialist (confidence: medium)

### Summary
Searched 5 domains for programmable data sources. Identified 28 candidates, wrote
gather/potential_sources.md with a categorised table. Wikidata and OpenAlex stand out for
structured-knowledge use cases. arXiv RSS is suitable for scientific monitoring.

### Files produced
- gather/potential_sources.md

### Sub-questions (3)
1. Should we restrict to free-tier APIs only? (suggested_agent: ask_human)
2. How does Wikidata Enterprise differ from public Wikidata for AI agents? (suggested_agent: wikipedia-specialist)
3. Are RSS feeds still maintained by the listed news APIs? (suggested_agent: web-search-specialist)

### Blockers
None.
```

This is what the parent sees in its context as the `tool_response` of `delegate_to(child)`. The format is deliberately verbose: the router must be able to act on it without re-querying the child.

---

## 2. Plan ownership: router-only writes

### 2.1 `plan_update` action-level role restriction (`tools/plan_update.py`)

Change `make_spec` signature:

```python
def make_spec(conv_folder: Path,
              has_write_grant: bool = False,
              agent_role: str = "specialist") -> ToolSpec:
```

Inside the handler:

```python
WRITE_ACTIONS = {"init", "mark", "add_substep", "reset"}

def _handler(action: str, **kwargs) -> str:
    if action in WRITE_ACTIONS and agent_role != "router":
        return json.dumps({
            "error": (
                f"action='{action}' is reserved for the router (jean-michel). "
                "Specialists may only call plan_update(action='read'). "
                "Use report_findings to surface findings to the router; "
                "the router will update the plan."
            ),
            "error_code": "plan_write_forbidden_for_specialist",
        })
    if action != "read" and not has_write_grant:
        return json.dumps({"error": "Write access not granted for this agent."})
    # ... existing dispatch ...
```

### 2.2 Plumbing the agent role

- `tools/__init__.py` `build_registry` already receives most context; add an `agent_role: str = "specialist"` parameter and forward it to `_plan_update_mod.make_spec(..., agent_role=agent_role)`.
- `orchestrator.py` line ~448, the `build_registry(...)` call: pass `agent_role=agent.role`. The `agent` row already comes from the DB and has a `role` column.

### 2.3 `_do_init` stays idempotent (no rollback)

Specialists can no longer call `init` anyway. The idempotent return (`already_exists: true`) remains for the router; it's harmless and prevents accidental re-init from breaking a request.

### 2.4 DB grants — DO NOT REVOKE `plan_update` from specialists

Specialists still need read access. The restriction is enforced **at the tool handler level**, not at the grant level. Keep `agent_tools(specialist, plan_update)` rows intact.

---

## 3. Forced router synthesis after specialist return

### 3.1 Mechanism (orchestrator.py)

When a specialist completes via `report_findings`, the router's next tool_call is observed. If it is **not** one of `{plan_update, delegate_to, ask_human, return_to_user}`, the orchestrator injects a system reminder once:

```
You just received a report from <child_agent>. Before doing anything else,
call plan_update(action='mark', step_id='<the step you delegated>', status=...,
findings='<one-line synthesis of the report>'). If the report included
sub_questions, add them via plan_update(action='add_substep', parent_step_id=...).
Only then continue with the next delegation.
```

Implementation:
- Add a transient state to the orchestrator's per-request loop: `_pending_synthesis: str | None = None` (holds the child agent code).
- Set it when `delegate_to` returns from a specialist (after the `report_findings` is consumed back in the parent's context).
- Clear it the moment the router calls `plan_update` with `action in {"mark", "add_substep"}`.
- On the next tool_call, if `_pending_synthesis` is still set AND the call is not in the allowed list, inject the reminder as a synthetic tool_response and **do not execute the call** (let the next LLM turn re-decide). Cap at 1 reminder per pending synthesis to avoid loops.

### 3.2 Yielded event

Add a new event dataclass:

```python
@dataclass
class SynthesisReminderInjected:
    agent_code: str         # the router
    child_agent_code: str   # the specialist that just returned
```

Yielded right after the synthetic tool_response, before the next LLM call. Used by the CLI to show `⚠ synthesis reminder · <child>`.

### 3.3 Paradigm update

Update `router_synthesis_discipline` (new paradigm) or add bullets to `task_plan_file`:

```
- After a specialist returns via report_findings, your FIRST action MUST be:
  plan_update(action='mark', step_id=<the step you delegated>, status=...,
              findings=<one-line synthesis of summary, files_produced, key sub_questions>).
- If the report contains sub_questions that you decide to follow up on, add each
  as a substep: plan_update(action='add_substep', parent_step_id=..., title=..., reason=...).
- Only after the plan reflects the new state may you delegate again or answer the human.
```

---

## 4. Minor bugs to fix in this same sprint

### 4.1 `plan_update(mark, findings=false)` — type validation

In `_do_mark` (`tools/plan_update.py`):

```python
if "findings" in kwargs and kwargs["findings"] is not None:
    f = kwargs["findings"]
    if not isinstance(f, str) or not f.strip():
        raise _PlanError(
            "'findings' must be a non-empty string when provided. "
            "Got: " + type(f).__name__ + " = " + repr(f)[:80]
        )
```

Test: pass `findings=False` and `findings=""` and `findings=123` — all must error.

### 4.2 Loop on idempotent `init`

In the orchestrator's per-request loop, add `_idempotent_init_count: int = 0`. After every tool_response of `plan_update`, if the parsed JSON has `"already_exists": true`:
- Increment counter.
- 1st time: warn via existing log only.
- 2nd time: inject reminder `"You already received the existing plan via init. Call plan_update(action='read') or proceed with mark/add_substep instead of init."`
- 3rd time: fail-fast → mark request `failed` with reason `"plan_init_loop"`.

Yield a `PlanInitLoopDetected(count: int)` event when ≥ 2.

### 4.3 `findings: false` reaching the file

Already covered by 4.1 once `_do_mark` validates.

### 4.4 `report_findings` payload validates `files_produced` paths

Already in 1.3 — guard against `..`, absolute paths, and missing files. Re-use `safe_resolve` from `tools/_workspace.py`.

### 4.5 Remove dead code

- `_SIGNAL_CONVERGENCE` constant in `prompts.py` (orphan after step 1).
- `signal_convergence` branch in orchestrator (replaced by `report_findings`).
- The dynamic `signal_convergence` injection for routers at depth ≥ 2.

Grep first to make sure nothing else references it; tests using the old verb must be migrated to the new one.

---

## 5. DB migration `049_router_owns_plan.sql`

```sql
-- migrate_049: router-owns-the-plan paradigm rewrite.
-- 1. Update task_plan_file: specialists read, router writes.
-- 2. Add router_synthesis_discipline paradigm.
-- 3. Grant router_synthesis_discipline to jean-michel.

UPDATE paradigms SET content =
'- Plan ownership: plan.md belongs to the router (jean-michel). Only the router writes to it.
- Specialists may call plan_update(action="read") to inspect the plan, never the write actions.
- Specialists report their findings via the report_findings control verb (not return_to_user, not signal_convergence).
- The router reads each report_findings response and updates plan.md via plan_update(action="mark", ...) and plan_update(action="add_substep", ...).
- Step ids are auto-assigned (S1, S2, S3, …). Never invent ids; only use those returned by plan_update or visible in the plan.
- plan_update(action="init") is idempotent: if a plan already exists it is returned as-is.'
WHERE code = 'task_plan_file';

INSERT INTO paradigms (code, category_code, content)
VALUES (
  'router_synthesis_discipline',
  'orchestration',
  '- After any specialist returns via report_findings, your FIRST tool_call MUST be plan_update(action="mark", step_id=..., status=..., findings=<one-line synthesis>).
- If the report contains sub_questions you decide to follow up on, add each via plan_update(action="add_substep", parent_step_id=..., title=..., reason=...).
- Only then may you delegate again or call return_to_user.
- The findings field must capture: (a) what was produced (files_produced), (b) the headline finding, (c) the most important sub_question if any. Be specific. "Done" is not a valid synthesis.'
);

-- Grant the new paradigm to the router only.
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE a.code = 'jean-michel' AND p.code = 'router_synthesis_discipline';
```

(Verify the exact category_code by reading `db/schema.sql` — use the same category as `task_plan_file` if `orchestration` does not exist.)

---

## 6. Tests

### 6.1 New test file `tests/test_report_findings.py`

- `test_report_findings_validates_summary_required`
- `test_report_findings_validates_confidence_enum`
- `test_report_findings_rejects_corrupted_summary`
- `test_report_findings_rejects_missing_files_produced`
- `test_report_findings_terminates_specialist_request`
- `test_report_findings_payload_visible_to_parent` — script a MockClient where router delegates, specialist calls `report_findings(...)`, then assert the parent's next prompt context contains the rendered report markdown.
- `test_specialist_cannot_call_return_to_user` — error message steers to `report_findings`.

### 6.2 Extend `tests/test_plan_update.py`

- `test_plan_update_specialist_cannot_init`
- `test_plan_update_specialist_cannot_mark`
- `test_plan_update_specialist_cannot_add_substep`
- `test_plan_update_specialist_cannot_reset`
- `test_plan_update_specialist_can_read`
- `test_plan_update_router_can_all_actions`
- `test_mark_rejects_non_string_findings` (False, 123, [], "")

### 6.3 New test file `tests/test_router_synthesis_discipline.py`

- `test_reminder_injected_when_router_skips_plan_update_after_specialist`
- `test_no_reminder_when_router_calls_plan_update_mark_first`
- `test_reminder_cap_at_one_per_pending_synthesis`
- `test_idempotent_init_warns_then_fails_at_third_call`

### 6.4 Update existing tests

- All tests that use `signal_convergence` → migrate to `report_findings`.
- `tests/test_phase_verbs.py` and `tests/test_filesystem_failfast.py` — re-check that the new role enforcement doesn't break the fixtures.

### 6.5 Acceptance smoke

Update `tests/smoke.py` (legacy integration smoke) to script:
1. Router delegates to web-search-specialist with `expected.completion_verb = "report_findings"`.
2. Specialist calls `web_search` once, then `report_findings(summary=..., files_produced=["gather/x.md"], sub_questions=[{"question":"y","why":"z"}], confidence="medium")`.
3. Assert the workspace file exists (test pre-writes it).
4. Router's next prompt contains the rendered report.
5. Router's next tool_call is `plan_update(action="mark", ...)` (script the mock that way).
6. No `signal_convergence` anywhere.

---

## 7. Order of execution (for Claude 4.6)

1. **Branch & baseline**: ensure `pytest tests/ -q` passes (`297 passed` currently). Run `ruff check src/ tests/` — note pre-existing UP043 / I001 warnings, leave them.
2. **Sub-sprint A**: tool-level role restriction.
   - `tools/plan_update.py` — add `agent_role` param, action check, findings validation.
   - `tools/__init__.py` — propagate `agent_role`.
   - `orchestrator.py` — pass `agent.role` to `build_registry`.
   - Tests 6.2.
   - `pytest tests/test_plan_update.py -v` must be green.
3. **Sub-sprint B**: new control verb.
   - `prompts.py` — add `_REPORT_FINDINGS`, remove `_SIGNAL_CONVERGENCE` from specialists, keep `_RETURN_TO_USER` router-only-effectively. Update `_DELEGATE_TO.expected.completion_verb` enum.
   - `orchestrator.py` — new interception branch, removal of `signal_convergence` branch, helper `_format_report_for_parent`, refuse `return_to_user` from specialists.
   - Tests 6.1.
   - Full `pytest tests/ -q` must be green (after migrating any test that still references `signal_convergence`).
4. **Sub-sprint C**: router synthesis discipline.
   - `orchestrator.py` — `_pending_synthesis` state, reminder injection, `SynthesisReminderInjected` + `PlanInitLoopDetected` events.
   - `cli/` — render the two new events (single emoji line each).
   - DB migration 049.
   - Tests 6.3.
5. **Sub-sprint D**: cleanup + smoke.
   - Remove dead `_SIGNAL_CONVERGENCE` constant once 100% of tests use the new verb.
   - Update `tests/smoke.py`.
   - `ruff check` — only pre-existing warnings remain.
6. Run the full test suite. Expected count: ~315–320 tests (297 baseline + ~20 new − ~2 deletions).
7. Manual integration: run `./jm.sh` on the deep_research prompt that triggered conv `2026-05-24_17-06`. Verify:
   - No `report_findings` from router.
   - No `return_to_user` from specialists.
   - After every specialist return, the router's first tool_call is `plan_update(mark, ...)` with a real findings string.
   - `plan.md` ends with substeps per domain searched and findings referencing the workspace files.

---

## 8. Out of scope (explicit non-goals)

- Wall-clock timeout per LLM call (audit §2.1) — separate sprint.
- Semantic loop detection beyond the existing duplicate filter (audit §3.1) — separate sprint.
- Workspace path normalisation `workspace/foo` → `foo` (audit §2.3) — already partially done in sprint 10; revisit only if tests still flag it.
- Re-introducing planner as a write-grant holder on plan.md — explicitly rejected.
- Mandatory pipeline GATHER→CRITIQUE→BUILD enforcement — already exists (`migrate_045_pipeline_enforcement.sql`); this sprint composes with it, does not replace it.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM keeps emitting `signal_convergence` from training inertia. | Orchestrator must catch the literal name, return an error `tool_response` that steers to `report_findings`, and never silently accept it. Add a test. |
| Router does not actually synthesise after the reminder (just calls `mark(findings="ok")`). | The plan asks for content quality, but enforcement is paradigm-level only. If observed empirically, add a min-length check on `findings` in `_do_mark` (e.g. ≥ 20 chars and ≠ "ok"). Out of scope for this sprint unless it reproduces. |
| `files_produced` is empty when the specialist genuinely produced files but forgot to declare them. | The router-visible markdown also shows "Files produced: none" prominently — if the router sees it but the workspace has new files, that is a paradigm violation surfaced to the human via `plan.md`. Acceptable for now. |
| Existing conversations replayed with the new code break. | This codebase has no conversation replay path; only schema. No risk. |

---

## 10. Definition of done

- All four sub-sprints merged.
- Full `pytest tests/ -q` green.
- `ruff check src/ tests/` — only pre-existing UP043 / I001 warnings.
- DB migration 049 applied; `task_plan_file` and `router_synthesis_discipline` paradigms verifiable via `sqlite3 jeanmichel.db "SELECT code, substr(content,1,80) FROM paradigms WHERE code IN ('task_plan_file','router_synthesis_discipline');"`.
- One live integration run on a deep_research prompt where:
  - `plan.md` final version contains substeps and real findings per delegated specialist.
  - No `signal_convergence` artifact anywhere on disk in the new conversation.
  - No `return_to_user` artifact from any non-router agent in the new conversation.

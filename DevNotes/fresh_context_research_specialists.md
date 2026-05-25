# Fresh-context iteration for research specialists

**Status**: draft spec, not yet implemented
**Inspiration**: Ralph (snarktank/ralph) — fresh AI instance per iteration, memory externalised to files
**Problem solved**: 120s LLM timeout when a research specialist accumulates 8+ tool calls in a single context

---

## Problem statement

Today, when `web-search-specialist` (or `wikipedia-specialist`) does N iterations of `tool_call → tool_response → thought`, the conversation history grows linearly inside `messages`. By iteration 8 the prompt reaches ~25 KB and Ollama times out at 120 s.

The Phase 6 patch (bounded re-injection, identity-arg fingerprint) is a mitigation — it slows the growth on duplicates but does not address the root cause.

The root cause is the accumulative context model. We want a structural fix for research specialists, while keeping the current model for router / finalizer / one-shot specialists.

---

## Design — "fresh iteration" mode

A new value for `agents.context_mode`:

| Mode | Behaviour |
|---|---|
| `accumulative` (default) | Current. Each turn appends to `messages`. |
| `fresh_iteration` | At each iteration, `messages` is rebuilt from scratch from a notes file. |

Activated initially on **3 agents** only:
- `web-search-specialist`
- `wikipedia-specialist`
- `comparator-specialist`

All other agents (router, finalizer, one-shot specialists, critical-thinker, document-builder, code-runner, …) remain `accumulative` — they don't suffer the same iteration-count problem and benefit from accumulative chain-of-thought.

---

## The notes file — `research_notes.md`

One file per delegation, in the conversation workspace. Structure is fixed and parts are owned by either the orchestrator or the LLM.

```markdown
# Briefing
[copied verbatim from the delegate_to briefing — invariant]

# Open questions
- [Q1] Quelles sources fournissent des données scientifiques structurées ?
- [Q2] Quelles licences sont compatibles avec un usage commercial ?

# Done
- iter 1 (00:01:23): web_search("scientific data API") → 8 hits, kept 3
- iter 2 (00:01:45): wikipedia_get_page("PubMed") → license CC0 confirmed
- iter 3 (00:02:11): update_research_notes — closed Q2, added Q3

# Citations
[1] https://arxiv.org/help/api — arXiv OAI-PMH, daily dumps
[2] https://www.ncbi.nlm.nih.gov/home/develop/api/ — PubMed E-utilities
[3] https://api.openalex.org/ — OpenAlex graph API, no key required

# Next intended action
web_search("openalex API rate limits")
```

### Ownership of each section

| Section | Owner | Mechanism |
|---|---|---|
| `# Briefing` | orchestrator | written once at delegation start |
| `# Done` | orchestrator | one line appended per successful tool execution (auto, from the existing `plan_writer` flow) |
| `# Citations` | orchestrator | auto-extracted from `tool_response` JSON of `web_search` and `wikipedia_*` tools (URL + first 80 chars of snippet) |
| `# Open questions` | LLM | written/updated via `update_research_notes` tool |
| `# Next intended action` | LLM | same tool |

**Key insight**: the orchestrator does ~80% of the maintenance deterministically. The LLM only handles the semantic 20% (which questions remain open, what to do next). This is critical because small / medium local models are unreliable at maintaining structured state.

---

## The orchestrator loop in fresh mode

```python
# Pseudocode for _run_request when agent.context_mode == "fresh_iteration"

research_notes_writer.init(conv_folder, briefing)

for step in range(STEP_BUDGET_FRESH):
    if wall_clock_exceeded(): break

    notes_md = read(conv_folder / "research_notes.md")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"BRIEFING (verbatim):\n{briefing}\n\n"
            f"CURRENT RESEARCH NOTES:\n{notes_md}\n\n"
            f"What is your next single action? "
            f"Either: (a) call exactly one research tool, "
            f"(b) call update_research_notes to revise Open/Next, "
            f"or (c) call report_findings to converge."
        )}
    ]

    response = llm.chat(messages)
    yield ThoughtCaptured(...)

    # Enforce: one tool call max
    if not response.tool_calls:
        # Treat as soft warning, force an update_research_notes prompt on next iter
        research_notes_writer.append_note(
            conv_folder,
            f"iter {step}: model produced no tool call — forcing convergence prompt"
        )
        continue

    primary = response.tool_calls[0]
    if len(response.tool_calls) > 1:
        research_notes_writer.append_note(
            conv_folder,
            f"iter {step}: dropped {len(response.tool_calls)-1} extra tool call(s) — fresh mode allows 1/iter"
        )

    # Execute primary
    if primary.name == "report_findings":
        # standard convergence path, exits the loop
        return _handle_report_findings(primary)

    if primary.name == "update_research_notes":
        research_notes_writer.update_semantic_sections(
            conv_folder,
            open_questions=primary.arguments["open_questions"],
            next_action=primary.arguments["next_action"],
        )
        continue

    # Any other tool — execute normally, then auto-append to Done + Citations
    result = execute_tool(primary)
    research_notes_writer.append_done(conv_folder, step, primary, result)
    if primary.name in CITING_TOOLS:
        research_notes_writer.append_citations(conv_folder, result)

# Budget exhausted → orchestrator forces a final report_findings synthesis turn
yield ForcedConvergence(...)
```

---

## The new tool — `update_research_notes`

Granted only to agents in `fresh_iteration` mode.

```python
ToolSpec(
    name="update_research_notes",
    description=(
        "Revise the semantic sections of research_notes.md. "
        "Use this to update Open questions (mark answered, add new sub-questions, "
        "abandon dead-ends) and to declare your Next intended action. "
        "Call this when your understanding of the research has changed — "
        "typically after 2-3 tool results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The current list of open sub-questions, prefixed "
                               "with their status: '[OPEN]', '[PARTIAL]', "
                               "'[ANSWERED]', '[ABANDONED]'.",
            },
            "next_action": {
                "type": "string",
                "description": "Single sentence describing your next intended tool call.",
            },
        },
        "required": ["open_questions", "next_action"],
    },
    handler=...,  # context-bound on conv_folder
)
```

Returns standard `tool_ok(summary, ...)`.

---

## Stop conditions (in priority order)

1. LLM calls `report_findings(...)` → clean exit, standard convergence flow.
2. All Open questions tagged `[ANSWERED]` or `[ABANDONED]` → orchestrator injects on next prompt:
   *"All sub-questions are closed. Call report_findings to deliver your synthesis now."*
   If the agent ignores, force a final synthesis prompt.
3. Step budget exhausted (proposed: 50 in fresh mode, vs 20 in accumulative).
4. Wall-clock for `_run_request` exhausted (existing `REQUEST_WALL_CLOCK_SECONDS=900`).

---

## DB changes

```sql
-- migrate_055_context_mode.sql
ALTER TABLE agents ADD COLUMN context_mode TEXT NOT NULL DEFAULT 'accumulative';

UPDATE agents SET context_mode = 'fresh_iteration'
WHERE code IN ('web-search-specialist', 'wikipedia-specialist', 'comparator-specialist');

-- Grant update_research_notes to the same three agents
INSERT INTO tools (code, ...) VALUES ('update_research_notes', ...) ON CONFLICT DO NOTHING;
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'update_research_notes' FROM agents
WHERE code IN ('web-search-specialist', 'wikipedia-specialist', 'comparator-specialist')
ON CONFLICT DO NOTHING;
```

---

## New code surface

| File | Purpose |
|---|---|
| `src/jeanmichel/research_notes_writer.py` | deterministic notes file manager (init, append_done, append_citations, update_semantic_sections) |
| `src/jeanmichel/tools/update_research_notes.py` | the new tool |
| `src/jeanmichel/orchestrator.py` | branch on `context_mode` in `_run_request` |
| `db/migrations/migrate_055_context_mode.sql` | schema + seed |
| `tests/test_research_notes_writer.py` | unit tests for the writer |
| `tests/test_fresh_iteration_orchestrator.py` | integration: MockClient scripts a 5-iteration fresh research flow |

Estimated diff: ~400 lines of code + ~200 lines of tests. No removal — all behind a DB flag.

---

## Backwards compatibility & risk

- Default `context_mode='accumulative'` → existing flows unchanged.
- 274 existing tests still pass without modification (they only touch accumulative agents in their fixtures).
- The 3 migrated agents see a behavioural shift — to be validated by replaying the "sources of truth" conversation from `2026-05-25_00-19` and comparing latency + output quality.
- Rollback: flip `context_mode` back to `accumulative` in DB. Tool grants for `update_research_notes` can stay (the LLM just won't call it).

---

## Open questions for review

1. **Citations format**: keep flat `[N] URL — title`, or include the snippet too? Flat is smaller, snippet helps the LLM judge relevance without re-fetching. Suggest: title only at first, escalate to snippet if the LLM keeps re-searching.

2. **Should `critical-thinker` read `research_notes.md`** when it's invoked after a research phase? Today it reads the workspace artifact. Reading `research_notes.md` would give it the structured Open/Done view for free.

3. **Step budget in fresh mode**: 50 feels right. With ~1-2 s per iter (constant context), that's ~60-90 s of pure LLM time per delegation. Well under the 900 s request wall-clock.

4. **What if the LLM calls `update_research_notes` every iteration**? It would burn steps without progress. Mitigation: detect 2 consecutive `update_research_notes` calls and inject a notice "you've updated notes twice in a row — your next action must be a research tool or report_findings".

5. **Should the orchestrator log dropped tool calls (when LLM produces 2+) into the `# Done` section** or into a separate `# Warnings` section? Suggest `# Warnings` to keep `# Done` clean.

---

## Implementation order

1. Migration 055 + DB seed (5 min).
2. `research_notes_writer.py` + its unit tests (deterministic, easy).
3. `update_research_notes` tool + grants + unit tests.
4. Orchestrator branch `fresh_iteration` in `_run_request` + integration tests with MockClient.
5. Smoke test: replay the "sources of truth" question, compare with `2026-05-25_00-19` baseline.
6. If green: extend to `wikipedia-specialist` and `comparator-specialist`. Update README.
7. If issues: iterate on the spec, don't extend.

---

## What this is NOT

- Not a replacement for the duplicate-call detection (Phase 6) — that still applies inside one iteration's tool call.
- Not a planner. There's no upstream LLM deciding the plan; the specialist itself owns its `Open questions`.
- Not multi-agent within a single research turn. It's a single specialist iterating, just with externalised memory.
- Not Ralph itself. We borrow the principle, our stack and use case are different.

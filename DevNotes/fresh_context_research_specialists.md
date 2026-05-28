# Progressive externalised state for research specialists

**Status**: draft spec, not yet implemented
**Inspiration**: Ralph (snarktank/ralph) — externalised memory + bounded LLM context per iteration
**Problem solved**: 120s LLM timeout when a research specialist accumulates 8+ tool calls
**Guiding principle**: KISS, measure before each step, reuse what already exists

---

## Problem statement

Today, when `web-search-specialist` (or `wikipedia-specialist`) does N iterations of `tool_call → tool_response → thought`, the conversation history grows linearly inside `messages`. By iteration 8 the prompt reaches ~25 KB and Ollama times out at 120 s.

The Phase 6 patch (bounded re-injection, identity-arg fingerprint) is a mitigation. We want a structural fix without building a parallel system.

---

## Key insight — we already have 80% of Ralph

Looking at any current conversation (e.g. `2026-05-25_01-50_fceaec9d54c6/`), the project **already externalises** what Ralph externalises:

| Ralph's `agent.md` | Jean-Michel's existing equivalent |
|---|---|
| Briefing | `plan.md` section `Task:` (per delegation) |
| Done log | `plan.md` section `Actions:` (timestamped, one line per tool call) |
| Findings / artefacts | `workspace/*.md` (files produced by specialists) |
| Summary | `plan.md` section `Summary:` |

What we **don't** have:
- The LLM doesn't **read** its own slice of `plan.md` during its iterations — it relies on accumulated `messages` instead.
- `Citations` (URL → claim) aren't explicitly extracted; they're buried in `workspace/*.md` text.

**The cure isn't to build a new notes file. It's to feed back what's already written.**

This reframes the whole approach: no `research_notes.md`, no `context_mode` enum, no new tool at step 1. Just close the loop.

---

## Design — progressive, three steps, each measurable

### Step 0 — Measure (no code change)

Before touching anything, instrument or analyse the 3 most recent long research conversations and record:
- bytes of `messages` payload at each LLM call
- bytes at the moment of timeout (if any)
- number of duplicate tool fingerprints caught by Phase 6
- total iterations per specialist

**Decision gate**: if the recent prompt-engineering patch (`source_admission_criteria` paradigm, migration 055) + Phase 6 are enough to keep specialists under 15 KB and below the timeout — **stop here**. Don't implement anything else. The cheapest win is the one already in production.

### Step 1 — Sliding-window context for specialists (atomic)

If Step 0 shows the wall is still hit, do this single atomic change. **One commit, one toggle, fully reversible.**

The change has two halves that must ship together (otherwise we just add bytes — see "Why redundancy was a real risk" below):

**1a. Trim `messages` to a sliding window for specialists.**

```python
# In the messages builder for specialist roles, inside _run_request:
SPECIALIST_WINDOW_TURNS = 3  # last 3 turns kept raw (tunable)

if agent.role == 'specialist' and len(history) > SPECIALIST_WINDOW_TURNS * 2:
    # Keep system + initial briefing + last N raw turns
    history = history[:2] + history[-SPECIALIST_WINDOW_TURNS * 2:]
```

**1b. Inject a deterministic "what you have done" block as a system-side prefix.**

```python
# Built deterministically by orchestrator from existing artefacts:
progress_block = render_specialist_progress(
    plan_path=conv_folder / 'plan.md',
    workspace_dir=conv_folder / 'workspace',
    delegation_id=current_delegation_id,
    max_bytes=2048,
)
# Then prepended to the system message (or as a second system turn)
```

`render_specialist_progress()` produces something like:

```
## Your progress so far in this delegation
Briefing: <verbatim Task: from plan.md, truncated to 400 chars>

Actions already executed (N=5):
- wikipedia_search("…") → 3 pages: List of file formats | …
- wikipedia_search("…") → 10 pages: API key | List of Java APIs | …
- workspace_create_file(wikipedia_sources.md) → wrote 1648 bytes

Workspace files you have produced:
- wikipedia_sources.md (1648 bytes) — first line: "## Reliable Information Sources…"
```

**The two halves are non-negotiable together**: window without injection = LLM forgets earlier turns; injection without window = duplicated bytes, no relief.

**Step 1 properties**:
- 0 new files, 0 new tools, 0 migrations, 0 DB columns
- 1 new function (`render_specialist_progress`)
- ~80-100 LOC + tests
- Rollback: set `SPECIALIST_WINDOW_TURNS = math.inf` → exact pre-patch behaviour
- Applies to all `role='specialist'` agents uniformly (no per-agent flag yet)

### Step 2 — Explicit `Open questions` slot (optional, only if Step 1 insufficient)

If after Step 1 we still observe specialists losing track of what they were trying to answer (e.g. they conclude early on a partial answer, or they thrash on the same sub-question), then add a lightweight semantic slot:

- New tool `note_progress(open_questions: list[str], next_action: str)`
- The orchestrator stores its output in **a new section of the existing `plan.md`** for the current delegation (NOT a new file)
- `render_specialist_progress` reads that section back into the prefix

Granted only to specialists. No `context_mode` enum even at this stage — every specialist gets it, the LLM uses it when relevant or ignores it.

**Properties**:
- 1 new tool (~50 LOC)
- Extend `plan_writer` to manage the new section (~30 LOC)
- 0 new files, 0 migrations beyond a tool grant insert
- Rollback: revoke the tool grant

This step is **optional and provisional**. Don't pre-build it.

### Step 3 — Citations extraction (optional, quality bet)

Independent of the timeout problem, an `Auto-cited URLs` block inside `render_specialist_progress` could further reduce hallucinations (it pairs with the `source_admission_criteria` paradigm from migration 055):

- Orchestrator scans `tool_response` JSON for keys `url`, `link`, `source` after each `web_search` / `wikipedia_*` call
- Builds a flat list `[N] URL — title` in memory keyed by delegation
- Includes it in the progress prefix

**Properties**:
- ~30 LOC, deterministic, no DB, no tool
- Activated independently of Steps 1-2

---

## Why redundancy was a real risk

The naive "just inject a summary into the system prompt" idea (an earlier draft of this spec) would have **made the timeout worse**: the LLM would receive

```
[system + injected summary]
[user briefing]
[full accumulated messages]
```

i.e. the summary on top **plus** the same information still in raw form below. Net effect: more bytes, faster blow-up. That's why Step 1 must combine injection with `messages` trimming. The injection replaces the elided history, it doesn't supplement it.

---

## What this is NOT

- Not a new mode (`context_mode` enum) — same code path for all specialists, just a tighter window.
- Not a new file (`research_notes.md`) — `plan.md` already plays the role, slice it per delegation.
- Not a new module (`research_notes_writer.py`) — extend `plan_writer.py` if and only if Step 2 ships.
- Not opt-in per agent — applies to all `role='specialist'` (with the window size as the single tunable).
- Not Ralph — borrows the externalisation principle, otherwise our stack and trade-offs differ.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Trimming drops the tool_call that established a hypothesis, LLM loses thread | Injection brings the summary back; if not enough, raise `SPECIALIST_WINDOW_TURNS` from 3 to 5 |
| `render_specialist_progress` itself grows unbounded over a 20-iter delegation | Hard cap `max_bytes=2048`; oldest actions get elided into "… (12 earlier actions omitted)" |
| Different specialists need different window sizes | If observed, promote `SPECIALIST_WINDOW_TURNS` to a per-role config (still simpler than `context_mode`) |
| Existing 274 tests break | Step 1 only touches the messages builder; tests that script ≤3 turns are unaffected. Tests with >3 turns may need a feature flag in the test fixture |

---

## Implementation order (only after Step 0 measurement)

1. Add `render_specialist_progress()` in a new helper (could live alongside `plan_writer.py`) — pure function over `plan.md` + workspace ls. Easy to unit test.
2. Modify the messages-builder branch in `orchestrator.py`: window trim + prefix injection, guarded by `role == 'specialist'`.
3. Unit tests on the renderer.
4. Integration test with MockClient: simulate 10-iteration specialist, assert messages payload stays < 8 KB.
5. Smoke test: replay the "sources of truth" question, compare with baseline (latency, output quality, byte size at peak).
6. If green: ship. If not: tune `SPECIALIST_WINDOW_TURNS`, retest. If still not: consider Step 2.

---

## Open questions for review

1. **Where to put `render_specialist_progress`** — sibling of `plan_writer.py` (good cohesion) or inside `prompts.py` (close to where it's consumed)? Lean toward `prompts.py` since it's prompt-shaping logic.

2. **Should the injected progress block be a separate `system` message or appended to the existing one?** Ollama tends to give more weight to the first system message; a second system turn keeps the original paradigms uncontaminated. Lean toward second turn.

3. **Initial window size** — 3 turns feels right (~6 messages: 3 tool_call/response pairs + a couple of thoughts). To be confirmed by Step 0 measurements.

4. **Does this change the behaviour of `report_findings`?** No — convergence path is untouched. Only the steady-state context construction changes.

5. **Apply to `comparator-specialist` and `code-runner` too?** They are specialists but typically converge in <3 turns. The window won't trim them in practice. Safe to apply uniformly to `role='specialist'`.

---

## Decision checkpoints

- After **Step 0**: do we even need to ship anything?
- After **Step 1** smoke test: is the wall gone? If yes, stop. If partially, tune window. If no, escalate to Step 2.
- After **Step 2** (if reached): is convergence quality improved? If no, revert and accept the limitation.

Each checkpoint is a measurable observation, not a feeling. The whole point of the staging is to avoid paying engineering cost upfront for a problem that may already be solved by cheaper means.

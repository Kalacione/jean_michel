# Jean-Michel — Prompt Skeleton (v2)

The v2 main loop renders this skeleton once per turn for the main agent
and once per delegation for each subagent. The renderer is
`jeanmichel.prompts.render_system_prompt_v2`.

All inter-agent text is in **English**. Only the user-facing reply is
rendered in the human's detected language (langdetect on the user_text,
fallback `en`).

## Conventions (v2)

- Ollama handles all Gemma 4 token rendering internally
  (`<|turn>system`, `<|think|>`, etc.). We never inject those tokens in
  prompt text.
- Thinking is enabled by passing `think: true` in the API call when
  `agents.thinking_mode = 1`. Trivial agents (Tier 0 dispatcher) pass
  `think: false`.
- Tools are declared via the `tools` array in the API call. Per role :
  - **router** : `ask_human`, `delegate_to`, plus the registry tools
    granted in `agent_tools`.
  - **specialist** : `delegate_to`, `report_back`, plus granted registry
    tools. **No `ask_human`** — if clarification is missing, conclude
    with `report_back(confidence='low', low_confidence_reason='...')`.
  - **finalizer** : no control verbs. Terminates by emitting an
    assistant turn without tool_calls.
- The conversation history is the **native Ollama `messages[]` array**.
  No reconstructed recap. The LLM sees its own prior tool_calls and
  outputs directly.

## Skeleton

```
# IDENTITY
You are {agent_name} ({agent_code}).
Role: {agent_role}.
Mission: {agent_mission}

# CONTEXT
## Human
{user_profile_text or "No user profile provided."}

## Known facts about the user (long-term memory)
- [user]     {code1} : {description1}
- [feedback] {code2} : {description2}
- [project]  {code3} : {description3}
...

Use `manage_user_memory(action='recall', code='<code>')` to load the
full body of an entry, `action='save'` to add a new fact,
`action='update'` to refine one.

Detected language — use for human-facing output: {user_language}
Working language for everything else (internal reasoning, tool queries,
briefings to other agents): English only.

## Conversation
- mode: {analyse | chat | vocal}

# DIRECTIVES
## {Category 1 title}
{paradigm content}

## {Category 2 title}
{paradigm content}
...

# OUTPUT CONTRACT
{role-specific termination rules}
```

## Composition order

1. **`# IDENTITY`** — name, code, role, mission of the agent (from `agents` row).
2. **`# CONTEXT`** :
   - `## Human` — `user_profile.toml` rendered + `user_memory` index
     prepended automatically by `render_user_memory_index` (limit 100,
     warning at 90).
   - Detected language line + working language line.
   - `## Conversation` — mode only (no other runtime state in the prompt).
3. **`# DIRECTIVES`** — paradigms grouped by category. Selection :
   `is_global=1 OR explicitly bound in agent_paradigms`, AND mode
   compatible (`paradigm_modes`). Rendered by `render_directives`.
4. **`# OUTPUT CONTRACT`** — role-specific termination rules. Three
   variants : router / specialist / finalizer. Set by
   `_render_output_contract_v2`.

## Per-role output contract

### Router (jean-michel)

```
- Reflect first in your thought channel.
- Delegate via delegate_to(agent_code, briefing, expected?, support_files?).
  Multiple parallel delegate_to calls in the same turn are processed sequentially.
- Ask the human via ask_human(question, why) only when a clarification blocks progress.
- Conclude by emitting an assistant turn WITHOUT any tool_calls.
  The `content` field of that turn IS the final answer to the user.
- Inter-agent briefings: English. Human-facing output: in the detected language.
```

### Specialist

```
- Reflect first in your thought channel ; surface assumptions and traps.
- You may use delegate_to(agent_code, briefing, expected?, support_files?) to
  descend the task tree if a sub-task exceeds your scope.
- You do NOT have ask_human. If a clarification is missing, conclude with
  report_back(confidence='low', low_confidence_reason='...').
- Conclude with report_back(summary, files_produced, confidence,
  low_confidence_reason?). This is the ONLY way to exit.
  low_confidence_reason is mandatory when confidence='low'.
- Inter-agent briefings: English. Workspace files: English unless requested.
```

### Finalizer

```
- Reflect first ; produce the deliverable.
- Conclude by emitting an assistant turn WITHOUT any tool_calls.
  The `content` field of that turn IS the final answer.
- You do NOT delegate, you do NOT ask the human.
```

## Multi-turn (chat mode)

Between human turns within the same conversation :

1. `messages.json` is reloaded from disk (full Ollama-shape array).
2. `messages[0]` (the system prompt) is re-rendered with a fresh
   `user_memory` index — entries saved during the previous turn become
   visible.
3. The new user input is appended as `{role: "user", content: ...}`.
4. The main loop resumes.

This means the system prompt is effectively re-rendered at the start of
every human turn — the `user_memory` block is always current. Within a
single turn, the system prompt stays stable.

## Tier 0 dispatcher prompt

The Tier 0 dispatcher uses a **separate, static** system prompt (no
identity / paradigms / memory). See `DISPATCH_SYSTEM_PROMPT` in
`prompts.py`. It is JSON-forced via Ollama's `format="json"` parameter,
which guarantees parseable output.

## Subagent briefing

When `delegate_to` spawns a subagent, the orchestrator builds the
subagent's `messages[]` from scratch :

```
[
  {role: "system",  content: <render_system_prompt_v2(sub_agent, ...)>},
  {role: "user",    content: <_format_subagent_briefing(briefing, support_files, expected)>}
]
```

The subagent never sees the caller's history. Files referenced via
`support_files` must physically exist (the caller is responsible for
writing them via workspace tools before delegating).

## What changed vs v1

| Concept                       | v1                                                | v2                                                  |
|-------------------------------|---------------------------------------------------|-----------------------------------------------------|
| Conversation history          | Reconstructed `running_user_text` per iteration   | Native Ollama `messages[]` array                    |
| Plan                          | `plan.md` written by orchestrator                 | Disappears as source of truth (regenerable view)    |
| Subagent termination          | `report_findings` or implicit                     | Explicit `report_back` with `confidence`+`low_confidence_reason`|
| Router termination            | `return_to_user` tool                             | Implicit (assistant without tool_calls)             |
| Subagent ask_human            | Available                                         | Removed (use `report_back(confidence='low')`)       |
| Context budget                | 7 orthogonal counters (steps, delegations, etc.)  | Partitioned `SYSTEM_RESERVE + WORKING + OUTPUT_RESERVE` |
| Compaction                    | Truncation hacks                                  | 4-level escalade (Snip / Microcompact / Collapse / Autocompact) |
| Cross-conv user memory        | `user_profile.toml` only (static)                 | `user_memory` table + index auto-injected           |

# Jean-Michel — Prompt Skeleton

The orchestrator (Python, not an agent) renders this skeleton for every agent
turn. Order, sections and tokens are fixed; only the values inside `{...}` and
the rendered paradigm bullets vary.

All inter-agent text is in **English**. Only the user-facing reply is rendered
in the human's detected language.

## Conventions

- Ollama handles all Gemma 4 token rendering internally (`<|turn>system`,
  `<|think|>`, `<|tool>declaration...`, etc.). We never inject those tokens
  manually in the prompt text — Ollama does it from the `role: system` message
  and the `tools` / `think` API parameters.
- Thinking is enabled by passing `think: true` in the API call when
  `agents.thinking_mode = 1`. Trivial agents pass `think: false`.
- Tools are declared by passing a JSON-schema `tools` array in the API call,
  **not** by injecting `<|tool>declaration...` tokens in the system text.
  Double-declaring would break tool calling.
- Briefings between agents flow through the `delegate_to` tool — never through
  free-form text.
- `ask_human` interrupts the flow; the orchestrator pauses the request,
  collects the answer, then resumes with the human answer injected into
  `running_user_text` for the next LLM turn.
- The `OUTPUT CONTRACT` block is **role-adapted**: routers and specialists
  receive the full set of control tools (`ask_human`, `delegate_to`,
  `return_to_user`); finalizers receive only `return_to_user`. The contract
  text rendered in the system prompt mirrors the tools the agent actually has.

## Skeleton

This is what `render_system_prompt()` produces and passes as `role: system`
to the Ollama API. Ollama wraps it in the appropriate Gemma 4 tokens before
sending to the model.

```
# IDENTITY
You are {agent.name} ({agent.code}).
Role: {agent.role}.
Mission: {agent.mission}.

# CONTEXT
## Human
{user_profile}
Detected language for user-facing reply: {detected_language}

## Conversation
- conversation_id: {conv.id}
- request_id: {req.id}
- parent_request_id: {req.parent_id_or_none}
- recursion_depth: {req.depth}/{MAX_RECURSION_DEPTH}
- mode: {conv.mode}
- turn_index: {req.turn_index}
- conversation_folder: {conv.folder_path}

## Machine
- os: {os}
- cwd: {cwd}
- utc_now: {utc_iso8601}

## Inbound briefing
from: {sender_agent_code_or_human}
expected: {expected_outcome}
support_files:
{relative_paths_list}
{inbound_text}

## Available specialists
{list of active agents with code and mission, excluding self and excluding archivist}

# DIRECTIVES
{paradigms rendered as `## {category.title}` blocks containing the `content`
 of each paradigm, in deterministic order:
 sections.order_priority -> categories.order_priority -> paradigms.order_priority}

# OUTPUT CONTRACT  (router / specialist variant)
- Reflect first in your thought channel; surface assumptions, traps, biases.
- If you must clarify with the user: call ask_human(question, why). One question only. `why` is mandatory.
- If task belongs to another specialist: call delegate_to(...). Multiple parallel delegate_to calls allowed in the same turn for independent subtasks.
- A delegate_to result is a structured object `{agent, artifact, answer}`. When forwarding to a finalizer, pass the `artifact` filename in support_files. Do NOT copy specialist `answer` content inline into the next briefing.
- If task is yours and complete: call return_to_user(answer).
- Inter-agent briefings: English. Human-facing output: see ## Human detected language.

# OUTPUT CONTRACT  (finalizer variant)
- Reflect first in your thought channel; surface assumptions, traps, biases.
- Produce the deliverable and return it via return_to_user(answer).
- You do not delegate, you do not call ask_human. Work with the inputs provided.
- Inter-agent text: English. Human-facing output: see ## Human detected language.
```

### API call shape (what the orchestrator sends to Ollama)

```python
client.chat(
    model   = "gemma4:latest",
    messages = [
        {"role": "system", "content": <rendered skeleton above>},
        {"role": "user",   "content": <inbound_text or tool results>},
    ],
    tools   = [...],   # JSON-schema array — Ollama renders as <|tool>declaration...
                       # filtered by role: finalizers get only return_to_user
    think   = True,    # Ollama injects <|think|> in the system block
    options = {"temperature": agent.temperature},
    stream  = False,
)
```

Ollama wraps the system content with `<|turn>system … <turn|>` and the user
content with `<|turn>user … <turn|>` internally. We never write those tokens.

## Multi-turn rules (Gemma 4 spec)

- **Standard turns**: strip the model's previous thoughts before re-prompting.
- **Within a single tool-call turn**: thoughts must NOT be stripped between
  the `tool_call` and the `tool_response`. The `ask_human` cycle falls under
  this rule — the orchestrator preserves the thinking block when it resumes.
- **Long agent chains** (depth ≥ 3): inject a `## Prior reasoning summary`
  block produced by the `synthesizer` instead of raw thoughts, to avoid
  cyclical reasoning. *(Planned — not yet implemented; the orchestrator
  currently relies on the per-turn briefing to carry context. This remains
  an active design goal as agent chains grow longer.)*

## Why this shape

- **`think` via API parameter**: Ollama's `think: true/false` (added in v0.9)
  injects `<|think|>` in the system block server-side. We pass it as an API
  parameter, not as raw text.
- **Tools via `tools` API parameter**: Ollama renders `<|tool>declaration...`
  tokens from the JSON-schema array we pass. Injecting them manually in the
  system text would duplicate declarations and break tool calling.
- **Tools filtered by role**: a finalizer only receives `return_to_user`. This
  is enforced both in the JSON tool list passed to Ollama AND in the OUTPUT
  CONTRACT text — keeping the two consistent prevents the model from being
  told it can call a tool it doesn't have.
- **`mode` in CONTEXT**: the agent sees the conversation mode (`analyse`,
  `chat`, `vocal`) but does not branch on it itself — mode-specific paradigms
  filtered by `paradigm_modes` do that work. The `mode` field is informational.
- **`turn_index` in CONTEXT**: lets agents know whether they are at the
  opening turn of a conversation or further along. Combined with the
  re-injected `summary.md` (chat/vocal modes), this is how continuity is
  carried.
- **`Available specialists` excludes archivist**: the archivist is invoked
  exclusively by the orchestrator after each user turn in chat/vocal modes;
  it is not a delegate target. Hiding it from the list prevents the router
  from attempting `delegate_to(archivist)` (also rejected at the orchestrator
  level as a defense-in-depth).
- `IDENTITY → CONTEXT → DIRECTIVES → OUTPUT CONTRACT` order: identity before
  context, context before rules, contract last. Anchors what the model "is"
  before what it "can do".
- Paradigms grouped by category, never inlined as a flat list: keeps the
  prompt readable and lets the model retrieve a directive by topic.
- `inbound_text` in the system block (not only in the user message): the
  mission is immutable for the lifetime of a request — the user message
  changes each tool-call iteration, the system prompt does not.

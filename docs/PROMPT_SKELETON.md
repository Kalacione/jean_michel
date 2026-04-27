# Jean-Michel — Prompt Skeleton

The orchestrator (Python, not an agent) renders this skeleton for every agent
turn. Order, sections and tokens are fixed; only the values inside `{...}` and
the rendered paradigm bullets vary.

All inter-agent text is in **English**. Only the user-facing reply is rendered
in the human's detected language.

## Conventions

- `<|think|>` is included in the system block when `agents.thinking_mode = 1`.
  Trivial agents (e.g. `clock`) omit it.
- Tools are declared inside the system block, after directives.
- Briefings between agents flow through the `delegate_to` tool — never through
  free-form text.
- `ask_human` interrupts the flow; the orchestrator pauses the request,
  collects the answer, then resumes by re-rendering the prompt with the
  `tool_response` injected (thoughts of the previous turn are preserved, per
  the Gemma 4 multi-turn exception for tool calls).

## Skeleton

```
<|turn>system
<|think|>
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
- recursion_depth: {req.depth}/5
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

# DIRECTIVES
{paradigms rendered as `## {category.title}` blocks containing the `content`
 of each paradigm, in deterministic order:
 sections.order_priority -> categories.order_priority -> paradigms.order_priority}

# TOOLS
<|tool>declaration:ask_human{question:str, why:str}<tool|>
<|tool>declaration:delegate_to{agent_code:str, briefing:str, support_files:list[str], expected:str}<tool|>
<|tool>declaration:return_to_user{answer:str}<tool|>
{...other tools granted to this agent...}

# OUTPUT CONTRACT
- Reflect first in your thought channel; surface assumptions, traps, biases.
- If you must clarify with the user: call ask_human(question, why). One question only. `why` is mandatory.
- If task belongs to another specialist: call delegate_to(...). Multiple parallel delegate_to calls allowed in the same turn for independent subtasks.
- If task is yours and complete: call return_to_user(answer).
- Inter-agent briefings: English. User-facing answer: {detected_language}.
<turn|>
<|turn>user
{inbound_briefing_text_or_raw_human_input}
<turn|>
<|turn>model
```

## Multi-turn rules (Gemma 4 spec)

- **Standard turns**: strip the model's previous thoughts before re-prompting.
- **Within a single tool-call turn**: thoughts must NOT be stripped between
  the `tool_call` and the `tool_response`. The `ask_human` cycle falls under
  this rule — the orchestrator preserves the thinking block when it resumes.
- **Long agent chains** (depth ≥ 3): inject a `## Prior reasoning summary`
  block produced by the `synthesizer` instead of raw thoughts, to avoid
  cyclical reasoning.

## Why this shape

- `<|think|>` in system: per Gemma 4 doc, thinking is enabled at conversation
  level via the system block.
- Tools declared in system: also per spec — declarations live alongside the
  thinking flag, consolidated into one system turn.
- `IDENTITY → CONTEXT → DIRECTIVES → TOOLS → OUTPUT CONTRACT` order: identity
  before context, context before rules, rules before tools, contract last.
  Anchors what the model "is" before what it "can do".
- Paradigms grouped by category, never inlined as a flat list: keeps the
  prompt readable and lets the model retrieve a directive by topic.
- Briefing as a tool, not free text: zero parsing, zero ambiguity, native
  Gemma 4 structured output.

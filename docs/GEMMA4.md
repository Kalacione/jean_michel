# Gemma 4 — Capabilities & Interaction Cheat-Sheet

> **NB (2026-06-15)** : gemma4 n'est PLUS le modèle par défaut du routeur (→ `cogito:32b`, cf.
> [20260614_model_selection.md](20260614_model_selection.md)). gemma4:26b reste utilisé pour les rôles
> `reasoner` / `compactor` / `subagent`, et gemma4 (multimodal) pour le tool `analyze_image`.

Source: <https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4> (last verified 2026-04-27).

## Control tokens

| Token / pair | Purpose |
|---|---|
| `<|turn>system … <turn|>` | System role (native in G4, no longer folded into user) |
| `<|turn>user … <turn|>` | User turn |
| `<|turn>model … <turn|>` | Model turn (model emits `<turn|>` to stop) |
| `<|think|>` | Activates thinking mode — placed inside the system block |
| `<|channel>thought … <channel|>` | Model's internal reasoning channel |
| `<|tool>declaration:name{schema}<tool|>` | Tool declaration (in system block) |
| `<|tool_call>call:name{args}<tool_call|>` | Model invokes a tool |
| `<|tool_response>response:name{...}<tool_response|>` | Tool result fed back; also acts as a stop sequence |
| `<|"|>` | Mandatory delimiter for **all string values** inside tool blocks |
| `<|image|>`, `<|audio|>` | Multimodal placeholders inside user turn |

## Family & limits

| Variant | Active params | Context | Notes |
|---|---|---|---|
| E2B | ~2B effective | 128K | edge / mobile, audio input supported |
| E4B | ~4B effective | 128K | edge / laptop, audio input supported |
| 26B-A4B (MoE) | 4B active / 26B total | 256K | fast inference, recommended workhorse |
| 31B (dense) | 31B | 256K | best quality, heaviest |

## Behaviors that matter for an agentic stack

| Behavior | Implication for Jean-Michel |
|---|---|
| Native `system` role | One consolidated system block per turn carries identity, context, directives, tools, and `<|think|>` |
| Native function calling | Briefings between agents go through `tool_call` — no custom JSON parser needed |
| Strip thoughts between standard turns | Orchestrator must remove the previous `thought` channel when re-prompting |
| Do **not** strip thoughts inside a tool-call sequence | `ask_human` resume preserves the prior thinking block |
| Long agent chains can loop | Inject a `## Prior reasoning summary` (built by `synthesizer`) instead of raw thoughts at depth ≥ 3 |
| 26B / 31B may emit `thought` even with thinking off | Stabilize by injecting an empty `<|channel>thought\n<channel|>` |
| LOW-thinking via system instruction | "Think briefly and efficiently" reduces ~20% thinking tokens; use for trivial agents |
| 140+ languages trained | `langdetect` on user input → set `detected_language` for the final reply only |

## Temperature strategy (per project doctrine)

| Phase | Temperature | Used by |
|---|---|---|
| Triage / routing | 0.1 | jean-michel (router) |
| Factual / summarization | 0.0–0.2 | summarizer, code agents |
| Brainstorm / divergent | 0.7–0.81 | future creative specialists |
| Validation pass | 0.2 (× N runs) | future critic agent |

## Ollama specifics (project context)

- Project version: **Ollama 0.21**.
- Native thinking implemented since Ollama 0.9. The `thought` channel is
  surfaced as `<thinking>…</thinking>` in the API response — capture it
  before persisting the artifact.
- Tool calls are exposed via the standard Ollama tool-call response shape;
  the orchestrator parses them, executes locally, and re-prompts with
  `tool_response`.

## Hard rules taken from the doc

1. Tool declarations live **only** in the system block.
2. Every string value inside a tool block must be wrapped in `<|"|>`.
3. `<|tool_response>` is a stop token — generation halts there until the
   application appends the response.
4. Thinking mode is conversation-level (set once in the consolidated system
   block), not per turn.

## Vision / multimodal (images)

Gemma 4 is multimodal on **every** variant (Text+Image ; E2B/E4B also audio).

| Aspect | Value |
|---|---|
| Image token budget | configurable 70/140/280/560/1120 → ~64/121/**256**/529 image tokens |
| Native resolution | encoder ~896×896 ; Ollama auto-resizes any larger input down |
| Placement | put the image **before** the text in the message |
| Multi-image | supported (several images per prompt) |

- **Ollama transport.** `/api/chat` takes images as a per-message base64 array
  (`message.images = ["<b64>", …]`) — there is no path-based API, so base64 is
  required **at call time**. `OllamaClient.chat_messages` forwards messages
  verbatim, so an `images` field reaches the model with no LLM-layer change.
  Accepted formats: JPEG/PNG/WebP (BMP/TIFF too) — **not SVG**.
- **Project doctrine (cf. `DevNotes/WEBUI/03`).** Images live in the workspace ;
  we never persist base64 in `messages.json`. We feed a **normalized ≤1024px
  WebP derivative** (lower bandwidth + format-safe). An attached image forces the
  **DEEP** verdict (the granite dispatcher is text-only). Two paths :
  `analyze_image(path, question)` (chat/vocal — transient isolated call → text)
  and ephemeral in-context base64 on the user turn (`analyse` mode only).

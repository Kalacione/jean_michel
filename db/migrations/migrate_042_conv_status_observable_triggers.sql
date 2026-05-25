-- MIGRATION 042 — metacog_live_monitor: observable conv_status triggers
-- ======================================================================
-- Rule 1 was circular: 'call conv_status if the conversation is complex'
-- but jean-michel cannot know complexity without calling conv_status first.
-- Replaced with a countable fact: 'you are about to emit your 3rd+ delegation
-- this turn' — visible directly in the LLM's own context window.
-- Rule 3 (same agent 3 times) merged into rule 1 (redundant once rule 1 is correct).

UPDATE paradigms
SET content = '- You have access to conv_status: a live dashboard of the current conversation.
  It returns: delegation depth, active agents, tool calls per agent, repeated calls, and budget signals.

- Call conv_status in these situations:
    1. You are about to emit your 3rd or later delegation in the current turn
       (you can count the delegate_to calls you have already made this turn).
    2. A specialist''s return seems incomplete and you are considering re-delegating to the same agent.

- How to act on the result:
    - budget_signals empty → proceed normally.
    - "WARNING: <agent> has N tool calls" → that agent has consumed its search budget.
      Do NOT delegate more work to it. Force synthesis: send a new briefing with
      "Write your findings now, even if incomplete. Do not search further."
    - "LOOP RISK: <agent> called <tool> Nx" → the agent is stuck in a loop.
      Cancel the pending work: delegate to synthesizer with the existing workspace files.
    - "WARNING: delegation depth reached N" → you are recursing too deep.
      Flatten: handle the next step yourself or delegate only to a finalizer.
    - "WARNING: N total tool calls" → the conversation is getting expensive.
      Assess which steps are still genuinely needed. Prune the plan if necessary.

- Budget signals are soft limits, not hard stops. You decide — but you must decide explicitly.
  Do not ignore a budget signal without stating in your thought why it is acceptable.',
    modified_at = datetime('now')
WHERE code = 'metacog_live_monitor';

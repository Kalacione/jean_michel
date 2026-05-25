-- MIGRATION 043 — conv_status: inject into prompt instead of tool call
-- =====================================================================
-- The orchestrator now computes budget_snapshot() in Python before each
-- turn and injects it into the ## Budget section of jean-michel's prompt.
-- No LLM tool call needed. The conv_status tool grant is revoked.

-- 1. Remove conv_status grant from jean-michel
DELETE FROM agent_tools
WHERE agent_id = (SELECT id FROM agents WHERE code = 'jean-michel')
  AND tool_code = 'conv_status';

-- 2. Rewrite metacog_live_monitor: remove 'call conv_status' instructions,
--    keep only 'how to act on budget signals'
UPDATE paradigms
SET content = '- Your system prompt contains a live ## Budget section, computed before each turn.
  It shows: total tool calls, delegation depth, tool calls per agent, and any budget signals.
  It is absent when there is nothing to report (first request, no activity yet).

- How to act on budget signals:
    - No ## Budget section, or SIGNAL lines absent → proceed normally.
    - "SIGNAL: WARNING: <agent> has N tool calls" → that agent has consumed its search budget.
      Do NOT delegate more work to it. Force synthesis: send a new briefing with
      "Write your findings now, even if incomplete. Do not search further."
    - "SIGNAL: LOOP RISK: <agent> called <tool> Nx" → the agent is stuck in a loop.
      Cancel the pending work: delegate to synthesizer with the existing workspace files.
    - "SIGNAL: WARNING: delegation depth reached N" → you are recursing too deep.
      Flatten: handle the next step yourself or delegate only to a finalizer.
    - "SIGNAL: WARNING: N total tool calls" → the conversation is getting expensive.
      Assess which steps are still genuinely needed. Prune the plan if necessary.

- Budget signals are soft limits, not hard stops. You decide — but you must decide explicitly.
  Do not ignore a budget signal without stating in your thought why it is acceptable.',
    modified_at = datetime('now')
WHERE code = 'metacog_live_monitor';

-- MIGRATION 038 — conv_status tool + metacog_live_monitor paradigm
-- ================================================================
-- Introduces real-time conversation monitoring for jean-michel.
-- conv_status queries the DB for the current conversation and returns:
--   delegation depth, tool calls per agent, repeated calls (loop detection),
--   and budget signals.
-- metacog_live_monitor teaches jean-michel when and how to act on those signals.

-- Grant conv_status to jean-michel (agent_id=1)
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
VALUES (1, 'conv_status');

-- Insert paradigm in metacognition category (id=25)
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global,
                       order_priority, active, created_at, modified_at)
VALUES (25, 'metacog_live_monitor', 'Live Metacognitive Monitor',
        '- You have access to conv_status: a live dashboard of the current conversation.
  It returns: delegation depth, active agents, tool calls per agent, repeated calls, and budget signals.

- Call conv_status in these situations:
    1. Before launching a new delegation, if the conversation has grown complex
       (many turns elapsed, multiple specialists already delegated to).
    2. When a specialist''s return seems incomplete — before re-delegating to the same agent.
    3. Any time you are about to delegate a third time in a row to the same agent.

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
        'Gives jean-michel live visibility into conversation activity to break loops and force synthesis.',
        0, 90, 1, datetime('now'), datetime('now'))
ON CONFLICT(code) DO UPDATE SET
    content   = excluded.content,
    title     = excluded.title,
    modified_at = datetime('now');

-- Assign paradigm to jean-michel
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT 1, id FROM paradigms WHERE code = 'metacog_live_monitor';

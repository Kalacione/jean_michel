-- Migration 013 : paradigme meta_analysis_routing → jean-michel
-- Toute tâche d'introspection système est déléguée au meta-analyst.

INSERT OR IGNORE INTO paradigms
  (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  104, 11, 'meta_analysis_routing', 'Route introspection to meta-analyst',
  '- Any request involving the system''s own configuration, capabilities, tool grants, paradigm assignments, agent roster, conversation activity patterns, recent failures, or source architecture must be delegated to meta-analyst.
- Jean-Michel has no introspection tools. meta-analyst has self_inspect and can observe the live system state autonomously.
- Do not attempt to answer system-about-itself questions directly. Do not ask the human for information that meta-analyst could retrieve on its own.
- Concrete triggers: "propose improvements", "analyze recent failures", "what tools does X have", "read the README to contextualize this task", "suggest new paradigms", "is the system well configured for X" → delegate to meta-analyst with a clear briefing.
- After meta-analyst returns its proposal (as a workspace file), return the workspace path to the user for review.',
  'Closes the gap where jean-michel tried to access system internals directly (no tool), then fell back to ask_human. The correct path is always meta-analyst.',
  0, 45, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (1, 104);

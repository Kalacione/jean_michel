-- Migration 010: workspace grants for critical-thinker + workspace_as_shared_memory paradigm
-- Specialists should write their findings to the workspace so other agents can read
-- them directly rather than re-delegating the same work.

-- Grant workspace tools to critical-thinker (id=8)
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (8, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (8, 'workspace_list');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (8, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (8, 'workspace_view');

-- Grant write access (enables actual file creation/edit, not just read)
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (8);

-- Paradigm 103: workspace_as_shared_memory (category: handoff=11)
-- Bound to all agents with workspace write access.
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
    103, 11,
    'workspace_as_shared_memory',
    'Workspace as shared memory',
    '- The conversation workspace is shared memory. Any agent can read files written by previous agents via workspace_view or conv_read_file.
- Write your key findings, analysis, or research output to a workspace file (e.g. analysis_<topic>.md, research_<topic>.md). This avoids other agents re-doing the same work.
- Before starting a task, check support_files for workspace artifacts already produced by other agents in this conversation.
- Keep workspace files concise and structured — they are reference material, not verbose reports.',
    'Turns the workspace into a shared knowledge base across agents in a conversation, reducing redundant work and recursive loops.',
    0, 75, 1, datetime('now'), datetime('now')
);

-- Bind to all agents with workspace write access
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (8, 103);   -- critical-thinker
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (9, 103);   -- document-builder
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (10, 103);  -- workspace-manager
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (11, 103);  -- meta-analyst
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (12, 103);  -- code-runner

-- Migration 011: workspace access for content-producing agents
-- synthesizer, wikipedia-specialist, comparator-specialist, summarizer
-- All get full read+write so they can persist their outputs to the shared workspace.

-- Agent IDs:
-- synthesizer          = 3
-- summarizer           = 2
-- wikipedia-specialist = 5
-- comparator-specialist= 6

-- Tool grants
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (3, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (3, 'workspace_list');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (3, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (3, 'workspace_view');

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (2, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (2, 'workspace_list');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (2, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (2, 'workspace_view');

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (5, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (5, 'workspace_list');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (5, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (5, 'workspace_view');

INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (6, 'workspace_create_file');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (6, 'workspace_list');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (6, 'workspace_str_replace');
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (6, 'workspace_view');

-- Write grants
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (3);
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (2);
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (5);
INSERT OR IGNORE INTO agent_workspace_grants (agent_id) VALUES (6);

-- Bind workspace_as_shared_memory paradigm (id=103) to new grantees
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (3, 103);
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (2, 103);
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (5, 103);
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (6, 103);

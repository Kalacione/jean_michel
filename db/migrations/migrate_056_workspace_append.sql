-- Migration 056: add workspace_append tool grants
--
-- workspace_append is a new tool that lets specialists add content to the end
-- of an existing workspace file. It fills an ergonomic gap that was forcing
-- the LLM through a fragile create_file (fails) → view → str_replace dance
-- whenever it wanted to add findings to its output file.
--
-- Grants are identical to workspace_create_file: every agent that can create
-- a workspace file should be able to append to it.

BEGIN;

INSERT INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'workspace_append'
FROM agents a
JOIN agent_tools at ON at.agent_id = a.id
WHERE at.tool_code = 'workspace_create_file'
ON CONFLICT DO NOTHING;

COMMIT;

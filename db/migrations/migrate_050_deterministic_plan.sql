-- Migration 050: plan.md becomes a deterministic orchestrator side-effect
-- plan_update tool removed; plan_writer.py drives plan.md from delegate_to events.
-- Removed paradigms: task_plan_file, orchestration_plan_maintenance
-- Simplified paradigm: router_synthesis_discipline

BEGIN;

-- Remove plan_update tool grants from all agents
DELETE FROM agent_tools WHERE tool_code = 'plan_update';

-- Remove plan_update from agent_paradigms if any reference it
DELETE FROM agent_paradigms
WHERE paradigm_id IN (
    SELECT id FROM paradigms
    WHERE code IN ('task_plan_file', 'orchestration_plan_maintenance')
);

-- Delete the obsolete paradigms
DELETE FROM paradigms WHERE code IN ('task_plan_file', 'orchestration_plan_maintenance');

-- Simplify router_synthesis_discipline (no more pipeline phases)
UPDATE paradigms
SET content = '- After a specialist returns via report_findings, decide: follow up with another delegation, or synthesize for the user.
- If the report includes sub_questions you want to follow up on, delegate to the appropriate agent.
- When all necessary research is done, synthesize the results and call return_to_user.
- Never re-delegate the same question without narrowing the scope.',
    modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'router_synthesis_discipline';

COMMIT;

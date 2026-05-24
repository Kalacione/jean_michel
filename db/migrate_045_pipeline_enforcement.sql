-- Migration 045 — Pipeline enforcement
-- Adds task_class / current_phase tracking to conversations.
-- Adds agent_workspace_grants for jean-michel (needed for plan_update write).
-- Updates research_phase_routing paradigm content.

-- New columns on conversations
ALTER TABLE conversations ADD COLUMN task_class    TEXT;   -- 'single_fact' | 'medium_task' | 'deep_research'
ALTER TABLE conversations ADD COLUMN current_phase TEXT;   -- NULL | 'planner_done' | 'gather_done' | 'critic_done' | 'build_done'

-- Give jean-michel workspace write access so plan_update(action='init'|'mark'|...) works
INSERT OR IGNORE INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'jean-michel';

-- Update research_phase_routing paradigm to describe the enforced pipeline
UPDATE paradigms
SET content = '- For deep_research tasks, the orchestrator enforces the pipeline GATHER → CRITIC → BUILD.
- Phase order (you cannot skip):
    1. PLAN: call plan_update(action="init", ...) to materialise the plan in workspace/plan.md.
    2. GATHER: delegate_to web-search-specialist and/or wikipedia-specialist. Each completes with gather_done.
    3. CRITIC: delegate_to critical-thinker with the gather artifacts in support_files. Completes with critic_done.
    4. BUILD: delegate_to document-builder with the gather + critic artifacts. Completes with build_done.
    5. RETURN: call return_to_user with a concise summary referencing the final workspace document.
- After each phase, plan_update(action="mark", step_id=..., status="done", findings=...) before moving to the next phase.
- If CRITIC identifies a gap, you may go back to GATHER once (the orchestrator allows gather_done → critic_done → gather_done → critic_done loop, but BUILD must be the eventual outcome).
- The current pipeline state is shown in your system prompt under # PIPELINE STATE.',
    modified_at = datetime('now')
WHERE code = 'research_phase_routing';

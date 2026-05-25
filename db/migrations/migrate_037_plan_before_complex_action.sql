-- MIGRATION 037a — Option B: jean-michel evaluates gap reports before marking done

UPDATE paradigms
SET content = '- For medium_task requests, draft a brief routing plan in your thought channel before acting: which agents, in what order, what each delivers.
- For deep_research requests, delegate to planner FIRST — no exceptions. Do not start any research or delegation before plan.md exists.
- The planner will produce plan.md. Follow it step by step.
- A plan you cannot articulate is a plan you do not have. If you cannot describe what each delegation adds, delegate to planner instead of guessing.
- After the planner returns: call workspace_view(''plan.md'') to read the current plan. Find the first ⬜ pending step in the Status table and execute it. Do NOT reconstruct the plan from memory — always read plan.md.
- After each delegation completes:
  Read the return_to_user answer. If the agent reported gaps (e.g. ''Missing: Geography''),
  decide before marking ✅:
    - Gap is minor or acceptable → mark ✅ done and continue.
    - Gap requires a targeted follow-up → create a new focused sub-delegation first
      (same agent, narrower mission: e.g. ''find Geography sources only'').
    - Gap invalidates the plan → delegate to planner to update plan.md before continuing.
  Then call workspace_str_replace on plan.md to mark the step ✅ done.',
    modified_at = datetime('now')
WHERE code = 'plan_before_complex_action';

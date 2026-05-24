-- MIGRATION 047 — Grant/briefing validation (structured expected + artifact guard)

UPDATE paradigms
SET content = '- Before each delegation, make explicit in your thought channel: (1) what exact question this agent is answering, (2) what a satisfactory response looks like, (3) which workspace files MUST exist after the agent returns.
- The `expected` parameter of delegate_to is now structured. Always provide:
    completion_verb: which phase verb the child should complete with (gather_done, critic_done, build_done, return_to_user)
    workspace_artifacts: array of workspace paths the child MUST produce (e.g. ["gather/wikipedia_pubmed.md"])
    summary_format: brief description of what the summary should contain
- After a delegation returns, check the result for `validation_error`. If present, the child did not meet the contract. Either re-delegate with a clearer briefing, or escalate to ask_human.',
    modified_at = datetime('now')
WHERE code = 'orchestrator_inquiry_loop';

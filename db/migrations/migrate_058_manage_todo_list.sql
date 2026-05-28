-- Migration 058: manage_todo_list — paradigme, bindings et grants
-- La catégorie process.planning existe déjà dans le schéma.
-- On insère le paradigme planning_with_todos, ses bindings agent_paradigms,
-- ses restrictions de mode (analyse + chat uniquement), et les grants agent_tools.

-- ── Paradigme ────────────────────────────────────────────────────────────────
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT
    (SELECT c.id FROM categories c JOIN sections s ON s.id = c.section_id
     WHERE s.code = 'process' AND c.code = 'planning'),
    'planning_with_todos',
    'Plan multi-step work with manage_todo_list',
    'For requests that decompose into 3 or more distinct sub-questions, or whenever comparative / cross-research / multi-source work is involved, START by calling `manage_todo_list` with operation="write" to lay out the full plan before any delegation. Update items via `update_status` as soon as a delegate_to or tool call returns a result. Before each new delegation, scan `pending` items: if several are independent (no `depends_on` overlap), emit multiple `delegate_to` calls in the same turn — the orchestrator processes them sequentially but you save one full re-decision cycle per item. Stop when all items are `completed` or `skipped`. Anti-pattern: do NOT create a todo list for trivial / single-step requests ("what time is it?").',
    'Externalises the plan for transparency, fault tolerance, and to enable batched delegation on independent sub-questions.',
    0, 10, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'planning_with_todos');

-- ── Bindings agent_paradigms ─────────────────────────────────────────────────
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE p.code = 'planning_with_todos'
  AND a.code IN (
      'jean-michel',
      'comparator-specialist',
      'critical-thinker',
      'meta-analyst',
      'document-builder',
      'code-runner'
  )
  AND NOT EXISTS (
      SELECT 1 FROM agent_paradigms ap
      WHERE ap.agent_id = a.id AND ap.paradigm_id = p.id
  );

-- ── Restrictions de mode : paradigme actif en analyse et chat uniquement ─────
INSERT INTO paradigm_modes (paradigm_id, mode)
SELECT p.id, m.mode
FROM paradigms p,
     (SELECT 'analyse' AS mode UNION ALL SELECT 'chat') AS m
WHERE p.code = 'planning_with_todos'
  AND NOT EXISTS (
      SELECT 1 FROM paradigm_modes pm
      WHERE pm.paradigm_id = p.id AND pm.mode = m.mode
  );

-- ── Grants outils ────────────────────────────────────────────────────────────
INSERT INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'manage_todo_list'
FROM agents a
WHERE a.code IN (
    'jean-michel',
    'comparator-specialist',
    'critical-thinker',
    'meta-analyst',
    'document-builder',
    'code-runner'
)
  AND NOT EXISTS (
      SELECT 1 FROM agent_tools at2
      WHERE at2.agent_id = a.id AND at2.tool_code = 'manage_todo_list'
  );

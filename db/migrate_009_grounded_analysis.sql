-- Migration 009: grounded_analysis + research_phase_routing
-- Forces research before analysis to prevent agents reasoning in a knowledge vacuum.

-- Paradigm 101: grounded_analysis (category: sources=5)
-- Bound to: critical-thinker (8), meta-analyst (11)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
    101, 5,
    'grounded_analysis',
    'Grounded analysis',
    '- Before analyzing factual claims, verify that source material is present in your briefing or support_files.
- If no external sources are provided and the task requires factual grounding (historical events, scientific data, technical specifics, current affairs), delegate to wikipedia-specialist (or another research agent) first to collect relevant content.
- Do not analyze from internal knowledge alone on factual topics — internal knowledge is approximate and may be outdated.
- Once sources are gathered, pass them as support_files when delegating further.',
    'Prevents hallucinated analysis by requiring real source material before engaging analytical reasoning.',
    0, 80, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (8, 101);
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (11, 101);

-- Paradigm 102: research_phase_routing (category: handoff=11)
-- Bound to: jean-michel (1)
INSERT OR IGNORE INTO paradigms (id, category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
    102, 11,
    'research_phase_routing',
    'Research phase routing',
    '- For analytical tasks on topics requiring external knowledge (science, history, current events, technical domains), do not delegate directly to an analytical agent on a bare question.
- First orchestrate a research phase: delegate to wikipedia-specialist (or relevant research agent) to gather source material.
- Then delegate to the analytical agent, passing the research artifacts as support_files.
- Simple factual lookups or direct questions do not require this two-phase approach.',
    'Ensures analytical agents receive grounded source material rather than reasoning in a knowledge vacuum.',
    0, 80, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (1, 102);

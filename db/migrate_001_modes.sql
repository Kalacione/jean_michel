-- =============================================================
-- Migration 001 — modes support
-- Apply once to an existing jeanmichel.db created before commit db25435.
-- Safe to run on a fresh install (INSERT OR IGNORE / ADD COLUMN guards).
-- =============================================================

PRAGMA foreign_keys = ON;

-- 1. New table: paradigm_modes
CREATE TABLE IF NOT EXISTS paradigm_modes (
  paradigm_id INTEGER NOT NULL REFERENCES paradigms(id) ON DELETE CASCADE,
  mode        TEXT    NOT NULL CHECK (mode IN ('analyse','chat','vocal')),
  PRIMARY KEY (paradigm_id, mode)
);

-- 2. New columns (SQLite does not support IF NOT EXISTS for ADD COLUMN before 3.37;
--    we rely on the script being idempotent via the INSERT OR IGNORE guards below,
--    but the ALTER TABLE will error if the column already exists.
--    Wrap in a SELECT to detect before altering is not possible in plain SQL,
--    so this script is meant to be run through the Python helper below which
--    handles the OperationalError gracefully.)
ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'analyse'
  CHECK (mode IN ('analyse','chat','vocal'));

ALTER TABLE requests ADD COLUMN turn_index INTEGER NOT NULL DEFAULT 0;

-- 3. archival category
INSERT OR IGNORE INTO categories (section_id, code, title, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT id FROM sections WHERE code='process'),
  'archival', 'Archival', 70, 1, datetime('now'), datetime('now')
);

-- 4. archivist agent
INSERT OR IGNORE INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at)
VALUES (
  'archivist', 'Archivist', 'finalizer',
  'Maintain a structured running summary of the conversation. Resolve contradictions, surface evolving threads, in a direct factual tone. Called exclusively by the orchestrator after each user turn in chat/vocal modes.',
  1, 0.1, 1, datetime('now'), datetime('now')
);

-- 5. archivist paradigms
INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT id FROM categories WHERE code='archival'),
  'archivist_format', 'Archivist summary format',
  '- Structure the summary under exactly four headings:
  ## Established facts
  ## Open threads
  ## Resolved contradictions
  ## User preferences observed
- Each heading must be present even if empty (write "(none)" in that case).
- Use bullet points under each heading. No prose, no transitions.',
  'Enforces a stable, parseable format for the running summary.', 0, 10, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT id FROM categories WHERE code='archival'),
  'archivist_tone', 'Archivist tone',
  '- Direct, factual, no narration, no transitions.
- No introductory or concluding sentences.
- Compressed bullet points — enough to reconstruct context, nothing more.
- Keep the full summary under 1500 words.',
  'Prevents verbose prose that would bloat the summary injected into future turns.', 0, 20, 1, datetime('now'), datetime('now')
);

-- 6. Mode-specific paradigms
INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='style'),
  'followup_proposals', 'Follow-up proposals',
  '- After delivering the answer, propose 2 to 3 specific angles the user might want to explore further.
- Format them as a short list, no preamble.
- If the answer is fully self-contained and no useful angle remains, do not force proposals.',
  NULL, 0, 30, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='style'),
  'concise_output', 'Concise output',
  '- Keep the user-facing answer under 4 short sentences.
- Headline first, details on demand.
- Offer to expand specific points: "Want me to detail X?".',
  NULL, 0, 40, 1, datetime('now'), datetime('now')
);

INSERT OR IGNORE INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  (SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='style'),
  'no_context_recap', 'No context recap',
  '- A running summary is provided. Do not paraphrase or repeat what the user already knows.
- Address the new turn directly.',
  NULL, 0, 50, 1, datetime('now'), datetime('now')
);

-- 7. Agent paradigm bindings
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'archivist' AND p.code IN ('archivist_format', 'archivist_tone');

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'jean-michel' AND p.code = 'followup_proposals';

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code IN ('jean-michel','summarizer','synthesizer',
                 'weather-specialist','wikipedia-specialist','comparator-specialist')
  AND p.code = 'concise_output';

INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'jean-michel' AND p.code = 'no_context_recap';

-- 8. paradigm_modes entries
INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'chat' FROM paradigms WHERE code = 'followup_proposals';

INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'vocal' FROM paradigms WHERE code = 'concise_output';

INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'chat' FROM paradigms WHERE code = 'no_context_recap';

INSERT OR IGNORE INTO paradigm_modes (paradigm_id, mode)
SELECT id, 'vocal' FROM paradigms WHERE code = 'no_context_recap';

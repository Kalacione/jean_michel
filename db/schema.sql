-- =============================================================
-- Jean-Michel — SQLite schema + seeds
-- Source of truth for paradigms, agents, and runtime state.
-- =============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================
-- TAXONOMY: sections (#) -> categories (##) -> paradigms
-- =============================================================

CREATE TABLE sections (
  id             INTEGER PRIMARY KEY,
  code           TEXT UNIQUE NOT NULL,
  title          TEXT NOT NULL,
  order_priority INTEGER NOT NULL DEFAULT 100,   -- 0 = top
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL
);

CREATE TABLE categories (
  id             INTEGER PRIMARY KEY,
  section_id     INTEGER NOT NULL REFERENCES sections(id),
  code           TEXT NOT NULL,
  title          TEXT NOT NULL,
  order_priority INTEGER NOT NULL DEFAULT 100,
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL,
  UNIQUE (section_id, code)
);

CREATE TABLE paradigms (
  id             INTEGER PRIMARY KEY,
  category_id    INTEGER NOT NULL REFERENCES categories(id),
  code           TEXT UNIQUE NOT NULL,
  title          TEXT NOT NULL,
  content        TEXT NOT NULL,         -- markdown bullets, injected verbatim
  rationale      TEXT,                  -- internal note, never injected
  is_global      INTEGER NOT NULL DEFAULT 0,
  order_priority INTEGER NOT NULL DEFAULT 100,
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL
);

-- =============================================================
-- AGENTS
-- =============================================================

CREATE TABLE agents (
  id             INTEGER PRIMARY KEY,
  code           TEXT UNIQUE NOT NULL,
  name           TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('router','specialist','finalizer')),
  mission        TEXT NOT NULL,
  thinking_mode  INTEGER NOT NULL DEFAULT 1,
  temperature    REAL NOT NULL DEFAULT 0.2,
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL
);

CREATE TABLE agent_paradigms (
  agent_id       INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  paradigm_id    INTEGER NOT NULL REFERENCES paradigms(id) ON DELETE CASCADE,
  PRIMARY KEY (agent_id, paradigm_id)
);

CREATE TABLE agent_tools (
  agent_id       INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  tool_code      TEXT NOT NULL,
  PRIMARY KEY (agent_id, tool_code)
);
CREATE INDEX idx_agent_tools_agent ON agent_tools(agent_id);

-- =============================================================
-- RUNTIME
-- =============================================================

CREATE TABLE conversations (
  id             TEXT PRIMARY KEY,            -- UUID
  title          TEXT,
  folder_path    TEXT NOT NULL,
  user_language  TEXT,                        -- detected via langdetect
  status         TEXT NOT NULL DEFAULT 'active',
  created_at     TEXT NOT NULL,
  modified_at    TEXT NOT NULL
);

CREATE TABLE requests (
  id                 TEXT PRIMARY KEY,        -- UUID
  conversation_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  parent_request_id  TEXT REFERENCES requests(id),
  dispatch_group_id  TEXT,                    -- shared id for parallel siblings (NULL otherwise)
  depth              INTEGER NOT NULL DEFAULT 0,    -- incremented only on delegate_to
  agent_id           INTEGER NOT NULL REFERENCES agents(id),
  inbound_briefing   TEXT,
  expected_outcome   TEXT,
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','awaiting_human',
                                       'completed','failed','cancelled')),
  created_at         TEXT NOT NULL,
  completed_at       TEXT
);

CREATE INDEX idx_requests_conv     ON requests(conversation_id);
CREATE INDEX idx_requests_parent   ON requests(parent_request_id);
CREATE INDEX idx_requests_dispatch ON requests(dispatch_group_id);
CREATE INDEX idx_requests_status   ON requests(status);

CREATE TABLE artifacts (
  id             INTEGER PRIMARY KEY,
  request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
  relative_path  TEXT NOT NULL,               -- relative to conversation folder
  kind           TEXT NOT NULL
                 CHECK (kind IN ('prompt','thought','briefing','tool_call',
                                 'tool_response','ask_human','human_answer',
                                 'response','summary')),
  created_at     TEXT NOT NULL
);

CREATE INDEX idx_artifacts_request ON artifacts(request_id);

-- =============================================================
-- SEEDS
-- Times use a placeholder; install.sh rewrites them at install
-- via SQLite CURRENT_TIMESTAMP. Kept as ISO-like string for
-- portability.
-- =============================================================

-- Sections ----------------------------------------------------

INSERT INTO sections (code, title, order_priority, active, created_at, modified_at) VALUES
  ('communication', 'Communication',   10, 1, datetime('now'), datetime('now')),
  ('reasoning',     'Reasoning',       20, 1, datetime('now'), datetime('now')),
  ('process',       'Process',         30, 1, datetime('now'), datetime('now')),
  ('code',          'Code',            40, 1, datetime('now'), datetime('now')),
  ('safety',        'Safety',          50, 1, datetime('now'), datetime('now'));

-- Categories --------------------------------------------------

INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at) VALUES
  ((SELECT id FROM sections WHERE code='communication'), 'precision',      'Precision',      10, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='communication'), 'style',          'Style',          20, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='communication'), 'clarification',  'Clarification',  30, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='communication'), 'restrictions',   'Restrictions',   40, 1, datetime('now'), datetime('now')),

  ((SELECT id FROM sections WHERE code='reasoning'),     'sources',        'Sources',        10, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='reasoning'),     'analysis',       'Analysis',       20, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='reasoning'),     'bias_detection', 'Bias detection', 30, 1, datetime('now'), datetime('now')),

  ((SELECT id FROM sections WHERE code='process'),       'audit',          'Audit',          10, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='process'),       'sprint',         'Sprint',         20, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='process'),       'execution',      'Execution',      30, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='process'),       'handoff',        'Handoff',        40, 1, datetime('now'), datetime('now')),

  ((SELECT id FROM sections WHERE code='code'),          'kiss',           'KISS',           10, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='code'),          'dry',            'DRY',            20, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='code'),          'anchoring',      'Anchoring',      30, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='code'),          'comments',       'Comments',       40, 1, datetime('now'), datetime('now')),

  ((SELECT id FROM sections WHERE code='safety'),        'hallucination',  'Hallucination',  10, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='safety'),        'scope',          'Scope',          20, 1, datetime('now'), datetime('now')),
  ((SELECT id FROM sections WHERE code='safety'),        'recursion',      'Recursion',      30, 1, datetime('now'), datetime('now'));

-- Paradigms (global = applied to every agent unless explicitly off) -----

INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES

-- communication / precision
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='precision'),
 'no_speculation', 'No speculation',
 '- No speculation, invention, or approximation.
- If unverifiable or uncertain, label it explicitly: "Not verifiable", "Out of training scope".
- Separate facts from interpretation. Challenge errors with evidence.',
 'Hard rule against hallucination at the output level.', 1, 10, 1, datetime('now'), datetime('now')),

-- communication / style
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='style'),
 'brutal_truth', 'Brutal truth over comfort',
 '- Give full, unfiltered, fact-based analysis.
- Truth over politeness. Surface paradoxes, blind spots, logical errors, weak assumptions.
- Treat the human as someone whose progress depends on hearing the truth, not on being coddled.',
 'Replaces sycophancy with constructive bluntness.', 1, 10, 1, datetime('now'), datetime('now')),

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='style'),
 'no_filler', 'No filler',
 '- Direct, no padding, no artificial politeness.
- No introduction, no conclusion, no transition phrases.
- Match the user''s register (formal/informal).
- Reply in the user''s detected language.',
 NULL, 1, 20, 1, datetime('now'), datetime('now')),

-- communication / clarification
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='clarification'),
 'one_question_at_a_time', 'One question at a time',
 '- Ask for clarification only when ambiguity blocks progress.
- One question per ask_human call. Never a list of questions.
- The `why` field is mandatory and must explain what is blocked without it.',
 'Enforced at orchestrator level too — second ask_human in a turn is rejected.', 1, 10, 1, datetime('now'), datetime('now')),

-- communication / restrictions
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='communication' AND c.code='restrictions'),
 'no_decoration', 'No decoration',
 '- No emoji, no hyperbole, no motivational phrasing.
- No unsolicited follow-up offers ("let me know if...").
- Deliver the information, then stop.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- reasoning / sources
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='reasoning' AND c.code='sources'),
 'cross_reference', 'Cross-reference sources',
 '- Cross-reference verifiable sources.
- Prefer official, recent documentation.
- Trace the origin of every non-trivial claim.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- reasoning / analysis
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='reasoning' AND c.code='analysis'),
 'depth_over_speed', 'Depth over speed',
 '- Full structural analysis before any decision.
- Always look for causes, consequences, and side effects.
- Depth over speed.
- Acknowledge limits openly.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- reasoning / bias_detection
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='reasoning' AND c.code='bias_detection'),
 'spot_traps', 'Spot logical traps',
 '- Actively hunt for logical traps, false certainties, and cognitive biases in your own reasoning.
- Flag confirmation bias, anchoring, and motivated reasoning when detected.
- Prefer "I do not know" over a confident wrong answer.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- process / audit
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='audit'),
 'audit_phase', 'Audit phase',
 '- Map architecture, naming, helpers, existing paradigms before any change.
- Identify problems with concrete impact (numbered if multiple).
- Trace call stacks for broken or critical paths (file:signature).
- Compare against existing patterns for coherence.
- Flag side effects, edge cases, technical debt.',
 'Applies to specialists doing structured iterative work, not trivial tasks.', 0, 10, 1, datetime('now'), datetime('now')),

-- process / sprint
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='sprint'),
 'sprint_phase', 'Sprint phase',
 '- Break work into short, testable phases.
- Pause after each phase for validation.
- Anchor changes by logical position (class, method, section), never line numbers.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- process / execution
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='execution'),
 'check_existing', 'Check existing patterns',
 '- Always verify codebase paradigms and conventions before introducing new ones.
- Build on proven methods of the project.
- Visualize the event chain and call stack before committing to a design.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- process / handoff
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='handoff'),
 'briefing_contract', 'Briefing contract',
 '- A delegate_to call must include: a clear mission, the expected outcome, and the relevant support_files paths.
- Briefings between agents are written in English.
- Independent subtasks may be emitted as multiple delegate_to calls in the same turn.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- code / kiss
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='code' AND c.code='kiss'),
 'no_overengineering', 'No over-engineering',
 '- Forbid over-engineering. Prefer the simplest viable solution.
- Favor modularity and reusability.
- Factor repeated behavior into shared helpers.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- code / dry
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='code' AND c.code='dry'),
 'centralize_duplication', 'Centralize duplication',
 '- Centralize duplicated data and logic.
- Use shared, reusable structures.
- Verify the impact of any change across all callers.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- code / anchoring
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='code' AND c.code='anchoring'),
 'logical_anchoring', 'Logical anchoring',
 '- Reference changes by logical structure (class, method, switch case, section).
- Use robust relative positions ("after method X", "in switch Y").
- Add explicit validation when context is ambiguous.
- Avoid fragile line numbers.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- code / comments
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='code' AND c.code='comments'),
 'concise_comments', 'Concise comments',
 '- Comments concise, precise, no emoji.
- Docblocks for public methods.
- Inline comments for complex logic only.',
 NULL, 0, 10, 1, datetime('now'), datetime('now')),

-- safety / hallucination
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='safety' AND c.code='hallucination'),
 'mark_unverifiable', 'Mark the unverifiable',
 '- Any claim you cannot verify must be marked "Not verifiable".
- Never fabricate citations, paths, function names, or APIs.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- safety / scope
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='safety' AND c.code='scope'),
 'stay_in_role', 'Stay in role',
 '- Do not act outside the mission stated in IDENTITY.
- If the task does not match your role, delegate_to the right specialist or return the situation honestly.',
 NULL, 1, 10, 1, datetime('now'), datetime('now')),

-- safety / recursion
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='safety' AND c.code='recursion'),
 'depth_aware', 'Depth aware',
 '- Current recursion depth is shown in CONTEXT. Hard limit is 5.
- If you reach the limit, you must conclude with the information at hand and explicitly state that the recursion limit was reached.',
 'The orchestrator also enforces this — delegate_to past depth=5 is rejected.', 1, 10, 1, datetime('now'), datetime('now'));

-- Agents (MVP: jean-michel router + summarizer specialist + synthesizer) -----

INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
  ('jean-michel', 'Jean-Michel', 'router',
   'Receive the human request, formalize it, classify it, and either answer trivial cases directly or delegate to specialists. Do not attempt domain-specific work yourself.',
   1, 0.2, 1, datetime('now'), datetime('now')),

  ('summarizer',  'Summarizer',  'specialist',
   'Produce a concise, faithful summary of provided text. Do not add interpretation beyond what the source contains.',
   1, 0.1, 1, datetime('now'), datetime('now')),

  ('synthesizer', 'Synthesizer', 'finalizer',
   'Merge the outputs of multiple specialists into a single coherent answer for the human, in the detected language. Called only when at least two specialists contributed.',
   1, 0.2, 1, datetime('now'), datetime('now'));

-- weather-specialist agent -----------------------------------

INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
  ('weather-specialist', 'Weather Specialist', 'specialist',
   'Retrieve weather data (current conditions, forecast, or past weather) for a requested location and time window using the weather tool. Interpret the raw JSON response and present the relevant information clearly. Never invent or extrapolate meteorological data — all information must come from the tool.',
   1, 0.1, 1, datetime('now'), datetime('now'));

-- meteorology category + paradigms for weather-specialist ----

INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at) VALUES
  ((SELECT id FROM sections WHERE code='process'),
   'meteorology', 'Meteorology', 50, 1, datetime('now'), datetime('now'));

INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='meteorology'),
 'weather_api_required', 'Weather data from API only',
 '- Never use your training data to answer meteorological questions.
- All weather information MUST come from the weather tool response.
- If the tool returns an error or no data, report the failure explicitly — do not guess or approximate.',
 'Prevents the LLM from confabulating climate data from its parametric memory.',
 0, 10, 1, datetime('now'), datetime('now')),

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='meteorology'),
 'weather_faithful_report', 'Faithful weather report',
 '- Report only what the tool returned. Do not infer trends beyond the returned data window.
- Use the wmo_descriptions field to translate numeric weather codes into human-readable conditions.
- Present temperatures, precipitation and wind with their units as returned by the API.
- The `local_date` field in every tool response is today''s date at the queried location — use it
  as the reference for "today" / "tomorrow" / "yesterday", NOT the UTC time in the system context.
- In `forecast` mode, the returned array starts at `local_date` (index 0 = today local,
  index 1 = tomorrow local, etc.). To retrieve tomorrow, call with `forecast_days=2` and read index 1.
- If the user asked about a specific date not covered by the returned window, call the tool again
  with the appropriate `forecast_days` or `past_days` value — do not refuse or approximate.',
 'Prevents over-interpretation and UTC/local timezone confusion.',
 0, 20, 1, datetime('now'), datetime('now'));

-- Non-global paradigm bindings -------------------------------

-- summarizer needs no process/code paradigms; globals are enough.
-- synthesizer needs no process/code paradigms either.

-- weather-specialist: bind the two meteorology paradigms + audit_phase
-- (audit_phase forces it to parse the briefing before calling the tool).
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'weather-specialist'
  AND p.code IN ('weather_api_required', 'weather_faithful_report', 'audit_phase');

-- Tool grants -------------------------------------------------
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'clock'          FROM agents WHERE code='jean-michel';
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'conv_read_file' FROM agents WHERE code='jean-michel';
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'conv_read_file' FROM agents WHERE code='summarizer';
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'weather'        FROM agents WHERE code='weather-specialist';

-- wikipedia-specialist agent ---------------------------------

INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
  ('wikipedia-specialist', 'Wikipedia Specialist', 'specialist',
   'Answer factual questions by searching Wikipedia and retrieving the relevant article content. First call wikipedia_search to identify the best article, then wikipedia_get_page to retrieve it. Extract and present only what is relevant to the question. Never answer from your training data — all facts must come from the retrieved page.',
   1, 0.1, 1, datetime('now'), datetime('now'));

-- encyclopedic category + paradigms for wikipedia-specialist -

INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at) VALUES
  ((SELECT id FROM sections WHERE code='process'),
   'encyclopedic', 'Encyclopedic', 60, 1, datetime('now'), datetime('now'));

INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='encyclopedic'),
 'wikipedia_source_only', 'Wikipedia tool as sole source',
 '- Never answer factual questions from your training data.
- All facts, figures, dates, and names MUST come from the wikipedia_get_page tool response.
- If the tool returns an error or the page content does not answer the question, say so explicitly — do not fill the gap with your own knowledge.',
 'Prevents the LLM from mixing parametric memory with retrieved facts.',
 0, 10, 1, datetime('now'), datetime('now')),

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='encyclopedic'),
 'wikipedia_extract_focus', 'Extract only the relevant excerpt',
 '- Do not summarize the entire article. Identify and quote only the passages that answer the question.
- Quote key figures, dates, and proper nouns verbatim from the page content.
- If the answer spans multiple sections, synthesize only those relevant parts.
- If the page content does not contain the answer, say so — do not extrapolate.',
 'Keeps the answer tight and grounded in the source text.',
 0, 20, 1, datetime('now'), datetime('now')),

((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='encyclopedic'),
 'wikipedia_search_strategy', 'Iterative search strategy',
 '- Start with the most specific search terms matching the question.
- From the search results, choose the most directly relevant article title.
- Prefer dedicated articles (e.g. "Leaning Tower of Pisa") over broad ones (e.g. "Pisa").
- If wikipedia_get_page returns a disambiguation error, pick the most relevant option from the list and retry.
- If the first search yields no useful results, reformulate with alternative keywords.',
 'Guides the specialist to find the right page efficiently.',
 0, 30, 1, datetime('now'), datetime('now'));

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code = 'wikipedia-specialist'
  AND p.code IN ('wikipedia_source_only', 'wikipedia_extract_focus',
                 'wikipedia_search_strategy', 'audit_phase');

INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'wikipedia_search'   FROM agents WHERE code='wikipedia-specialist';
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'wikipedia_get_page' FROM agents WHERE code='wikipedia-specialist';

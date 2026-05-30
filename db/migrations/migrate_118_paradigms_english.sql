-- =============================================================================
-- migrate_118_paradigms_english.sql
-- =============================================================================
-- Uniformise prompt-facing paradigm content to English (internal/inter-LLM
-- language = English ; user-facing I/O stays in the user's language). Strips
-- the French trigger examples that had crept into a few paradigms' content.
-- Surgical REPLACE() per fragment → idempotent (no French left = no-op).
-- =============================================================================

UPDATE paradigms SET content = REPLACE(content, '(caleçon)', '(original term)') WHERE code = 'briefing_contract';
UPDATE paradigms SET content = REPLACE(content, 'French "morse" → "walrus", "dauphin" → "dolphin",', 'a non-English entity name to its') WHERE code = 'wikipedia_search_strategy';
UPDATE paradigms SET content = REPLACE(content, '"rhinocéros" → "rhinoceros", "caleçon" → "boxer shorts", "slip" → "briefs"', 'English equivalent') WHERE code = 'wikipedia_search_strategy';
UPDATE paradigms SET content = REPLACE(content, '("actualités", "news", "dernières nouvelles", "what is', '("news", "latest updates", "what is') WHERE code = 'news_first_for_news_briefs';
UPDATE paradigms SET content = REPLACE(content, '  happening with X", "que se passe-t-il", "what was reported about"),', '  happening with X", "what was reported about"),') WHERE code = 'news_first_for_news_briefs';
UPDATE paradigms SET content = REPLACE(content, '("écris un script", "implement X", "fix this bug", "fais', '("write a script", "implement X", "fix this bug",') WHERE code = 'code_runner_for_code_production_briefs';
UPDATE paradigms SET content = REPLACE(content, '  marcher ce code", "make Y work", "run this", "test this approach"),', '  "make this code work", "make Y work", "run this", "test this approach"),') WHERE code = 'code_runner_for_code_production_briefs';
UPDATE paradigms SET content = REPLACE(content, 'For research-only briefs ("trouve-moi des libs", "comment marche X")', 'For research-only briefs ("find me libraries", "how does X work")') WHERE code = 'code_runner_for_code_production_briefs';
UPDATE paradigms SET content = REPLACE(content, 'For mixed briefs ("trouve-moi une lib ET écris un script qui s''en sert"),', 'For mixed briefs ("find a library AND write a script that uses it"),') WHERE code = 'code_runner_for_code_production_briefs';
UPDATE paradigms SET content = REPLACE(content, '(e.g. "montre-moi une image de X", "des images de", "trouve une photo de")', '(e.g. "show me an image of X", "images of", "find a photo of")') WHERE code = 'show_images_inline';

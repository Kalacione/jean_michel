-- =============================================================================
-- migrate_119_image_results_cap.sql
-- =============================================================================
-- image_search now returns up to 6 results (the chat image grid fits 6). Bump the
-- show_images_inline paradigm hint from "(up to ~5)" to "(up to 6)" to match.
-- Idempotent (REPLACE of an absent fragment is a no-op).
-- =============================================================================

UPDATE paradigms SET content = REPLACE(content, '(up to ~5)', '(up to 6)')
WHERE code = 'show_images_inline';

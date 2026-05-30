-- =============================================================================
-- migrate_117_image_display_routing.sql
-- =============================================================================
-- Routing paradigm bound to the router (jean-michel) : when the user wants to
-- SEE images, use image_search AND present the results inline as Markdown
-- images, never a bare list of links. The tool description alone proved
-- insufficient in practice. Additive + idempotent (guarded by paradigm code).
-- =============================================================================

INSERT INTO paradigms
    (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
SELECT
    (SELECT c.id FROM categories c JOIN sections s ON s.id = c.section_id
        WHERE s.code = 'process' AND c.code = 'planning'),
    'show_images_inline',
    'Show found images inline',
    'When the user asks to SEE, SHOW or FIND images (e.g. "montre-moi une image de X", "des images de", "trouve une photo de"), call image_search with the subject as the query, then PRESENT each relevant result INLINE as a Markdown image so it renders in the chat: ![title](image_url) using the image_url field (the direct image). Never reply with just a list of links to source pages: the user wants to SEE the pictures (up to ~5). To analyse or keep an image afterwards, bring it into the workspace with image_fetch.',
    'Migration 117 : le router listait des liens au lieu des images quand on demande à les voir.',
    0, 36, 1, datetime('now'), datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM paradigms WHERE code = 'show_images_inline');

INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT (SELECT id FROM agents WHERE code = 'jean-michel'),
       (SELECT id FROM paradigms WHERE code = 'show_images_inline')
WHERE NOT EXISTS (
    SELECT 1 FROM agent_paradigms
    WHERE agent_id = (SELECT id FROM agents WHERE code = 'jean-michel')
      AND paradigm_id = (SELECT id FROM paradigms WHERE code = 'show_images_inline')
);

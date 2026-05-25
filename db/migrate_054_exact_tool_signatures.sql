-- migrate_054_exact_tool_signatures.sql
--
-- Ancrer les signatures EXACTES des outils dans les paradigmes qui les mentionnent.
-- Problème observé : quand un paradigme dit "use workspace_str_replace to refine",
-- le LLM invente des noms de paramètres plausibles (old_content/new_content,
-- old_string/new_string) au lieu des noms réels (old_str/new_str). Le schema
-- JSON envoyé via tools=[...] ne suffit visiblement pas — il faut que la
-- signature apparaisse aussi en clair dans le contexte sémantique du paradigme.

BEGIN TRANSACTION;

-- Paradigme document_workspace_output : citer la signature
UPDATE paradigms
SET content = replace(
        content,
        'Use workspace_str_replace to refine a document iteratively rather than recreating it from scratch.',
        'Use workspace_str_replace(relative_path, old_str, new_str) to refine a document iteratively rather than recreating it from scratch. The parameter names are EXACTLY old_str and new_str — not old_content/new_content/old_string/new_string.'
    ),
    modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'document_workspace_output';

-- Paradigme planner_plan_format : ancrer les signatures dans le protocole
UPDATE paradigms
SET content = replace(
        content,
        'Call workspace_str_replace to update only what changed — never recreate from scratch.',
        'Call workspace_str_replace(relative_path, old_str, new_str) to update only what changed — never recreate from scratch. Parameter names are EXACT: old_str (string to find, must appear exactly once) and new_str (replacement). Do not invent variants like old_content/new_content.'
    ),
    modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'planner_plan_format';

UPDATE paradigms
SET content = replace(
        content,
        'Use workspace_str_replace to update only the affected sections',
        'Use workspace_str_replace(relative_path, old_str, new_str) to update only the affected sections'
    ),
    modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE code = 'planner_plan_format';

COMMIT;

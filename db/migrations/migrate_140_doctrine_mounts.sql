-- =============================================================================
-- migrate_140_doctrine_mounts.sql
-- =============================================================================
-- Étage C / Partie 2 : la sandbox projet (repo_exec) monte DÉSORMAIS le repo à
-- /app (cwd) ET le scratch de la conversation à /workspace — pour qu'un script
-- d'action écrit dans le workspace puisse tourner SUR le repo sans le polluer.
-- On précise ces 2 points de montage dans la doctrine `code_space_doctrine`.
--
-- REPLACE (pas un SET du contenu entier) → applique exactement le même swap que
-- le miroir db/schema.sql, et idempotent (no-op si l'ancienne phrase est absente).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

UPDATE paradigms
SET content = REPLACE(
        content,
        'it runs inside a per-project container that mounts the repo, offline and confined (no network, no host access)',
        'it runs inside a per-project container that mounts the repo at /app (your working directory) AND your scratch workspace at /workspace, offline and confined (no network, no host access) — write action scripts and their outputs under /workspace and operate on the repo at /app'
    ),
    modified_at = datetime('now')
WHERE code = 'code_space_doctrine';

COMMIT;

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- SELECT content LIKE '%mounts the repo at /app%' FROM paradigms WHERE code='code_space_doctrine'; -- 1

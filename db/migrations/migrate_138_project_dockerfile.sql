-- =============================================================================
-- migrate_138_project_dockerfile.sql
-- =============================================================================
-- Étage C/C3 — manage the project sandbox image from the PROJECT SETTINGS instead
-- of requiring a .jm/Dockerfile committed in the target repo. One TEXT column
-- holds the project's Dockerfile (FROM = base image, RUN = setup); empty ⇒ the
-- default bash+git image. repo_exec builds it (tagged by content hash) and runs
-- the sandbox from it.
--
-- NOTE: ADD COLUMN is one-shot in SQLite (not re-appliable) — like migrate_133.
-- =============================================================================

PRAGMA foreign_keys = ON;

ALTER TABLE projects ADD COLUMN dockerfile TEXT NOT NULL DEFAULT '';

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- PRAGMA table_info(projects);  -- doit lister dockerfile

-- =============================================================================
-- migrate_133_project_repo.sql
-- =============================================================================
-- "Brancher un repo" (cf. docs/20260612_improve_thinking/branchable_repo_design.md) :
-- attache un dépôt de code (chemin LOCAL ou URL SSH) à un PROJET. Les conversations
-- en mode `code` rattachées au projet en héritent ; `code_repo` vide ⇒ fallback
-- `config.PROJECT_ROOT` (rétro-compatible).
--
-- NOTE: ADD COLUMN est one-shot en SQLite (pas ré-appliable) — comme migrate_102.
-- =============================================================================

PRAGMA foreign_keys = ON;

ALTER TABLE projects ADD COLUMN code_repo TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN repo_kind TEXT NOT NULL DEFAULT 'local'
  CHECK (repo_kind IN ('local', 'ssh'));

-- =============================================================================
-- VALIDATION
-- =============================================================================
-- PRAGMA table_info(projects);  -- doit lister code_repo + repo_kind

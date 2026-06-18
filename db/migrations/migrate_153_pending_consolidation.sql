-- =============================================================================
-- migrate_153_pending_consolidation.sql
-- =============================================================================
-- Refonte mémoire (Étape 1). La file des CANDIDATS de consolidation (mémoire +
-- paradigmes) passe des sidecars fichier (pending_memory.json) à la DB. Source
-- unique pour les 2 producteurs (outil `propose_memory` + beat de réflexion
-- fin-de-tour) ; lue par la revue humaine (CLI `/memo` + web).
--
--   kind      : 'fact' (→ memory) | 'rule' (→ promotion paradigme)
--   dedup_key : scope/code/target (fact) | section/cat/title-slug (rule) — l'upsert
--               sur (conversation_id, dedup_key) garde UN candidat courant (re-proposé
--               = rafraîchi, pas dupliqué).
--   payload   : le dict candidat (JSON) verbatim.
--   status    : pending | applied | dismissed (la revue lit pending).
--
-- Le watermark `consolidation_state.json` n'est PAS porté : il servait au daemon
-- 15-min (supprimé) ; la réflexion se déclenche désormais en fin de tour.
-- Additif + idempotent (IF NOT EXISTS).
-- =============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pending_consolidation (
  id              INTEGER PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('fact', 'rule')),
  dedup_key       TEXT NOT NULL,
  payload         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'dismissed')),
  created_at      TEXT NOT NULL,
  UNIQUE (conversation_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_pending_conv ON pending_consolidation(conversation_id, status);

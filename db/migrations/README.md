# Migrations

`../schema.sql` is the **baseline** — the full, current schema, loaded verbatim by
`./jm.sh --install` on a fresh database. A fresh install sits at `PRAGMA user_version = 0`.

This folder holds **forward migrations** that evolve an existing database (yours, or a
friend's installed copy) to match a newer `schema.sql` after a pull. The baseline itself
is never a migration — it's the starting point migrations build on.

## Adding a migration

1. Write `migrate_NNN_short_description.sql` here, where `NNN` is the next free number
   starting at `001` (`001`, `002`, …). Use `IF EXISTS` / `IF NOT EXISTS` /
   `INSERT OR IGNORE` so a statement is safe to re-read.
2. **Mirror the same change into `../schema.sql`** so fresh installs get it directly —
   `schema.sql` and "baseline + all migrations" must always describe the same database.
3. Apply it:

   ```sh
   ./jm.sh --migrate
   ```

## What `--migrate` does

- Snapshots the database first (`--export-db`) so a bad migration is recoverable.
- Reads `PRAGMA user_version` (the high-water mark) and applies every `migrate_NNN_*.sql`
  whose `NNN` is greater than it, in numeric order.
- Runs each migration in its **own transaction** and bumps `user_version = NNN` on success.
  It stops at the first failure and rolls that one back — never a half-migrated database.
- Re-running with nothing pending is a no-op.

So `user_version` is the version the database is *at*; the highest `migrate_NNN` present is
the version it can be brought *to*.

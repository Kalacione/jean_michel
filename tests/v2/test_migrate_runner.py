"""The `--migrate` runner : db.apply_pending_migrations.

schema.sql is the baseline (user_version 0) ; migrate_<NNN>_*.sql files bump it forward,
applied once, in order, atomically. These tests use throwaway temp DBs + migration files
(no real DB, no real migrations dir).
"""

from __future__ import annotations

import sqlite3

import pytest

from jeanmichel import db


def _user_version(path) -> int:
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _migration(migdir, name: str, sql: str) -> None:
    (migdir / name).write_text(sql, encoding="utf-8")


def test_applies_pending_and_bumps_version(tmp_path):
    dbp = tmp_path / "t.db"
    sqlite3.connect(dbp).close()  # fresh DB → user_version 0
    migdir = tmp_path / "migrations"
    migdir.mkdir()
    _migration(migdir, "migrate_001_add_t.sql", "CREATE TABLE t (x INTEGER);")
    _migration(migdir, "migrate_002_seed_t.sql", "INSERT INTO t (x) VALUES (42);")

    applied = db.apply_pending_migrations(dbp, migdir)

    assert applied == ["migrate_001_add_t.sql", "migrate_002_seed_t.sql"]
    assert _user_version(dbp) == 2
    with sqlite3.connect(dbp) as c:
        assert c.execute("SELECT x FROM t").fetchone()[0] == 42


def test_idempotent_rerun_is_noop(tmp_path):
    dbp = tmp_path / "t.db"
    sqlite3.connect(dbp).close()
    migdir = tmp_path / "migrations"
    migdir.mkdir()
    _migration(migdir, "migrate_001_add_t.sql", "CREATE TABLE t (x INTEGER);")
    db.apply_pending_migrations(dbp, migdir)

    assert db.apply_pending_migrations(dbp, migdir) == []  # nothing pending
    assert _user_version(dbp) == 1


def test_only_applies_above_current_version(tmp_path):
    dbp = tmp_path / "t.db"
    with sqlite3.connect(dbp) as c:
        c.execute("PRAGMA user_version = 1")  # pretend migration 1 already ran
    migdir = tmp_path / "migrations"
    migdir.mkdir()
    _migration(migdir, "migrate_001_old.sql", "CREATE TABLE old_t (x);")
    _migration(migdir, "migrate_002_new.sql", "CREATE TABLE new_t (x);")

    applied = db.apply_pending_migrations(dbp, migdir)

    assert applied == ["migrate_002_new.sql"]
    assert _user_version(dbp) == 2
    with sqlite3.connect(dbp) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "new_t" in tables and "old_t" not in tables  # 001 skipped


def test_failure_stops_and_does_not_bump(tmp_path):
    dbp = tmp_path / "t.db"
    sqlite3.connect(dbp).close()
    migdir = tmp_path / "migrations"
    migdir.mkdir()
    _migration(migdir, "migrate_001_ok.sql", "CREATE TABLE ok_t (x);")
    _migration(migdir, "migrate_002_bad.sql", "THIS IS NOT VALID SQL;")

    with pytest.raises(sqlite3.Error):
        db.apply_pending_migrations(dbp, migdir)

    # 001 committed (version 1) ; 002 failed → rolled back, no half-applied state.
    assert _user_version(dbp) == 1
    with sqlite3.connect(dbp) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_t" in tables


def test_ignores_non_migration_files(tmp_path):
    dbp = tmp_path / "t.db"
    sqlite3.connect(dbp).close()
    migdir = tmp_path / "migrations"
    migdir.mkdir()
    (migdir / "README.md").write_text("# migrations", encoding="utf-8")
    _migration(migdir, "migrate_001_t.sql", "CREATE TABLE t (x);")

    assert db.apply_pending_migrations(dbp, migdir) == ["migrate_001_t.sql"]

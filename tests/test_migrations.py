import sqlite3

import pytest

from edutictac_community.db import connect
from edutictac_community.migrations import Migration, apply_migrations, current_version


def test_apply_migrations_tracks_namespace_versions(tmp_path):
    db_path = str(tmp_path / "m.db")
    with connect(db_path) as conn:
        version = apply_migrations(
            conn,
            "test",
            [
                Migration(1, "CREATE TABLE example (id TEXT PRIMARY KEY);"),
                Migration(2, "ALTER TABLE example ADD COLUMN name TEXT DEFAULT '';"),
            ],
        )

        assert version == 2
        assert current_version(conn, "test") == 2
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(example)")]
        assert columns == ["id", "name"]


def test_apply_migrations_is_idempotent(tmp_path):
    db_path = str(tmp_path / "m.db")
    calls = []

    def migration(conn: sqlite3.Connection) -> None:
        calls.append("ran")
        conn.execute("CREATE TABLE example (id TEXT PRIMARY KEY)")

    with connect(db_path) as conn:
        assert apply_migrations(conn, "test", [Migration(1, migration)]) == 1
        assert apply_migrations(conn, "test", [Migration(1, migration)]) == 1

    assert calls == ["ran"]


def test_apply_migrations_rejects_gaps(tmp_path):
    db_path = str(tmp_path / "m.db")
    with connect(db_path) as conn:
        with pytest.raises(ValueError):
            apply_migrations(conn, "test", [Migration(2, "SELECT 1;")])

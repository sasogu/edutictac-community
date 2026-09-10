"""Migracions SQLite xicotetes i explícites per als backends EduTicTac."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


MigrationFn = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    apply: str | MigrationFn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _edutictac_migrations (
            namespace TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def current_version(conn: sqlite3.Connection, namespace: str) -> int:
    ensure_migrations_table(conn)
    row = conn.execute(
        "SELECT version FROM _edutictac_migrations WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    return int(row["version"] if row else 0)


def apply_migrations(
    conn: sqlite3.Connection,
    namespace: str,
    migrations: Iterable[Migration],
) -> int:
    """Aplica migracions pendents i retorna la versió final.

    El versionat és per `namespace`, no global de la base de dades. Això permet
    usar la mateixa SQLite per taules pròpies de l'app i per peces compartides.
    """
    ensure_migrations_table(conn)
    version = current_version(conn, namespace)
    ordered = sorted(migrations, key=lambda item: item.version)

    for migration in ordered:
        if migration.version <= version:
            continue
        if migration.version != version + 1:
            raise ValueError(
                f"migration gap for {namespace}: have {version}, next {migration.version}"
            )
        if isinstance(migration.apply, str):
            conn.executescript(migration.apply)
        else:
            migration.apply(conn)
        conn.execute(
            """
            INSERT INTO _edutictac_migrations (namespace, version, applied_at)
            VALUES (?, ?, ?)
            ON CONFLICT(namespace) DO UPDATE SET
                version = excluded.version,
                applied_at = excluded.applied_at
            """,
            (namespace, migration.version, _now()),
        )
        version = migration.version

    return version

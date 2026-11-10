from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import ConfigurationError
from .schema import MIGRATIONS, SCHEMA_VERSION
from .utils import utc_now


class Database:
    """SQLite owner with explicit serialized write transactions."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise ConfigurationError(
                    f"Database schema {current} is newer than supported schema {SCHEMA_VERSION}"
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                migration = MIGRATIONS.get(version)
                if migration is None:
                    raise ConfigurationError(f"Missing database migration {version}")
                applied_at = utc_now().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{migration}\n"
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                    f"VALUES ({version}, '{applied_at}');\n"
                    f"PRAGMA user_version = {version};\n"
                    "COMMIT;"
                )
                try:
                    connection.executescript(script)
                except sqlite3.DatabaseError:
                    if connection.in_transaction:
                        connection.rollback()
                    raise

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

"""
Connection + transaction helpers.

Concurrency strategy (see README "Concurrency" section for the full writeup):
SQLite in WAL mode allows concurrent readers, but only one writer at a time.
Every mutating operation (stock in, bill finalize, khata entry) opens its
transaction with `BEGIN IMMEDIATE`, which grabs the write lock up front —
instead of the default deferred lock that can deadlock/race when two writers
both start with a read. Combined with "check the current quantity and update
it in the same transaction", this makes the oversell guard and double-spend
guard atomic: a second writer simply blocks until the first commits, then
sees the post-commit state.

For a single physical shop this is more than enough — throughput is a human
typing on a phone, not a web farm — and it avoids running a second database
service just for this take-home.
"""
from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "kirana.db")
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _db_path() -> str:
    # Read fresh every call (not cached at import time) so tests can point
    # different processes/threads at different DB files via the env var
    # without needing to reload this module.
    return os.environ.get("KIRANA_DB_PATH", _DEFAULT_DB_PATH)


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")  # wait up to 5s for the write lock instead of erroring
    conn.row_factory = sqlite3.Row


def get_connection() -> sqlite3.Connection:
    db_path = _db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=5)  # autocommit; we manage BEGIN ourselves
    _configure(conn)
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())
    finally:
        conn.close()


@contextmanager
def write_txn():
    """
    Use for every mutation. BEGIN IMMEDIATE takes the write lock immediately,
    so 'read current stock, then write new stock' can't race with another
    writer doing the same read-then-write in between.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


@contextmanager
def read_conn():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

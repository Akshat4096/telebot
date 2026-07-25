"""
Tests point KIRANA_DB_PATH at a fresh temp file per test (real local disk,
not a network-mounted directory — see the WAL/BEGIN IMMEDIATE note in
db/db.py: SQLite's locking files don't work reliably over some network
filesystems, so tests deliberately avoid that class of flakiness).
"""
import os
import tempfile
import pytest

from db.db import init_db


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmpdir = tempfile.mkdtemp(prefix="kirana_test_")
    db_path = os.path.join(tmpdir, "kirana.db")
    monkeypatch.setenv("KIRANA_DB_PATH", db_path)
    init_db()
    yield db_path

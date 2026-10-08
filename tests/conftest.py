"""Shared test setup: a throwaway database, offline mode, no background watcher."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MODE"] = "offline"
os.environ["DISABLE_WATCHER"] = "1"
os.environ["DEMO_PASSWORD"] = "TestPass!2026"
# The tests use SQLite unless TEST_DATABASE_URL is set (PostgreSQL), so a DATABASE_URL in your .env
# is never touched by the tests, even though they only ever use throwaway schemas.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")

import db  # noqa: E402

PASSWORD = "TestPass!2026"


@pytest.fixture()
def fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    db.set_db_path(path)
    db.init_db()
    db.seed(PASSWORD)
    yield path
    try:
        db.drop_database(path)
    except PermissionError:      # Windows: a background worker thread may still hold the SQLite file open
        pass

"""The database layer behaves the same on SQLite and PostgreSQL (set TEST_DATABASE_URL to run on PostgreSQL)."""
import os

import pytest

import db


def test_insert_returns_the_new_id_and_ids_keep_counting(fresh_db):
    a = db.execute("INSERT INTO vendors(name, email) VALUES (?, ?)", ("Test One", "a@b.co"))
    b = db.execute("INSERT INTO vendors(name, email) VALUES (?, ?)", ("Test Two", "c@d.co"))
    assert isinstance(a, int) and b == a + 1


def test_insert_or_ignore_skips_duplicates_without_error(fresh_db):
    db.execute("INSERT OR IGNORE INTO vendors(name, email) VALUES (?, ?)", ("Same", "x@y.co"))
    db.execute("INSERT OR IGNORE INTO vendors(name, email) VALUES (?, ?)", ("Same", "other@y.co"))
    assert db.query("SELECT COUNT(*) n FROM vendors WHERE name='Same'", one=True)["n"] == 1


def test_rows_work_by_name_and_by_position_and_as_dicts(fresh_db):
    row = db.query("SELECT username, role FROM users WHERE username='mokshana'", one=True)
    assert row["username"] == "mokshana" and row[0] == "mokshana" and row[1] == "manager" and dict(row)["role"] == "manager"


def test_like_ignores_case_on_both_engines(fresh_db):
    assert db.query("SELECT COUNT(*) n FROM users WHERE full_name LIKE ?", ("%MEERA%",), one=True)["n"] == 1


def test_percent_signs_in_sql_text_survive_parameter_translation(fresh_db):
    n = db.query("SELECT COUNT(*) n FROM users WHERE full_name LIKE '%a%' AND username != ?", ("nobody",), one=True)["n"]
    assert n >= 1


def test_averages_come_back_as_plain_numbers(fresh_db):
    db.execute("INSERT INTO invoices(file_name, stored_path, source, created_at, processing_ms) VALUES (?,?,?,?,?)", ("a", "a", "upload", "x", 1000))
    db.execute("INSERT INTO invoices(file_name, stored_path, source, created_at, processing_ms) VALUES (?,?,?,?,?)", ("b", "b", "upload", "x", 2001))
    avg = db.query("SELECT AVG(processing_ms) a FROM invoices", one=True)["a"]
    assert isinstance(avg, float) and avg / 1000 == pytest.approx(1.5005)


def test_table_columns_and_database_exists(fresh_db):
    assert "last_error" in db.table_columns("emails") and "theme" in db.table_columns("users")
    assert db.database_exists(fresh_db)


def test_a_throwaway_database_is_separate_from_the_real_one(fresh_db):
    other = os.path.join(os.path.dirname(fresh_db), "other.db")
    with db.using(other):
        db.init_db()
        db.execute("INSERT INTO vendors(name, email) VALUES (?, ?)", ("Only Here", "o@o.co"))
        assert db.query("SELECT COUNT(*) n FROM vendors WHERE name='Only Here'", one=True)["n"] == 1
    assert db.query("SELECT COUNT(*) n FROM vendors WHERE name='Only Here'", one=True)["n"] == 0
    db.drop_database(other)
    assert not db.database_exists(other)


def test_the_sqlite_to_postgres_translation_is_exact():
    assert db._to_pg("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", True) == \
        "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    assert db._to_pg("SELECT * FROM t WHERE a LIKE ? AND b = '50%'", True) == "SELECT * FROM t WHERE a ILIKE %s AND b = '50%%'"
    assert db._to_pg("SELECT 1 WHERE x LIKE '%a%'", False) == "SELECT 1 WHERE x ILIKE '%a%'"
    assert "SERIAL PRIMARY KEY" in db._ddl_to_pg("id INTEGER PRIMARY KEY") and "DOUBLE PRECISION" in db._ddl_to_pg("x REAL")


def test_the_audit_chain_verifies_and_detects_tampering(fresh_db):
    for i in range(3):
        db.log_audit(None, "system", "system", f"step{i}", "ok", "x")
    assert db.verify_audit_chain() == (True, None)
    db.execute("UPDATE audit_log SET reason='edited' WHERE step='step1'")
    ok, broken = db.verify_audit_chain()
    assert not ok and broken is not None

"""
db.py - database layer: SQLite by default, PostgreSQL when DATABASE_URL is set.

  DATABASE_URL=postgresql://user:password@localhost:5432/clearpay   -> PostgreSQL
  (not set)                                                         -> SQLite file in data/

Everything else in the app talks to the database only through query() and execute(), so the engine can be
swapped without touching the rest of the code. In PostgreSQL the "throwaway databases" used by evaluation and
by the tests are separate SCHEMAS inside the same database, so they can never touch the real data.

Holds:
  * the schema (vendors, purchase_orders, po_lines, invoices, ledger,
    audit_log, emails, users, settings, investigations, inbox_messages)
  * deterministic seed data (8 vendors, 25 purchase orders, 4 demo users),
    or imported data from seed_data.json when that file exists
  * small query helpers
  * a tamper-evident audit log (each row stores a hash of the previous row)

The database path can be switched at runtime (evaluate.py uses a throwaway
copy so test runs never touch the real data).
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()          # so every entry point (app, import, migrate, evaluate) sees DATABASE_URL

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.lower().startswith(("postgres://", "postgresql://"))
if IS_PG:
    import psycopg
    from psycopg.adapt import Loader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")          # private: never served from /static
INBOX_DIR = os.path.join(BASE_DIR, "invoices_inbox")
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_invoices")
REDTEAM_DIR = os.path.join(BASE_DIR, "redteam_invoices")

_DEFAULT_PATH = os.path.join(DATA_DIR, "ap_agent.db")
_db_path = _DEFAULT_PATH
_local = threading.local()
_audit_lock = threading.Lock()      # audit rows must be chained one at a time

for _d in (DATA_DIR, UPLOAD_DIR, INBOX_DIR):
    os.makedirs(_d, exist_ok=True)


# --------------------------------------------------------------------------
# Connection helpers
# --------------------------------------------------------------------------
def set_db_path(path):
    """Point every new connection at a different database (a file in SQLite, a schema in PostgreSQL)."""
    global _db_path
    close()
    _db_path = path


def get_db_path():
    return getattr(_local, "override", None) or _db_path


@contextmanager
def using(path):
    """Use another database in THIS thread only (evaluation runs use this, so the live app keeps
    working on the real database at the same time)."""
    old = getattr(_local, "override", None)
    _local.override = path
    try:
        yield
    finally:
        close()
        _local.override = old


class Row(dict):
    """A result row. Look values up by column name (row["id"]) or position (row[0]), like sqlite3.Row."""
    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def _schema_for(path):
    """PostgreSQL: the real data lives in schema 'public'; any other 'path' (evaluation, tests) gets its own schema."""
    if os.path.abspath(path) == os.path.abspath(_DEFAULT_PATH):
        return "public"
    base = re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(path))[0].lower()).strip("_")[:30] or "db"
    return f"s_{base}_{hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:6]}"


_NO_ID_TABLES = {"settings"}           # tables without an integer "id" column


def _to_pg(sql, has_params):
    """Translate the few SQLite-only spellings we use into PostgreSQL."""
    m = re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO", sql, re.I)
    if m:
        sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.I).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    sql = re.sub(r"(?<![A-Za-z])LIKE\b", "ILIKE", sql, flags=re.I)        # SQLite's LIKE ignores case; PostgreSQL's does not
    if has_params:
        sql = sql.replace("%", "%%").replace("?", "%s")
    return sql


def _ddl_to_pg(sql):
    sql = re.sub(r"\bINTEGER PRIMARY KEY\b", "SERIAL PRIMARY KEY", sql)
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)


if IS_PG:
    class _FloatLoader(Loader):
        def load(self, data):          # NUMERIC (for example AVG of an integer column) -> float, like SQLite
            return float(data)

    def _row_factory(cursor):
        names = [c.name for c in (cursor.description or [])]
        return lambda values: Row(zip(names, values))


class _Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid, self.rowcount = cur, lastrowid, cur.rowcount

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class _PgConn:
    """Gives a PostgreSQL connection the same small interface the app uses on SQLite connections."""

    def __init__(self, raw, schema):
        self.raw, self.schema = raw, schema

    def execute(self, sql, params=()):
        has_params = bool(params)
        sql = _to_pg(sql, has_params)
        wants_id = False
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if m and m.group(1).lower() not in _NO_ID_TABLES and "RETURNING" not in sql.upper():
            sql, wants_id = sql.rstrip().rstrip(";") + " RETURNING id", True
        cur = self.raw.cursor()
        cur.execute(sql, tuple(params) if has_params else None)
        last = None
        if wants_id:
            row = cur.fetchone()
            last = row["id"] if row else None
        return _Cursor(cur, last)

    def executescript(self, sql):
        self.raw.execute(_ddl_to_pg(sql))

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def _pg_connect(schema):
    raw = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=_row_factory)
    raw.adapters.register_loader("numeric", _FloatLoader)
    if schema != "public":
        raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')           # schema names are generated by _schema_for
    raw.execute(f'SET search_path TO "{schema}"')
    return _PgConn(raw, schema)


def conn():
    """One connection per thread (database connections are not shared between threads)."""
    path = get_db_path()
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        if c is not None:
            c.close()
        if IS_PG:
            c = _pg_connect(_schema_for(path))
        else:
            c = sqlite3.connect(path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA journal_mode = WAL")
        _local.conn, _local.path = c, path
    return c


def close():
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def query(sql, params=(), one=False):
    """Run a SELECT. Always parameterised (never string-formatted) to block SQL injection."""
    cur = conn().execute(sql, params)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    c = conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur.lastrowid


def describe():
    """A one-line description of where the data lives (never includes the password)."""
    if IS_PG:
        u = urlsplit(DATABASE_URL)
        schema = _schema_for(get_db_path())
        return (f"PostgreSQL {u.username or ''}@{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}"
                + ("" if schema == "public" else f" (schema {schema})"))
    return f"SQLite {get_db_path()}"


def engine():
    return "PostgreSQL" if IS_PG else "SQLite"


def database_exists(path):
    """Does a database (file or schema) exist at this path, with our tables in it?"""
    if not IS_PG:
        return os.path.exists(path)
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        return bool(c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = 'invoices'",
                              (_schema_for(path),)).fetchone())


def drop_database(path):
    """Remove a throwaway database (tests). Never touches the real data."""
    close()
    if IS_PG:
        schema = _schema_for(path)
        if schema != "public":
            with psycopg.connect(DATABASE_URL, autocommit=True) as c:
                c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    else:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)


def table_columns(table):
    if IS_PG:
        return [r["name"] for r in conn().execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()",
            (table,))]
    return [r["name"] for r in conn().execute(f"PRAGMA table_info({table})")]   # nosec B608 - fixed table names


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def money(x):
    """Round to 2 decimals the way accountants expect (half up)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ap','manager','admin')),
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    department TEXT,
    theme TEXT NOT NULL DEFAULT 'system',      -- light | dark | system
    last_login_at TEXT,
    password_changed_at TEXT,
    has_password INTEGER NOT NULL DEFAULT 1,   -- 0 for accounts created by Google sign-up
    google_sub TEXT,                           -- Google's stable account id, once linked
    google_email TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT,                           -- authenticator secret (encrypted when MFA_ENCRYPTION_KEY is set)
    mfa_last_step INTEGER,                     -- last accepted 30-second step, so a code can't be used twice
    mfa_recovery TEXT,                         -- JSON list of hashed one-time recovery codes
    mfa_enabled_at TEXT
);
CREATE TABLE IF NOT EXISTS access_requests (   -- a new Google account asking for access; admins approve by email
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL,                  -- SHA-256 of the one-time link token (the token itself is only in the email)
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decision TEXT,                             -- approved | rejected
    decided_by TEXT,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS user_sessions (     -- one row per sign-in; lets people see and end their sessions
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    user_agent TEXT,
    ip TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS notifications (     -- in-app notifications, one row per recipient
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,                        -- approved | rejected | needs_approval | attention | failed | security
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    invoice_id INTEGER,
    actor TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, id);
CREATE INDEX IF NOT EXISTS ix_sess_user ON user_sessions(user_id);
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    gstin TEXT,
    bank_account TEXT,
    ifsc TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    msme INTEGER NOT NULL DEFAULT 0    -- small supplier (MSME): payment is due within 45 days at most
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY,
    po_number TEXT UNIQUE NOT NULL,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    issue_date TEXT NOT NULL,
    subtotal REAL NOT NULL,
    tax_rate REAL NOT NULL,
    tax REAL NOT NULL,
    total REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS po_lines (
    id INTEGER PRIMARY KEY,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    description TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    file_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    file_type TEXT,
    source TEXT NOT NULL,              -- 'upload' | 'inbox' | 'eval'
    uploaded_by INTEGER REFERENCES users(id),
    po_hint TEXT,                      -- PO typed by the user on upload (optional)
    report_email TEXT,                 -- send report to this address (optional)
    status TEXT NOT NULL DEFAULT 'Processing',
    vendor_name TEXT,
    vendor_id INTEGER,
    invoice_number TEXT,
    invoice_date TEXT,
    po_number TEXT,
    subtotal REAL,
    tax REAL,
    total REAL,
    confidence REAL,
    extracted_json TEXT,
    checks_json TEXT,                  -- raw check results (used by what-if replay)
    issues_json TEXT,
    comparison_json TEXT,
    highlights_json TEXT,
    decision_reason TEXT,
    summary TEXT,
    agent_mode TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    processing_ms INTEGER,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    manager_comment TEXT,
    payment_terms_days INTEGER,        -- from the invoice, else the default setting
    due_date TEXT,                     -- worked out in code: invoice date + terms
    email_from TEXT,                   -- set when the invoice arrived by email
    email_subject TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER UNIQUE NOT NULL REFERENCES invoices(id),
    vendor_id INTEGER,
    po_number TEXT,
    amount REAL NOT NULL,
    status TEXT NOT NULL,              -- pending_approval | ready_to_pay | paid | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note TEXT,
    paid_at TEXT,                      -- a PERSON recorded that the payment was made (outside ClearPay)
    paid_by INTEGER
);
CREATE TABLE IF NOT EXISTS investigations (    -- the Investigator's findings: a recommendation, never a decision
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    mode TEXT NOT NULL,                -- ai | rules (fallback when the AI is unavailable)
    recommendation TEXT NOT NULL,      -- approve | reject | ask_vendor
    confidence REAL,
    summary TEXT NOT NULL,
    findings_json TEXT,
    steps_json TEXT,
    similar_json TEXT,
    email_id INTEGER,
    note TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    requested_by TEXT,
    invoice_processed_at TEXT,         -- which processing run it looked at (stale after "Process again")
    human_decision TEXT,               -- what the person then did, for the agreement rate
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_invest_inv ON investigations(invoice_id);
CREATE TABLE IF NOT EXISTS inbox_messages (    -- emails already read by the mailbox pickup
    id INTEGER PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,
    from_addr TEXT,
    subject TEXT,
    result TEXT NOT NULL,
    invoice_ids TEXT,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER REFERENCES invoices(id),
    recipient_type TEXT NOT NULL,      -- manager | vendor | ap_team | custom
    purpose TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | sent (mock)
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    invoice_id INTEGER,
    actor TEXT NOT NULL,               -- 'agent', 'system', or a username
    actor_type TEXT NOT NULL,          -- agent | system | ap | manager | admin
    step TEXT NOT NULL,
    result TEXT NOT NULL,              -- ok | problem | info | blocked
    reason TEXT,
    detail_json TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inv_vendor_num ON invoices(vendor_id, invoice_number);
CREATE INDEX IF NOT EXISTS ix_audit_invoice ON audit_log(invoice_id);
CREATE INDEX IF NOT EXISTS ix_inv_po ON invoices(po_number);
CREATE INDEX IF NOT EXISTS ix_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS ix_poline_po ON po_lines(po_id);
"""

# Business rules live in the settings table so admins can change them without code.
DEFAULT_SETTINGS = {
    "auto_approve_limit": "5000",        # INR, invoice total incl. tax
    "variance_tolerance_pct": "5",       # % difference from PO allowed for "pending"
    "min_confidence": "0.7",             # below this a human must review
    "split_window_days": "14",           # look-back window for split-invoice detection
    "cost_saved_per_invoice_usd": "12",       # kept for older databases
    "cost_saved_per_invoice_inr": "860",      # AP staff time saved per invoice handled with no person (INR); ~$9.79 (range $9.40-$10.18) at ~Rs 88/USD
    "groq_input_usd_per_m": "0.59",      # estimate, check Groq pricing
    "groq_output_usd_per_m": "0.79",
    "ap_team_email": "ap-team@company.example",
    "manager_email": "manager@company.example",
    "default_payment_terms_days": "30",  # when the invoice states no terms
    "msme_max_days": "45",               # MSMED Act: small suppliers must be paid within 45 days
    "auto_investigate": "1",             # run the Investigator on every flagged invoice (1) or only on request (0)
    "smart_match_min_confidence": "0.7", # AI item matches below this are treated as "not on the PO"
}


TABLES = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)


def init_db():
    conn().executescript(SCHEMA)
    if "last_error" not in table_columns("emails"):       # upgrade older databases in place
        conn().execute("ALTER TABLE emails ADD COLUMN last_error TEXT")
    ucols = table_columns("users")
    for col, ddl in (("department", "TEXT"), ("theme", "TEXT NOT NULL DEFAULT 'system'"),
                     ("last_login_at", "TEXT"), ("password_changed_at", "TEXT"),
                     ("has_password", "INTEGER NOT NULL DEFAULT 1"), ("google_sub", "TEXT"), ("google_email", "TEXT"),
                     ("mfa_enabled", "INTEGER NOT NULL DEFAULT 0"), ("mfa_secret", "TEXT"), ("mfa_last_step", "INTEGER"),
                     ("mfa_recovery", "TEXT"), ("mfa_enabled_at", "TEXT")):
        if col not in ucols:
            conn().execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")  # nosec B608 - fixed column names
    conn().execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_google_sub ON users(google_sub)")
    for table, columns in (("vendors", (("msme", "INTEGER NOT NULL DEFAULT 0"),)),
                           ("invoices", (("payment_terms_days", "INTEGER"), ("due_date", "TEXT"),
                                         ("email_from", "TEXT"), ("email_subject", "TEXT"))),
                           ("ledger", (("paid_at", "TEXT"), ("paid_by", "INTEGER")))):
        have = table_columns(table)
        for col, ddl in columns:
            if col not in have:
                conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")  # nosec B608 - fixed names
    for k, v in DEFAULT_SETTINGS.items():
        conn().execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn().commit()


def reset_db():
    """Empty the current database and recreate the tables (SQLite: delete the file; PostgreSQL: drop our tables)."""
    path = get_db_path()
    close()
    if IS_PG:
        schema = _schema_for(path)
        with psycopg.connect(DATABASE_URL, autocommit=True) as c:
            if schema == "public":
                for t in reversed(TABLES):
                    c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')       # only our own tables
            else:
                c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    else:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
    init_db()


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def get_settings():
    s = {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}
    return {
        "auto_approve_limit": float(s["auto_approve_limit"]),
        "variance_tolerance_pct": float(s["variance_tolerance_pct"]),
        "min_confidence": float(s["min_confidence"]),
        "split_window_days": int(s["split_window_days"]),
        "cost_saved_per_invoice_usd": float(s["cost_saved_per_invoice_usd"]),
        "cost_saved_per_invoice_inr": float(s.get("cost_saved_per_invoice_inr", 1000)),
        "groq_input_usd_per_m": float(s["groq_input_usd_per_m"]),
        "groq_output_usd_per_m": float(s["groq_output_usd_per_m"]),
        "ap_team_email": s["ap_team_email"],
        "manager_email": s["manager_email"],
        "default_payment_terms_days": int(float(s.get("default_payment_terms_days", 30))),
        "msme_max_days": int(float(s.get("msme_max_days", 45))),
        "auto_investigate": s.get("auto_investigate", "1") == "1",
        "smart_match_min_confidence": float(s.get("smart_match_min_confidence", 0.7)),
    }


def set_setting(key, value):
    if key not in DEFAULT_SETTINGS:
        raise ValueError("Unknown setting")
    execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))


# --------------------------------------------------------------------------
# Tamper-evident audit log
# --------------------------------------------------------------------------
GENESIS = "0" * 64


def _row_hash(prev_hash, ts, invoice_id, actor, actor_type, step, result, reason, detail_json):
    payload = "|".join(str(x) for x in (prev_hash, ts, invoice_id, actor, actor_type,
                                          step, result, reason, detail_json))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log_audit(invoice_id, actor, actor_type, step, result, reason="", detail=None):
    """Append one audit row. Rows are never updated or deleted by the app."""
    detail_json = json.dumps(detail or {}, default=str, sort_keys=True)
    with _audit_lock:
        if IS_PG:                      # one writer at a time across ALL processes, per schema
            conn().execute("SELECT pg_advisory_lock(hashtext(current_schema()))")
        try:
            last = query("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1", one=True)
            prev = last["hash"] if last else GENESIS
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            h = _row_hash(prev, ts, invoice_id, actor, actor_type, step, result, reason, detail_json)
            execute("""INSERT INTO audit_log(ts, invoice_id, actor, actor_type, step, result,
                       reason, detail_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ts, invoice_id, actor, actor_type, step, result, reason, detail_json, prev, h))
        finally:
            if IS_PG:
                conn().execute("SELECT pg_advisory_unlock(hashtext(current_schema()))")


def verify_audit_chain():
    """Recompute every hash. Returns (ok, first_broken_row_id)."""
    prev = GENESIS
    for r in query("SELECT * FROM audit_log ORDER BY id"):
        expected = _row_hash(prev, r["ts"], r["invoice_id"], r["actor"], r["actor_type"],
                             r["step"], r["result"], r["reason"], r["detail_json"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return False, r["id"]
        prev = r["hash"]
    return True, None


# --------------------------------------------------------------------------
# Seed data (deterministic, so evaluation runs are repeatable)
# --------------------------------------------------------------------------
VENDORS = [
    # name, email, GSTIN (fake, valid format), bank account, IFSC
    ("Apex Office Supplies", "billing@apexoffice.example", "36AAACA1234F1Z5", "501002345671", "HDFC0001234"),
    ("BrightPath Logistics", "accounts@brightpath.example", "36AABCB2345G1Z2", "602113456782", "ICIC0002345"),
    ("Nimbus Cloud Services", "billing@nimbuscloud.example", "29AACCN3456H1Z9", "703224567893", "SBIN0003456"),
    ("Vertex Hardware", "accounts@vertexhw.example", "36AADCV4567J1Z6", "804335678904", "UTIB0004567"),
    ("Greenleaf Catering", "billing@greenleaf.example", "36AAECG5678K1Z3", "905446789015", "KKBK0005678"),
    ("Orbit Print Co.", "hello@orbitprint.example", "36AAFCO6789L1Z0", "106557890126", "HDFC0006789"),
    ("Acme Corp", "ar@acmecorp.example", "27AAGCA7890M1Z7", "207668901237", "ICIC0007890"),
    ("Sahyadri Electricals", "billing@sahyadri.example", "36AAHCS8901N1Z4", "308779012348", "SBIN0008901"),
]

TAX_RATE = 0.18  # GST 18% (CGST 9% + SGST 9%)

# po_number, vendor index, issue date, [(description, qty, unit price)]
PURCHASE_ORDERS = [
    ("PO-2001", 0, "2026-08-20", [("A4 paper ream", 10, 250), ("Stapler", 4, 180)]),
    ("PO-2002", 5, "2026-08-21", [("Business cards box", 5, 300), ("Letterhead pack", 2, 650)]),
    ("PO-2003", 4, "2026-08-21", [("Lunch box", 20, 120)]),
    ("PO-2004", 3, "2026-08-22", [("HDMI cable", 6, 350), ("Keyboard", 2, 650)]),
    ("PO-2005", 7, "2026-08-22", [("LED tube light", 12, 220)]),
    ("PO-2006", 0, "2026-08-23", [("Whiteboard marker", 15, 45), ("Sticky notes pad", 20, 60)]),
    ("PO-2007", 1, "2026-08-24", [("Courier pickup", 8, 250), ("Packaging box", 30, 40)]),
    ("PO-2008", 6, "2026-08-24", [("Cleaning supplies kit", 3, 900)]),
    ("PO-2009", 2, "2026-08-25", [("Domain renewal", 2, 1200), ("SSL certificate", 1, 1500)]),
    ("PO-2010", 5, "2026-08-25", [("Brochure", 200, 9), ("Poster", 10, 120)]),
    ("PO-2011", 2, "2026-08-26", [("Cloud server (monthly)", 3, 4200)]),
    ("PO-2012", 3, "2026-08-26", [("Laptop docking station", 4, 5500)]),
    ("PO-2013", 1, "2026-08-27", [("Warehouse handling", 10, 350), ("Pallet shipping", 12, 280)]),
    ("PO-2014", 7, "2026-08-27", [("UPS battery", 2, 4800)]),
    ("PO-2015", 0, "2026-08-28", [("Office chair", 2, 1800)]),
    ("PO-2016", 4, "2026-08-28", [("Event catering", 1, 3000)]),
    ("PO-2017", 6, "2026-08-29", [("Floor polish", 5, 520)]),
    ("PO-2018", 5, "2026-08-29", [("Flyer", 300, 5)]),
    ("PO-2019", 3, "2026-08-30", [("Wireless mouse", 10, 250)]),
    ("PO-2020", 7, "2026-08-30", [("Extension board", 6, 400)]),
    ("PO-2021", 1, "2026-08-31", [("Courier pickup", 10, 250)]),
    ("PO-2022", 4, "2026-08-31", [("Snack tray", 15, 150)]),
    ("PO-2023", 0, "2026-09-01", [("Printer toner", 2, 1900)]),
    ("PO-2024", 3, "2026-09-01", [("Network switch", 2, 3600)]),
    ("PO-2025", 2, "2026-09-02", [("Backup storage (yearly)", 1, 3200)]),
]

DEMO_USERS = [
    # username, full name, email, role
    ("prashasthi", "Priya Nair", "ap-team@company.example", "ap"),
    ("ap_user2", "Ravi Kumar", "ap-team@company.example", "ap"),
    ("mokshana", "Meera Iyer", "manager@company.example", "manager"),
    ("admin", "Admin", "admin@company.example", "admin"),
]


# --------------------------------------------------------------------------
# Optional imported data (written by import_kaggle_pos.py)
# --------------------------------------------------------------------------
SEED_FILE = os.path.join(BASE_DIR, "seed_data.json")


def load_seed_file(path=SEED_FILE):
    """If seed_data.json exists, use its vendors, purchase orders and setting defaults instead of
    the built-in demo data. evaluate.py's fresh test databases then use the same data automatically."""
    global TAX_RATE
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    VENDORS[:] = [(v["name"], v["email"], v["gstin"], v["bank_account"], v["ifsc"]) for v in data["vendors"]]
    PURCHASE_ORDERS[:] = [(p["po_number"], p["vendor_index"], p["issue_date"],
                           [(ln["description"], ln["quantity"], ln["unit_price"]) for ln in p["lines"]])
                          for p in data["purchase_orders"]]
    TAX_RATE = float(data.get("tax_rate", TAX_RATE))
    DEFAULT_SETTINGS.update({k: str(v) for k, v in data.get("settings", {}).items() if k in DEFAULT_SETTINGS})
    return True


load_seed_file()


def seed(demo_password=None):
    """Insert vendors, purchase orders and demo users (idempotent)."""
    demo_password = demo_password or os.getenv("DEMO_PASSWORD", "ChangeMe!2026")
    for v in VENDORS:
        execute("""INSERT OR IGNORE INTO vendors(name, email, gstin, bank_account, ifsc)
                   VALUES (?,?,?,?,?)""", v)
    vendor_ids = [r["id"] for r in query("SELECT id FROM vendors ORDER BY id")]
    for po_number, v_idx, issued, lines in PURCHASE_ORDERS:
        if query("SELECT 1 FROM purchase_orders WHERE po_number=?", (po_number,), one=True):
            continue
        subtotal = money(sum(q * p for _, q, p in lines))
        tax = money(subtotal * TAX_RATE)
        po_id = execute("""INSERT INTO purchase_orders(po_number, vendor_id, issue_date, subtotal,
                           tax_rate, tax, total) VALUES (?,?,?,?,?,?,?)""",
                        (po_number, vendor_ids[v_idx], issued, subtotal, TAX_RATE, tax, money(subtotal + tax)))
        for desc, q, p in lines:
            execute("INSERT INTO po_lines(po_id, description, quantity, unit_price) VALUES (?,?,?,?)",
                    (po_id, desc, q, p))
    seed_users(demo_password)


def seed_users(demo_password=None):
    """Insert the four demo users (idempotent). load_po_data.py uses this on its own."""
    demo_password = demo_password or os.getenv("DEMO_PASSWORD", "ChangeMe!2026")
    for username, name, email, role in DEMO_USERS:
        dept = {"ap": "Accounts Payable", "manager": "Finance", "admin": "Finance systems"}[role]
        execute("""INSERT OR IGNORE INTO users(username, full_name, email, role, password_hash, department)
                   VALUES (?,?,?,?,?,?)""", (username, name, email, role, generate_password_hash(demo_password), dept))


def po_with_lines(po_number):
    po = query("""SELECT p.*, v.name AS vendor_name FROM purchase_orders p
                  JOIN vendors v ON v.id = p.vendor_id WHERE p.po_number = ?""", (po_number,), one=True)
    if not po:
        return None
    d = dict(po)
    d["lines"] = [dict(r) for r in query("SELECT * FROM po_lines WHERE po_id=? ORDER BY id", (po["id"],))]
    return d


if __name__ == "__main__":
    init_db()
    seed()
    print("Database ready:", describe())

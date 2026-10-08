"""
migrate_sqlite_to_postgres.py - copy your existing ClearPay data from SQLite into PostgreSQL.

    1. Put your PostgreSQL address in .env:   DATABASE_URL=postgresql://clearpay:password@localhost:5432/clearpay
    2. python migrate_sqlite_to_postgres.py                (copies data/ap_agent.db)
       python migrate_sqlite_to_postgres.py --wipe         (empties the PostgreSQL tables first)
       python migrate_sqlite_to_postgres.py --sqlite C:\\path\\to\\other.db

Copies every table with the same ids (vendors, purchase orders, invoices, ledger, emails, users, sessions,
notifications, settings and the audit log), fixes the id counters, then checks the copy: row counts, invoice
totals, and the tamper-evident audit chain. The SQLite file is only READ, never changed.
"""
import argparse
import os
import sqlite3
import sys

import db

# parents before children, so foreign keys are always satisfied
TABLE_ORDER = ["users", "vendors", "purchase_orders", "po_lines", "invoices", "ledger", "emails",
               "audit_log", "notifications", "user_sessions", "settings"]
SERIAL_TABLES = ["users", "vendors", "purchase_orders", "po_lines", "invoices", "ledger", "emails", "audit_log",
                 "notifications"]


def main():
    ap = argparse.ArgumentParser(description="Copy ClearPay data from SQLite into PostgreSQL.")
    ap.add_argument("--sqlite", default=os.path.join(db.DATA_DIR, "ap_agent.db"), help="the SQLite file to copy (default: data/ap_agent.db)")
    ap.add_argument("--wipe", action="store_true", help="empty the PostgreSQL tables first")
    a = ap.parse_args()

    if not db.IS_PG:
        sys.exit("DATABASE_URL is not a PostgreSQL address. Add it to .env first, for example:\n"
                 "  DATABASE_URL=postgresql://clearpay:your_password@localhost:5432/clearpay")
    if not os.path.exists(a.sqlite):
        sys.exit(f"SQLite file not found: {a.sqlite}")

    src = sqlite3.connect(f"file:{os.path.abspath(a.sqlite)}?mode=ro", uri=True)    # read-only
    src.row_factory = sqlite3.Row
    print(f"From: SQLite {a.sqlite}\nTo:   {db.describe()}\n")

    if a.wipe:
        db.reset_db()
    else:
        db.init_db()
        busy = {t: db.query(f"SELECT COUNT(*) n FROM {t}", one=True)["n"] for t in ("users", "vendors", "invoices")}   # nosec B608
        if any(busy.values()):
            sys.exit(f"The PostgreSQL database already has data {busy}.\nRun again with --wipe to replace it, "
                     "or point DATABASE_URL at an empty database.")

    raw = db.conn().raw
    copied = {}
    for table in TABLE_ORDER:
        src_cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]          # nosec B608 - fixed table names
        if not src_cols:
            continue                                                                    # table missing in an older database
        cols = [c for c in src_cols if c in db.table_columns(table)]
        rows = [tuple(r) for r in src.execute(f"SELECT {', '.join(cols)} FROM {table}")]   # nosec B608
        if rows:
            marks = ", ".join(["%s"] * len(cols))
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})"           # nosec B608 - names come from our own schema
            if table == "settings":
                sql += " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            with raw.cursor() as cur:
                cur.executemany(sql, rows)
        copied[table] = len(rows)
        print(f"  {table:<16} {len(rows):>6} rows")

    for t in SERIAL_TABLES:                                    # next new row must not reuse an old id
        raw.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 1), "
                    f"(SELECT MAX(id) FROM {t}) IS NOT NULL)")                           # nosec B608

    print("\nChecking the copy ...")
    problems = []
    for table, n in copied.items():
        got = db.query(f"SELECT COUNT(*) n FROM {table}", one=True)["n"]                # nosec B608
        if got != n:
            problems.append(f"{table}: {n} rows in SQLite, {got} in PostgreSQL")
    s_total = src.execute("SELECT ROUND(COALESCE(SUM(total), 0), 2) FROM invoices").fetchone()[0] if "invoices" in copied else 0
    p_total = db.query("SELECT ROUND(CAST(COALESCE(SUM(total), 0) AS numeric), 2) t FROM invoices", one=True)["t"]
    if abs((s_total or 0) - p_total) > 0.005:
        problems.append(f"invoice totals differ: {s_total} vs {p_total}")
    ok, broken = db.verify_audit_chain()
    if not ok:
        problems.append(f"audit chain broken at entry #{broken}")
    if problems:
        sys.exit("PROBLEMS FOUND:\n  " + "\n  ".join(problems))
    print(f"  row counts match, invoice total ₹{p_total:,.2f} matches, audit chain verified ({copied.get('audit_log', 0)} entries).")
    print("\nDone. Start ClearPay as usual: python app.py   (the sidebar will say Database: PostgreSQL)")


if __name__ == "__main__":
    main()

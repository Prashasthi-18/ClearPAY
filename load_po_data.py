"""
load_po_data.py - put the Kaggle purchase-order data into ClearPay's database (PostgreSQL when DATABASE_URL is set).

Purchase orders are MASTER DATA: ClearPay only reads them (the app has no screen to add or edit a PO).
This script is how they get into the database, and it is safe to run again whenever the data should change.

What it loads, from the Kaggle "Procurement KPI Analysis Dataset" CSV:
  * the vendors (the CSV's suppliers; email, bank account, IFSC and GSTIN are FAKE test details)
  * PO-2001 to PO-2039: the same 39 purchase orders that import_kaggle_pos.py uses for the test invoices
  * PO-2040 onward: every other usable row of the CSV (cancelled orders and capped outliers are skipped)

It never touches invoices, the ledger, emails or the audit log. A PO that already exists is skipped, so running it
twice does nothing the second time.

Usage
  python load_po_data.py                       # loads into the database in .env (DATABASE_URL, or the SQLite file)
  python load_po_data.py --dry-run             # show what would be loaded, change nothing
  python load_po_data.py --csv path/to/file.csv --set-limit     # also set the auto-approve limit (default Rs 50,000)
"""
import argparse
import os
import random
import sys

import db
import import_kaggle_pos as kp

GST_RATE = kp.GST_RATE


def plan(rows, limit, seed):
    """Work out what to load. Returns (vendors, first39, extras); every PO row gets a po_number.

    The first 39 are chosen exactly as import_kaggle_pos.py chooses them, so PO-2001..PO-2039 never change.
    The rest follow in the CSV's own order (sorted by its PO_ID) as PO-2040, PO-2041, ..."""
    vendors = kp.build_vendors(rows, random.Random(seed))
    _, first39 = kp.pick_purchase_orders(rows, limit)
    taken = {r["source_po_id"] for r in first39}
    extras = []
    for r in rows:
        if r["source_po_id"] not in taken:
            r["po_number"] = f"PO-{2040 + len(extras)}"
            extras.append(r)
    return vendors, first39, extras


def write(vendors, pos, dry_run=False):
    """Insert vendors and purchase orders that are not in the database yet. Returns a dict of counts."""
    if not dry_run:
        db.init_db()
    has_tables = db.database_exists(db.get_db_path())            # a dry run must not create anything
    existing_pos = {r["po_number"] for r in db.query("SELECT po_number FROM purchase_orders")} if has_tables else set()
    existing_vendors = {r["name"] for r in db.query("SELECT name FROM vendors")} if has_tables else set()
    counts = {"vendors_added": 0, "pos_added": 0, "pos_skipped": 0, "value_added": 0.0}
    if not dry_run:
        for v in vendors:
            if v["name"] not in existing_vendors:
                db.execute("INSERT INTO vendors(name, email, gstin, bank_account, ifsc) VALUES (?,?,?,?,?)",
                           (v["name"], v["email"], v["gstin"], v["bank_account"], v["ifsc"]))
    counts["vendors_added"] = sum(1 for v in vendors if v["name"] not in existing_vendors)
    vendor_id = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM vendors")} if has_tables else {}
    for r in pos:
        if r["po_number"] in existing_pos:
            counts["pos_skipped"] += 1
            continue
        subtotal = db.money(r["quantity"] * r["price"])
        tax = db.money(subtotal * GST_RATE)
        total = db.money(subtotal + tax)
        counts["pos_added"] += 1
        counts["value_added"] += total
        if dry_run:
            continue
        po_id = db.execute("""INSERT INTO purchase_orders(po_number, vendor_id, issue_date, subtotal, tax_rate, tax, total)
                              VALUES (?,?,?,?,?,?,?)""",
                           (r["po_number"], vendor_id[r["supplier"]], r["ordered"], subtotal, GST_RATE, tax, total))
        db.execute("INSERT INTO po_lines(po_id, description, quantity, unit_price) VALUES (?,?,?,?)",
                   (po_id, r["category"], r["quantity"], r["price"]))
    return counts


def main():
    ap = argparse.ArgumentParser(description="Load the Kaggle purchase orders into ClearPay's database.")
    ap.add_argument("--csv", help="path to the Kaggle CSV (default: Procurement_KPI*.csv in this folder)")
    ap.add_argument("--limit", type=float, default=50000, help="auto-approve limit in rupees; also decides which 39 POs are the test POs")
    ap.add_argument("--seed", type=int, default=7, help="random seed for the fake vendor details (keep 7 to match import_kaggle_pos.py)")
    ap.add_argument("--set-limit", action="store_true", help="also set the auto-approve limit in Settings to --limit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be loaded and change nothing")
    a = ap.parse_args()

    path = kp.find_csv(a.csv)
    rows, skipped = kp.load_rows(path)
    vendors, first39, extras = plan(rows, a.limit, a.seed)
    print(f"Database : {db.describe()}")
    print(f"CSV      : {os.path.basename(path)}  ({len(rows)} usable rows; skipped {skipped})")
    print(f"Plan     : {len(vendors)} vendors, PO-2001..PO-2039 ({len(first39)} test POs) + PO-2040..PO-{2039 + len(extras)} ({len(extras)} more)")
    c = write(vendors, first39 + extras, dry_run=a.dry_run)
    verb = "Would add" if a.dry_run else "Added"
    print(f"{verb}    : {c['vendors_added']} vendors, {c['pos_added']} purchase orders worth Rs {c['value_added']:,.2f}"
          f"  ({c['pos_skipped']} already there, skipped)")
    if a.set_limit and not a.dry_run:
        db.set_setting("auto_approve_limit", int(a.limit))
        print(f"Setting  : auto-approve limit is now Rs {int(a.limit):,}")
    elif not a.dry_run:
        print(f"Setting  : auto-approve limit is Rs {db.get_settings()['auto_approve_limit']:,.0f}"
              + ("  (Kaggle amounts are large: run again with --set-limit to use Rs 50,000)" if db.get_settings()["auto_approve_limit"] < 50000 else ""))
    if not a.dry_run and not db.query("SELECT 1 FROM users LIMIT 1", one=True):
        db.seed_users()                       # a brand-new database also needs people to sign in
        print("Users    : created prashasthi, ap_user2, mokshana and admin (password = DEMO_PASSWORD in .env)")
    if not a.dry_run:
        n = db.query("SELECT COUNT(*) n FROM purchase_orders", one=True)["n"]
        v = db.query("SELECT COUNT(*) n FROM vendors", one=True)["n"]
        print(f"Now in the database: {v} vendors, {n} purchase orders.")


if __name__ == "__main__":
    sys.exit(main())

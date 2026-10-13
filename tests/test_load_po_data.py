"""The purchase-order loader: safe to repeat, never touches invoices; and the PO page only reads (SQLite and PostgreSQL)."""
import os
import re
import tempfile

import pytest

import db
import load_po_data as lp
import security
from conftest import PASSWORD

VENDORS = [{"name": "Alpha Inc", "email": "accounts@alphainc.example", "gstin": "36AAACA6305E1ZR", "bank_account": "17090709584", "ifsc": "HDFC0075954"},
           {"name": "Beta Supplies", "email": "accounts@betasupplies.example", "gstin": "29ABHCB9779D1ZP", "bank_account": "162632597597", "ifsc": "ICIC0953893"}]


def pos(n, start=2040):
    return [{"po_number": f"PO-{start + i}", "supplier": VENDORS[i % 2]["name"], "ordered": "2023-01-02",
             "quantity": 10 + i, "price": 12.5, "category": "MRO"} for i in range(n)]


@pytest.fixture()
def empty_db():
    """A database with the tables but no data at all (not even the demo data)."""
    path = os.path.join(tempfile.mkdtemp(), "loader.db")
    db.set_db_path(path)
    db.init_db()
    yield path
    db.drop_database(path)


def test_first_run_adds_everything_and_the_second_run_adds_nothing(empty_db):
    first = lp.write(VENDORS, pos(10))
    assert (first["vendors_added"], first["pos_added"], first["pos_skipped"]) == (2, 10, 0)
    again = lp.write(VENDORS, pos(10))
    assert (again["vendors_added"], again["pos_added"], again["pos_skipped"]) == (0, 0, 10)
    assert db.query("SELECT COUNT(*) n FROM purchase_orders", one=True)["n"] == 10
    assert db.query("SELECT COUNT(*) n FROM vendors", one=True)["n"] == 2


def test_a_loaded_po_has_its_vendor_one_line_and_gst(empty_db):
    lp.write(VENDORS, pos(2))
    po = db.po_with_lines("PO-2041")                       # i = 1 -> Beta Supplies, quantity 11, price 12.50
    assert po["vendor_name"] == "Beta Supplies" and po["issue_date"] == "2023-01-02"
    assert len(po["lines"]) == 1 and po["lines"][0]["quantity"] == 11 and po["lines"][0]["unit_price"] == 12.5
    assert (po["subtotal"], po["tax"], po["total"]) == (137.5, 24.75, 162.25)


def test_a_dry_run_creates_and_changes_nothing(empty_db):
    db.drop_database(empty_db)
    assert not db.database_exists(empty_db)
    c = lp.write(VENDORS, pos(5), dry_run=True)
    assert c["pos_added"] == 5 and not db.database_exists(empty_db)
    db.init_db()
    lp.write(VENDORS, pos(5), dry_run=True)
    assert db.query("SELECT COUNT(*) n FROM purchase_orders", one=True)["n"] == 0


def test_loading_never_touches_invoices(empty_db):
    db.execute("INSERT INTO invoices(file_name, stored_path, source, created_at) VALUES (?,?,?,?)", ("x.pdf", "/tmp/x.pdf", "upload", db.now_iso()))
    lp.write(VENDORS, pos(4))
    assert db.query("SELECT COUNT(*) n FROM invoices", one=True)["n"] == 1


def test_a_new_database_gets_the_four_demo_users_and_nothing_else(empty_db):
    db.seed_users(PASSWORD)
    db.seed_users(PASSWORD)
    assert [r["username"] for r in db.query("SELECT username FROM users ORDER BY id")] == ["prashasthi", "ap_user2", "mokshana", "admin"]
    assert db.query("SELECT COUNT(*) n FROM vendors", one=True)["n"] == 0        # no built-in demo vendors sneak in


def test_the_real_csv_keeps_po_2001_to_2039_and_numbers_the_rest_after_them():
    import import_kaggle_pos as kp
    try:
        path = os.environ.get("KAGGLE_CSV") or kp.find_csv(None)
    except SystemExit:
        pytest.skip("the Kaggle CSV is not in the project folder")
    rows, _ = kp.load_rows(path)
    vendors, first39, extras = lp.plan(rows, 50000, 7)
    assert [r["po_number"] for r in first39] == [f"PO-{2000 + i}" for i in range(1, 40)]
    assert [r["po_number"] for r in extras] == [f"PO-{2040 + i}" for i in range(len(extras))]
    assert len({r["source_po_id"] for r in first39 + extras}) == len(first39) + len(extras) == len(rows)
    rows2, _ = kp.load_rows(path)
    again = lp.plan(rows2, 50000, 7)
    assert [r["source_po_id"] for r in again[1]] == [r["source_po_id"] for r in first39]      # deterministic


# ---------- the PO page (needs the app) ----------
@pytest.fixture()
def app(fresh_db):
    import app as appmod
    appmod.app.config["TESTING"] = True
    security.reset_rate_limits()
    return appmod.app


def signed_in(app):
    c = app.test_client()
    t = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"username": "prashasthi", "password": PASSWORD, "csrf_token": t})
    return c


def test_the_po_page_pages_and_searches_on_the_server(app):
    bulk = [{"name": "Bulk Vendor Ltd", "email": "a@bulk.example", "gstin": "36AAACB1234F1Z5", "bank_account": "111122223333", "ifsc": "HDFC0001111"}]
    extra = [{"po_number": f"PO-{3000 + i}", "supplier": "Bulk Vendor Ltd", "ordered": "2024-01-01", "quantity": 5, "price": 10.0, "category": "MRO"} for i in range(150)]
    lp.write(bulk, extra)
    total = db.query("SELECT COUNT(*) n FROM purchase_orders", one=True)["n"]
    c = signed_in(app)
    html = c.get("/pos").text
    assert html.count('class="rowlink"') == 100 and f"of {total} purchase orders" in html
    one = c.get("/pos?q=PO-3149").text
    assert one.count('class="rowlink"') == 1 and "1 purchase order" in one
    assert "Bulk Vendor Ltd" in c.get("/pos?q=BULK vendor").text            # search ignores case
    assert "PO-3149" in c.get("/pos?po=PO-3149").text                        # a PO outside the first page still opens
    assert "No purchase orders match" in c.get("/pos?q=zzzzzz").text


def test_only_admin_routes_can_change_purchase_orders(app):
    """POs are controlled records: the only routes that write them are the admin's create and edit pages
    (tests/test_ai_features.py checks AP users and managers get 403 on both)."""
    writers = sorted(str(r) for r in app.url_map.iter_rules()
                     if "/pos" in str(r) and (r.methods & {"POST", "PUT", "PATCH", "DELETE"}))
    assert writers == ["/pos/<po_number>/edit", "/pos/new"]

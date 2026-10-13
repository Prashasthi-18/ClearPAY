"""New features and the AI-safety rules: the AI investigates, matches and explains; code checks the numbers;
people decide. None of these tests call Groq: the AI is replaced by fakes where needed."""
import email.message
import json
import re
from datetime import date

import pytest

import assistant
import db
import email_inbox
import helpbot
import insights
import investigator
import retrieval
import security
import smart_match
import tools
from conftest import PASSWORD

PO_LINE = {"id": 1, "description": "A4 paper ream", "quantity": 50, "unit_price": 250}


def inv_line(desc, q, p):
    return {"description": desc, "quantity": q, "unit_price": p, "amount": db.money(q * p)}


# ---------------------------------------------------------------- smart matching: code checks the numbers
def test_unit_conversion_is_checked_in_code():
    row, expected = smart_match.check_line(inv_line("Copier paper (box of 10 reams)", 5, 2500), PO_LINE,
                                           {"method": "ai", "factor": 10, "reason": "", "confidence": 0.9})
    assert not row["qty_over"] and not row["price_diff"] and expected == 12500
    row, _ = smart_match.check_line(inv_line("Copier paper (box of 10 reams)", 8, 2500), PO_LINE,
                                    {"method": "ai", "factor": 10})
    assert row["qty_over"] and row["po_units"] == 80
    row, _ = smart_match.check_line(inv_line("Copier paper (box of 10 reams)", 5, 2700), PO_LINE,
                                    {"method": "ai", "factor": 10})
    assert row["price_diff"]
    row, expected = smart_match.check_line(inv_line("Copier paper", 5, 2500), PO_LINE, {"method": "ai", "factor": None})
    assert row["unit_unknown"] and expected == 12500


def test_ai_match_is_never_approved_automatically():
    checks = {"extraction": {"ok": True, "confidence": 0.95, "injection_phrases": [], "hidden_chars": 0},
              "vendor": {"status": "known", "vendor_id": 1}, "duplicate": {},
              "math": {"ok": True, "missing_fields": [], "line_errors": [], "subtotal_ok": True, "total_ok": True,
                       "gst_issues": []},
              "po": {"found": True, "po_number": "PO-1", "vendor_ok": True, "unmatched_lines": [], "variance_pct": 0.0,
                     "expected_total": 100, "invoice_total": 100, "po_total": 100, "committed_before": 0,
                     "recent_totals_on_po": [], "po_tax_rate": 0.18, "invoice_tax_rate": 0.18,
                     "lines": [{"match": "ai", "description": "A4 paper ream", "inv_description": "Copier paper",
                                "factor": 10, "matched": True}]}}
    settings = {"auto_approve_limit": 5000, "variance_tolerance_pct": 5, "min_confidence": 0.7, "split_window_days": 14}
    status, _, issues = tools.decide(checks, settings, {"total": 100, "tax": 18})
    assert status == "Pending" and "ai_match" in {i["code"] for i in issues}


def test_pairing_uses_ai_only_for_differently_worded_lines(monkeypatch):
    sent = []

    def fake_ai(ctx, lines, po_lines, min_conf):
        sent.extend(li["description"] for li in lines)
        return {0: {"po_line": 1, "relation": "same", "factor": 10, "confidence": 0.9, "accepted": True, "reason": "box of 10"}}
    monkeypatch.setattr(tools, "llm_enabled", lambda feature=None: True)
    monkeypatch.setattr(smart_match, "ask_ai", fake_ai)
    po_lines = [{"id": 1, "description": "Stapler", "quantity": 4, "unit_price": 180}, PO_LINE | {"id": 2}]
    pairs, info = smart_match.pair_lines({"settings": {"smart_match_min_confidence": 0.7}},
                                         [inv_line("Stapler", 4, 180), inv_line("Copier paper (box of 10 reams)", 5, 2500)],
                                         po_lines)
    assert sent == ["Copier paper (box of 10 reams)"]           # the exact name never went to the AI
    assert pairs[0][2]["method"] == "rules" and pairs[1][2]["method"] == "ai" and pairs[1][1]["id"] == 2


def test_pairing_falls_back_to_the_name_rule_when_ai_fails(fresh_db, monkeypatch):
    monkeypatch.setattr(tools, "llm_enabled", lambda feature=None: True)
    monkeypatch.setattr(smart_match, "ask_ai", lambda *a: (_ for _ in ()).throw(RuntimeError("Groq down")))
    pairs, info = smart_match.pair_lines({"invoice_id": None, "settings": {"smart_match_min_confidence": 0.7}},
                                         [inv_line("Whiteboard markers (blue)", 30, 45)],
                                         [{"id": 1, "description": "Whiteboard marker", "quantity": 30, "unit_price": 45}])
    assert info["error"] and pairs[0][2]["method"] == "rules"


def test_ai_saying_different_overrides_a_fuzzy_name_match(monkeypatch):
    monkeypatch.setattr(tools, "llm_enabled", lambda feature=None: True)
    monkeypatch.setattr(smart_match, "ask_ai", lambda *a: {0: {"po_line": 0, "relation": "different", "factor": None,
                                                                "confidence": 0.9, "accepted": True,
                                                                "reason": "An eraser is not a marker."}})
    pairs, _ = smart_match.pair_lines({"settings": {"smart_match_min_confidence": 0.7}}, [inv_line("Whiteboard eraser", 30, 45)],
                                      [{"id": 1, "description": "Whiteboard marker", "quantity": 30, "unit_price": 45}])
    assert pairs[0][1] is None and "eraser" in pairs[0][2]["reason"]


# ---------------------------------------------------------------- read-only tools
def test_retrieval_refuses_anything_but_select():
    for sql in ("UPDATE invoices SET status='Approved'", "DELETE FROM ledger", "SELECT 1; DROP TABLE invoices",
                "INSERT INTO ledger VALUES (1)"):
        with pytest.raises(retrieval.ReadOnlyViolation):
            retrieval._select(sql)


def test_ai_tool_lists_contain_no_action_tools():
    for names in ({s["function"]["name"] for s in investigator.TOOL_SCHEMAS}, assistant.READ_ONLY_TOOLS):
        assert names and not any(re.search(r"(^|_)(approve|reject|pay|release|ledger|write|update|edit|delete|send|"
                                           r"create|record)(_|$)", n) for n in names)


def _flagged_invoice(codes=("price_diff", "variance_large")):
    issues = [{"code": c, "severity": "flagged", "title": c, "detail": c, "find": [], "fix_by": "vendor"} for c in codes]
    vid = db.query("SELECT id, name FROM vendors ORDER BY id", one=True)
    return db.execute("""INSERT INTO invoices(file_name, stored_path, file_type, source, status, vendor_name, vendor_id,
                         invoice_number, invoice_date, po_number, total, issues_json, created_at, processed_at)
                         VALUES ('x.txt','-','txt','upload','Flagged',?,?,'TST-1','2026-09-01','PO-2001',1000,?,?,?)""",
                      (vid["name"], vid["id"], json.dumps(issues), db.now_iso(), db.now_iso()))


def test_investigator_never_changes_the_invoice_or_ledger(fresh_db):
    iid = _flagged_invoice()
    inv_id = investigator.investigate(iid)              # offline: rule-based investigation
    r = db.query("SELECT * FROM investigations WHERE id=?", (inv_id,), one=True)
    assert r["mode"] == "rules" and r["recommendation"] in investigator.RECOMMENDATIONS
    assert db.query("SELECT status FROM invoices WHERE id=?", (iid,), one=True)["status"] == "Flagged"
    assert not db.query("SELECT 1 FROM ledger WHERE invoice_id=?", (iid,), one=True)


def test_ai_investigation_is_validated_and_fraud_is_never_recommended_for_approval(fresh_db, monkeypatch):
    iid = _flagged_invoice(("bank_changed",))
    monkeypatch.setattr(tools, "llm_enabled", lambda feature=None: True)

    def fake_loop(ctx, messages, schemas, dispatch, max_turns, max_tokens, on_call):
        on_call("get_invoice", {"invoice_id": iid}, dispatch("get_invoice", {"invoice_id": iid}))
        return json.dumps({"recommendation": "approve", "confidence": 0.9, "summary": "Pay it to 123456789012.",
                           "findings": ["ok"], "vendor_email": {"subject": "x", "body": "y"}})
    monkeypatch.setattr(tools, "tool_loop", fake_loop)
    r = db.query("SELECT * FROM investigations WHERE id=?", (investigator.investigate(iid),), one=True)
    assert r["mode"] == "ai" and r["recommendation"] == "ask_vendor"          # code overrode "approve"
    assert "123456789012" not in r["summary"]                                   # bank numbers masked
    assert r["email_id"] is None                                                # never email a possible impostor
    assert db.query("SELECT status FROM invoices WHERE id=?", (iid,), one=True)["status"] == "Flagged"


def test_ask_clearpay_discards_answers_about_records_it_never_retrieved(fresh_db, monkeypatch):
    monkeypatch.setattr(tools, "llm_enabled", lambda feature=None: True)

    def fake_loop(ctx, messages, schemas, dispatch, max_turns, max_tokens, on_call):
        on_call("search_invoices", {}, dispatch("search_invoices", {"status": "Flagged"}))
        return "ZZZ-9999 was flagged for a duplicate."                         # invented
    monkeypatch.setattr(tools, "tool_loop", fake_loop)
    res = assistant.ask("Which invoices were flagged?")
    assert res["mode"] == "rules" and "ZZZ-9999" not in res["answer"] and "discarded" in res["note"]


def test_ask_clearpay_offline_says_when_information_is_missing(fresh_db):
    assert "couldn't find invoice NOPE-123" in assistant.ask("Why was NOPE-123 flagged?")["answer"]
    assert "can't do that" in assistant.ask("Please approve NOPE-123 for me")["answer"]


# ---------------------------------------------------------------- plain English
def test_plain_english_filters(fresh_db):
    name = db.query("SELECT name FROM vendors ORDER BY id", one=True)["name"]
    f, chips = assistant.parse_query(f"Show flagged invoices over ₹50,000 from {name.split()[0]}")
    assert f["status"] == "Flagged" and f["min_amount"] == 50000 and f["vendor"] == name
    assert assistant.parse_query("overdue invoices")[0] == {"overdue": True}
    assert assistant.parse_query("invoices under 1.5 lakh")[0]["max_amount"] == 150000
    assert assistant.parse_query("show flagged invoices from Zzyzx")[0]["vendor"] == "Zzyzx"
    assert "No vendor matching" in retrieval.search_invoices({"vendor": "Zzyzx"})["message"]
    assert retrieval.clean_filters({"status": "Approved; DROP", "min_amount": "-5", "evil": 1}) == {}


# ---------------------------------------------------------------- deadlines and risk
def test_deadline_labels():
    on = date(2026, 10, 8)
    lab = lambda due, status="Pending", ledger=None: insights.due_info(
        {"status": status, "due_date": due, "invoice_date": None}, ledger, on)["label"]
    assert lab("2026-10-13") == "Due in 5 days"
    assert lab("2026-10-09") == "Due tomorrow"
    assert lab("2026-10-08") == "Due today"
    assert lab("2026-10-05") == "Overdue by 3 days"
    assert lab("2026-10-05", ledger="paid") == "Paid"
    assert lab("2026-10-05", status="Rejected") == "Not payable"


def test_due_date_rules():
    s = {"default_payment_terms_days": 30, "msme_max_days": 45}
    assert insights.due_date_for("2026-09-01", None, None, False, s)[0] == "2026-10-01"
    assert insights.due_date_for("2026-09-01", None, 60, False, s)[0] == "2026-10-31"
    assert insights.due_date_for("2026-09-01", None, 60, True, s)[0] == "2026-10-16"      # MSME: 45 days max
    assert insights.due_date_for("2026-09-01", "2026-09-20", 60, False, s)[0] == "2026-09-20"


def test_vendor_risk_score_is_smoothed_and_explained():
    assert insights._risk_from([])["score"] is None
    one_dup = insights._risk_from([{"status": "Rejected", "decided_by": None,
                                    "issues_json": json.dumps([{"code": "duplicate"}])}])
    assert one_dup["level"] in ("Low", "Medium") and one_dup["factors"][0]["factor"] == "Duplicate invoices"
    many = insights._risk_from([{"status": "Flagged", "decided_by": None,
                                 "issues_json": json.dumps([{"code": "bank_changed"}, {"code": "duplicate"}])}] * 6)
    assert many["level"] == "High"


# ---------------------------------------------------------------- permissions
@pytest.fixture()
def app(fresh_db):
    import app as appmod
    appmod.app.config["TESTING"] = True
    security.reset_rate_limits()
    return appmod.app


def login(app, username):
    c = app.test_client()
    t = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": t})
    return c, re.search(r'data-csrf="([^"]+)"', c.get("/").text).group(1)     # new session, new token


PO_FORM = {"po_number": "PO-7777", "vendor_id": "1", "issue_date": "2026-10-01", "tax_pct": "18",
           "desc_0": "Stapler", "qty_0": "2", "price_0": "180"}


def test_only_admin_can_create_or_edit_purchase_orders(app):
    for user in ("prashasthi", "mokshana"):
        c, t = login(app, user)
        assert c.get("/pos").status_code == 200                              # everyone can view
        assert c.get("/pos/new").status_code == 403
        assert c.post("/pos/new", data={**PO_FORM, "csrf_token": t}).status_code == 403
        assert c.post("/pos/PO-2001/edit", data={**PO_FORM, "csrf_token": t}).status_code == 403
    assert not db.query("SELECT 1 FROM purchase_orders WHERE po_number='PO-7777'", one=True)
    c, t = login(app, "admin")
    assert c.post("/pos/new", data={**PO_FORM, "csrf_token": t}).status_code == 302
    po = db.po_with_lines("PO-7777")
    assert po["total"] == 424.8 and po["lines"][0]["description"] == "Stapler"     # totals worked out in code


def test_only_a_person_records_a_payment(app):
    iid = _flagged_invoice()
    db.execute("UPDATE invoices SET status='Approved' WHERE id=?", (iid,))
    db.execute("""INSERT INTO ledger(invoice_id, amount, status, created_at, updated_at)
                  VALUES (?, 1000, 'ready_to_pay', ?, ?)""", (iid, db.now_iso(), db.now_iso()))
    c, t = login(app, "mokshana")
    assert c.post(f"/invoice/{iid}/record-payment", data={"csrf_token": t}).status_code == 403
    c, t = login(app, "prashasthi")
    assert c.post(f"/invoice/{iid}/record-payment", data={"csrf_token": t, "payment_ref": "UTR1"}).status_code == 302
    led = db.query("SELECT * FROM ledger WHERE invoice_id=?", (iid,), one=True)
    assert led["status"] == "paid" and led["paid_by"]


def test_new_pages_load_for_every_role(app):
    for user in ("prashasthi", "mokshana", "admin"):
        c, _ = login(app, user)
        for page in ("/ask", "/deadlines", "/vendors", "/analytics", "/compare", "/?ask=overdue+invoices"):
            assert c.get(page).status_code == 200, (user, page)


# ---------------------------------------------------------------- email inbox
def _mail(subject, body, attachment=None):
    m = email.message.EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "Vendor <billing@vendor.example>", subject, f"<{subject}@x>"
    m.set_content(body)
    if attachment:
        m.add_attachment(attachment[1], maintype="application", subtype="pdf", filename=attachment[0])
    return m


def test_mailbox_finds_invoices_and_ignores_other_mail():
    assert email_inbox.find_invoices(_mail("Invoice", "See attached", ("inv.pdf", b"%PDF-1.4 x")))[0][0] == "inv.pdf"
    assert email_inbox.find_invoices(_mail("Bill", "Tax invoice INV-1\nTotal: INR 1,180.00"))[0][0].endswith(".txt")
    assert email_inbox.find_invoices(_mail("Lunch?", "Are you free on Friday?")) == []


def test_mailbox_creates_invoices_for_the_normal_checks(fresh_db, monkeypatch):
    queued = []
    monkeypatch.setattr(email_inbox.agent, "submit", queued.append)
    raw = _mail("Invoice 77", "attached", ("inv.pdf", b"%PDF-1.4 test")).as_bytes()
    result, ids = email_inbox.handle_message(raw)
    assert ids == queued and len(ids) == 1
    inv = db.query("SELECT * FROM invoices WHERE id=?", (ids[0],), one=True)
    assert inv["source"] == "email" and inv["status"] == "Processing" and inv["email_from"] == "billing@vendor.example"
    assert email_inbox.handle_message(raw) == ("already read", [])               # never read twice


# ---------------------------------------------------------------- help guide
def test_help_guide_answers_about_the_ui_only():
    assert helpbot.search("Where can I see payment deadlines?")[0][0] == "deadlines"
    assert helpbot.search("What does the risk score mean?")[0][0] == "risk"
    assert helpbot.answer("asdfgh qwerty", use_ai=False)["links"] == []


# ---------------------------------------------------------------- payment file
def test_payment_file_lists_only_ready_to_pay_and_only_for_ap(app):
    iid = _flagged_invoice()
    db.execute("UPDATE invoices SET status='Approved' WHERE id=?", (iid,))
    db.execute("""INSERT INTO ledger(invoice_id, amount, status, created_at, updated_at)
                  VALUES (?, 1000, 'ready_to_pay', ?, ?)""", (iid, db.now_iso(), db.now_iso()))
    other = _flagged_invoice()                                               # still flagged: not in the file
    c, _ = login(app, "mokshana")
    assert c.get("/deadlines/payment-file.csv").status_code == 403
    c, _ = login(app, "prashasthi")
    r = c.get("/deadlines/payment-file.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("invoice_id,") and len(lines) == 2 and lines[1].startswith(f"{iid},")
    assert not any(ln.startswith(f"{other},") for ln in lines)
    assert db.query("SELECT COUNT(*) n FROM audit_log WHERE step='payment_file_export'", one=True)["n"] == 1

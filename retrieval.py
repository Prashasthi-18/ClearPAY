"""
retrieval.py - the READ-ONLY tools the AI features use to look things up.

The Investigator, Ask ClearPay and "Ask in plain English" can reach ClearPay data ONLY through the functions
in this file. Every query goes through _select(), which refuses anything that isn't a single SELECT, so none
of these tools can approve, reject, pay, or change a purchase order, an invoice or a ledger entry, whatever
an AI model asks for.

Results are small dicts with only what is needed: bank accounts are masked, and no passwords, sessions or
user email addresses are ever returned.
"""
import json
import re

import db
import insights
import security
import tools

MAX_ROWS = 25
STATUS_WORDS = ("Approved", "Pending", "Flagged", "Rejected", "Processing")


class ReadOnlyViolation(PermissionError):
    pass


def _select(sql, params=(), one=False):
    """The only door to the database in this module: one SELECT statement, parameterised."""
    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    if head not in ("SELECT", "WITH") or ";" in sql or re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|"
                                                                 r"REPLACE|GRANT|ATTACH|PRAGMA)\b", sql, re.I):
        raise ReadOnlyViolation("Retrieval tools may only read data.")
    return db.query(sql, params, one=one)


def _status_label(s):
    return {"Pending": "Pending approval", "Flagged": "AP review required"}.get(s, s)


def invoice_card(r, ledger_status=None):
    """The short form of an invoice used in every list."""
    due = insights.due_info(r, ledger_status)
    return {"id": r["id"], "invoice_number": r["invoice_number"] or r["file_name"], "vendor": r["vendor_name"],
            "po_number": r["po_number"], "total": r["total"], "total_inr": tools.fmt_inr(r["total"]),
            "status": r["status"], "status_label": _status_label(r["status"]), "invoice_date": r["invoice_date"],
            "due_date": due["due_date"], "deadline": due["label"], "reason": r["decision_reason"],
            "payment": {"ready_to_pay": "Ready for payment", "paid": "Paid", "pending_approval": "Awaiting approval",
                        "cancelled": "Cancelled"}.get(ledger_status, "Not in the ledger"),
            "url": f"/invoice/{r['id']}"}


# ==========================================================================
# Invoices
# ==========================================================================
def find_invoice(ref):
    """An invoice by database id, invoice number (any case) or file name."""
    ref = str(ref or "").strip()[:80]
    if not ref:
        return None
    if ref.isdigit():
        row = _select("SELECT * FROM invoices WHERE id = ?", (int(ref),), one=True)
        if row:
            return row
    row = _select("SELECT * FROM invoices WHERE UPPER(invoice_number) = ? ORDER BY id DESC", (ref.upper(),), one=True)
    return row or _select("SELECT * FROM invoices WHERE file_name = ? ORDER BY id DESC", (ref,), one=True)


def _ledger_status(iid):
    r = _select("SELECT status FROM ledger WHERE invoice_id = ?", (iid,), one=True)
    return r["status"] if r else None


def invoice_details(ref):
    """Everything known about one invoice: why it was flagged, the PO match, the investigation, what people did."""
    inv = find_invoice(ref)
    if not inv:
        return {"found": False, "message": f"No invoice '{str(ref)[:40]}' in ClearPay."}
    out = invoice_card(inv, _ledger_status(inv["id"]))
    out.update(found=True, summary=inv["summary"])
    out["problems"] = [{"code": i["code"], "title": i["title"], "detail": i["detail"]}
                       for i in json.loads(inv["issues_json"] or "[]")]
    cmp = json.loads(inv["comparison_json"] or "{}")
    if cmp:
        out["po_match"] = {
            "po_found": cmp.get("found"), "po_vendor": cmp.get("po_vendor"), "po_total": cmp.get("po_total"),
            "expected_total": cmp.get("expected_total"), "variance_pct": cmp.get("variance_pct"),
            "lines": [{"invoice_item": ln.get("inv_description") or ln.get("description"),
                       "po_item": ln.get("description") if ln.get("matched") else None,
                       "matched_by": ln.get("match") or ("none" if not ln.get("matched") else "rules"),
                       "invoice_qty": ln.get("inv_qty"), "po_qty": ln.get("po_qty"),
                       "units_per_invoice_unit": ln.get("factor"), "invoice_price": ln.get("inv_price"),
                       "po_price": ln.get("po_price"), "ai_reason": ln.get("ai_reason"),
                       "quantity_above_po": ln.get("qty_over"), "price_differs": ln.get("price_diff")}
                      for ln in (cmp.get("lines") or [])[:15]]}
    inv_row = latest_investigation(inv["id"])
    if inv_row:
        out["investigation"] = inv_row
    if inv["decided_by"]:
        who = _select("SELECT full_name FROM users WHERE id = ?", (inv["decided_by"],), one=True)
        out["human_decision"] = {"decision": inv["status"], "by": who["full_name"] if who else "a manager",
                                 "at": inv["decided_at"], "comment": inv["manager_comment"] or None}
    out["history"] = [{"at": r["ts"], "what": r["step"], "detail": (r["reason"] or "")[:160]}
                      for r in _select("""SELECT ts, step, reason FROM audit_log WHERE invoice_id = ? AND step IN
                          ('upload','inbox_pickup','email_pickup','decision','send_to_manager','reprocess',
                           'manager_approve','manager_reject','payment_recorded','investigation','email_sent')
                          ORDER BY id""", (inv["id"],))][-12:]
    if inv["email_from"]:
        out["arrived_by_email"] = {"from": inv["email_from"], "subject": inv["email_subject"]}
    return out


def latest_investigation(invoice_id):
    r = _select("SELECT * FROM investigations WHERE invoice_id = ? ORDER BY id DESC", (invoice_id,), one=True)
    if not r:
        return None
    return {"recommendation": r["recommendation"], "confidence": r["confidence"], "summary": r["summary"],
            "findings": json.loads(r["findings_json"] or "[]"), "mode": r["mode"], "at": r["created_at"],
            "human_decision": r["human_decision"]}


def search_invoices(f, limit=MAX_ROWS):
    """Filtered invoice list. `f` keys (all optional): vendor, status, min_amount, max_amount, overdue,
    due_within_days, invoice_number, po_number, date_from, date_to, payment ('ready_to_pay'|'paid'|'unpaid'),
    issue (an issue code). Only these keys are used; each value is checked."""
    f = clean_filters(f)
    sql = """SELECT i.*, l.status AS ledger_status FROM invoices i LEFT JOIN ledger l ON l.invoice_id = i.id
             WHERE i.status != 'Processing'"""
    params = []
    if f.get("vendor"):
        names = vendor_names_like(f["vendor"])
        if not names:
            return {"count": 0, "invoices": [], "filters": f,
                    "message": f"No vendor matching '{f['vendor']}' in ClearPay."}
        sql += f" AND i.vendor_name IN ({','.join('?' * len(names))})"
        params += names
    if f.get("status"):
        sql += " AND i.status = ?"
        params.append(f["status"])
    if f.get("min_amount") is not None:
        sql += " AND i.total > ?"
        params.append(f["min_amount"])
    if f.get("max_amount") is not None:
        sql += " AND i.total < ?"
        params.append(f["max_amount"])
    if f.get("invoice_number"):
        sql += " AND UPPER(i.invoice_number) LIKE ?"
        params.append(f"%{f['invoice_number'].upper()}%")
    if f.get("po_number"):
        sql += " AND i.po_number = ?"
        params.append(f["po_number"])
    if f.get("date_from"):
        sql += " AND i.invoice_date >= ?"
        params.append(f["date_from"])
    if f.get("date_to"):
        sql += " AND i.invoice_date <= ?"
        params.append(f["date_to"])
    if f.get("issue"):
        sql += " AND i.issues_json LIKE ?"
        params.append(f'%"code": "{f["issue"]}"%')
    if f.get("payment") == "ready_to_pay":
        sql += " AND l.status = 'ready_to_pay'"
    elif f.get("payment") == "paid":
        sql += " AND l.status = 'paid'"
    elif f.get("payment") == "unpaid":
        sql += " AND i.status != 'Rejected' AND (l.status IS NULL OR l.status NOT IN ('paid','cancelled'))"
    rows = _select(sql + " ORDER BY i.id DESC LIMIT 500", params)
    cards = [invoice_card(r, r["ledger_status"]) for r in rows]
    if f.get("overdue") or f.get("due_within_days") is not None:
        keep = []
        for c, r in zip(cards, rows):
            due = insights.due_info(r, r["ledger_status"])
            if f.get("overdue") and due["state"] == "overdue":
                keep.append(c)
            elif f.get("due_within_days") is not None and due["days"] is not None and \
                    0 <= due["days"] <= f["due_within_days"] and due["state"] not in ("paid", "closed"):
                keep.append(c)
        cards = sorted(keep, key=lambda c: c["due_date"] or "")
    return {"count": len(cards), "total_inr": tools.fmt_inr(sum(c["total"] or 0 for c in cards)),
            "invoices": cards[:limit], "filters": f}


FILTER_KEYS = ("vendor", "status", "min_amount", "max_amount", "overdue", "due_within_days", "invoice_number",
               "po_number", "date_from", "date_to", "payment", "issue")


def clean_filters(f):
    """Validate a filter dict (it may come from an AI model): unknown keys dropped, every value type-checked."""
    f = f if isinstance(f, dict) else {}
    out = {}
    v = tools._clean_str(f.get("vendor"), 80)
    if v:
        out["vendor"] = v
    st = str(f.get("status") or "").strip().capitalize()
    st = {"Needs review": "Flagged", "Review": "Flagged", "Awaiting approval": "Pending"}.get(st, st)
    if st in STATUS_WORDS:
        out["status"] = st
    for k in ("min_amount", "max_amount"):
        n = tools._num(f.get(k))
        if n is not None and n >= 0:
            out[k] = n
    if f.get("overdue") in (True, "true", 1, "1"):
        out["overdue"] = True
    n = tools._num(f.get("due_within_days"))
    if n is not None and 0 <= n <= 365:
        out["due_within_days"] = int(n)
    num = tools._clean_str(f.get("invoice_number"), 40)
    if num and re.fullmatch(r"[A-Za-z0-9\-/]{2,40}", num):
        out["invoice_number"] = num
    po = tools.normalize_po(f.get("po_number")) if f.get("po_number") else None
    if po:
        out["po_number"] = po
    for k in ("date_from", "date_to"):
        d = str(f.get(k) or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            out[k] = d
    if f.get("payment") in ("ready_to_pay", "paid", "unpaid"):
        out["payment"] = f["payment"]
    if f.get("issue") in insights.RISK_POINTS or f.get("issue") in ("ai_match", "lookalike_vendor", "unknown_vendor",
                                                                    "low_confidence", "over_limit", "split_invoice"):
        out["issue"] = f["issue"]
    return out


# ==========================================================================
# Vendors, purchase orders, past cases
# ==========================================================================
def vendor_names_like(text):
    """Vendor names matching a typed name: 'alpha' -> ['Alpha Inc']. Uses the vendor list and names on invoices."""
    key = tools.vendor_key(text)
    if not key:
        return []
    names = {r["name"] for r in _select("SELECT name FROM vendors")} | \
        {r["vendor_name"] for r in _select("SELECT DISTINCT vendor_name FROM invoices WHERE vendor_name IS NOT NULL")}
    return sorted(n for n in names if key in tools.vendor_key(n))


def vendor_profile(name):
    names = vendor_names_like(name)
    v = None
    for n in names:
        v = _select("SELECT id, name, msme FROM vendors WHERE name = ?", (n,), one=True)
        if v:
            break
    if not v:
        return {"found": False, "message": f"No vendor matching '{str(name)[:40]}' in the vendor list."}
    rows = _select("""SELECT i.*, l.status AS ledger_status FROM invoices i LEFT JOIN ledger l ON l.invoice_id = i.id
                      WHERE i.vendor_id = ? AND i.status != 'Processing' ORDER BY i.id DESC""", (v["id"],))
    issues = {}
    for r in rows:
        for i in json.loads(r["issues_json"] or "[]"):
            e = issues.setdefault(i["code"], {"problem": i["title"], "times": 0, "invoices": []})
            e["times"] += 1
            if len(e["invoices"]) < 5:
                e["invoices"].append(r["invoice_number"] or r["file_name"])
    risk = insights.vendor_risk(v["id"])
    return {"found": True, "vendor": v["name"], "msme": bool(v["msme"]),
            "risk_score": risk["score"], "risk_level": risk["level"], "risk_factors": risk["factors"][:6],
            "invoices": len(rows), "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_WORDS[:4]},
            "approved_spend_inr": tools.fmt_inr(sum(r["total"] or 0 for r in rows
                                                     if r["ledger_status"] in ("ready_to_pay", "paid"))),
            "previous_issues": sorted(issues.values(), key=lambda e: -e["times"])[:10],
            "human_decisions": [{"invoice": r["invoice_number"], "decision": r["status"], "comment": r["manager_comment"],
                                 "at": r["decided_at"]} for r in rows if r["decided_by"]][:8],
            "recent_invoices": [invoice_card(r, r["ledger_status"]) for r in rows[:8]],
            "url": "/vendors?v=" + str(v["id"])}


def purchase_order(po_number):
    po_number = tools.normalize_po(po_number)
    po = db.po_with_lines(po_number) if po_number else None       # po_with_lines only SELECTs
    if not po:
        return {"found": False, "message": f"No purchase order '{po_number}' in ClearPay."}
    invs = _select("""SELECT i.*, l.status AS ledger_status FROM invoices i LEFT JOIN ledger l ON l.invoice_id = i.id
                      WHERE i.po_number = ? ORDER BY i.id""", (po_number,))
    billed = sum(r["total"] or 0 for r in invs if r["ledger_status"] in ("ready_to_pay", "paid"))
    return {"found": True, "po_number": po["po_number"], "vendor": po["vendor_name"], "issue_date": po["issue_date"],
            "status": po["status"], "total": po["total"], "tax_rate": po["tax_rate"],
            "lines": [{"item": ln["description"], "qty": ln["quantity"], "unit_price": ln["unit_price"]}
                      for ln in po["lines"]],
            "approved_so_far_inr": tools.fmt_inr(billed), "remaining_inr": tools.fmt_inr(po["total"] - billed),
            "invoices": [invoice_card(r, r["ledger_status"]) for r in invs[:10]], "url": f"/pos?po={po_number}"}


def similar_cases(invoice_id=None, codes=None, vendor=None, limit=8):
    """Past invoices that a PERSON decided, sharing a problem with this one (same vendor first), with the
    decision and the person's comment. This is how the Investigator learns from earlier decisions."""
    if invoice_id:
        inv = _select("SELECT * FROM invoices WHERE id = ?", (int(invoice_id),), one=True)
        if not inv:
            return {"found": False, "message": "No such invoice."}
        codes = codes or [i["code"] for i in json.loads(inv["issues_json"] or "[]")]
        vendor = vendor or inv["vendor_name"]
    codes = {c for c in (codes or []) if isinstance(c, str)}
    rows = _select("""SELECT i.*, u.full_name AS decider FROM invoices i LEFT JOIN users u ON u.id = i.decided_by
                      WHERE i.decided_by IS NOT NULL AND i.id != ? ORDER BY i.decided_at DESC LIMIT 300""",
                   (int(invoice_id or 0),))
    cases = []
    for r in rows:
        rc = {i["code"] for i in json.loads(r["issues_json"] or "[]")}
        shared = sorted(rc & codes)
        same_vendor = bool(vendor) and tools.vendor_key(r["vendor_name"]) == tools.vendor_key(vendor)
        if not shared and not same_vendor:
            continue
        lines = (json.loads(r["comparison_json"] or "{}").get("lines") or [])[:3]
        cases.append({"invoice": r["invoice_number"] or r["file_name"], "vendor": r["vendor_name"],
                      "same_vendor": same_vendor, "shared_problems": shared,
                      "decision": r["status"], "decided_by": r["decider"], "comment": r["manager_comment"] or None,
                      "decided_at": r["decided_at"], "total": r["total"],
                      "items": [(ln.get("inv_description") or ln.get("description") or "") +
                                (f": {ln['inv_qty']} × {ln.get('inv_price')} (PO {ln.get('po_qty')} × {ln.get('po_price')})"
                                 if ln.get("inv_price") is not None else "") for ln in lines],
                      "_rank": (same_vendor and bool(shared), len(shared), same_vendor)})
    cases.sort(key=lambda c: c.pop("_rank"), reverse=True)
    return {"found": bool(cases), "cases": cases[:limit],
            "message": None if cases else "No earlier invoices with these problems were decided by a person."}


def deadlines_summary():
    rows = insights.open_deadlines()
    pick = lambda st: [invoice_card(r, r["ledger_status"]) for r in rows if r["due"]["state"] == st][:10]
    return {"overdue": pick("overdue"), "due_within_7_days": pick("due_soon"),
            "overdue_count": sum(1 for r in rows if r["due"]["state"] == "overdue")}


def analytics_summary():
    a = insights.analytics()
    return {"approved_spend_inr": tools.fmt_inr(a["spend_total"]), "on_hold_inr": tools.fmt_inr(a["on_hold"]),
            "fraud_caught": a["fraud_count"], "fraud_amount_inr": tools.fmt_inr(a["fraud_amount"]),
            "fraud_by_type": a["fraud_by_type"], "money_saved_inr": tools.fmt_inr(a["saved"]["total"]),
            "saved_breakdown_inr": {k: tools.fmt_inr(v) for k, v in a["saved"].items()
                                    if k in ("duplicates", "fraud", "overcharges", "staff_time")},
            "spend_by_vendor": [{"vendor": x["label"], "inr": tools.fmt_inr(x["amount"])} for x in a["spend_by_vendor"]],
            "overdue_invoices": a["overdue"], "invoices": a["invoices"]}


def mask(text):
    """Defence in depth: never let a full bank account number out in an answer."""
    return re.sub(r"\b\d{9,18}\b", lambda m: security.mask_account(m.group(0)), text or "")

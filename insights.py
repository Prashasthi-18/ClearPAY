"""
insights.py - numbers worked out in code from the invoice history (no AI anywhere in this file).

  * payment deadlines: due date, days left, "Due tomorrow" / "Overdue by 3 days"
  * vendor risk score: an informational 0-100 indicator built from each vendor's past problems
  * analytics: spend, fraud caught, money saved

None of these ever changes an invoice, a ledger entry or a payment. They only help people decide.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

import db

IST = timezone(timedelta(hours=5, minutes=30))


def today():
    """Deadlines are counted in India time, so 'due today' means today in IST."""
    return datetime.now(IST).date()


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ==========================================================================
# Payment deadlines
# ==========================================================================
def due_date_for(invoice_date, stated_due=None, terms_days=None, msme=False, settings=None):
    """(due date ISO, terms in days, note). The printed due date wins, then the printed terms, then the default.
    For an MSME supplier the due date is never later than msme_max_days after the invoice date."""
    s = settings or db.get_settings()
    inv_d = _date(invoice_date)
    if inv_d is None:
        return None, None, "No invoice date, so no due date"
    due, note = _date(stated_due), "Due date printed on the invoice"
    if due is None or due < inv_d:
        terms = terms_days if terms_days is not None else s["default_payment_terms_days"]
        due = inv_d + timedelta(days=terms)
        note = (f"{terms}-day terms from the invoice" if terms_days is not None
                else f"Default {terms}-day terms (none printed on the invoice)")
    days = (due - inv_d).days
    if msme and days > s["msme_max_days"]:
        due = inv_d + timedelta(days=s["msme_max_days"])
        days = s["msme_max_days"]
        note += f"; capped at {days} days because the vendor is an MSME"
    return due.isoformat(), days, note


def due_info(inv, ledger_status=None, on=None):
    """How urgent the payment is. `inv` needs status, due_date (or invoice_date). Never triggers anything."""
    on = on or today()
    if inv["status"] == "Rejected" or ledger_status == "cancelled":
        return {"state": "closed", "label": "Not payable", "days": None, "due_date": inv["due_date"]}
    if ledger_status == "paid":
        return {"state": "paid", "label": "Paid", "days": None, "due_date": inv["due_date"]}
    due = _date(inv["due_date"])
    if due is None:
        d = _date(inv["invoice_date"])
        due = d + timedelta(days=db.get_settings()["default_payment_terms_days"]) if d else None
    if due is None:
        return {"state": "unknown", "label": "No due date", "days": None, "due_date": None}
    days = (due - on).days
    if days < 0:
        label, state = f"Overdue by {-days} day{'s' if days != -1 else ''}", "overdue"
    elif days == 0:
        label, state = "Due today", "due_soon"
    elif days == 1:
        label, state = "Due tomorrow", "due_soon"
    else:
        label, state = f"Due in {days} days", "due_soon" if days <= 7 else "ok"
    return {"state": state, "label": label, "days": days, "due_date": due.isoformat()}


def open_deadlines(on=None):
    """Every invoice still to be paid, most urgent first."""
    rows = db.query("""SELECT i.*, l.status AS ledger_status, v.msme AS vendor_msme FROM invoices i
                       LEFT JOIN ledger l ON l.invoice_id = i.id LEFT JOIN vendors v ON v.id = i.vendor_id
                       WHERE i.status IN ('Approved','Pending','Flagged')
                         AND (l.status IS NULL OR l.status NOT IN ('paid','cancelled'))""")
    out = []
    for r in rows:
        d = dict(r)
        d["due"] = due_info(d, d["ledger_status"], on)
        out.append(d)
    order = {"overdue": 0, "due_soon": 1, "ok": 2, "unknown": 3}
    out.sort(key=lambda d: (order.get(d["due"]["state"], 4), d["due"]["days"] if d["due"]["days"] is not None else 9999))
    return out


# ==========================================================================
# Vendor risk score (informational only)
# ==========================================================================
RISK_POINTS = {  # issue code -> (points, plain-English factor)
    "duplicate": (15, "Duplicate invoices"), "near_duplicate": (12, "Re-sent invoices"),
    "bank_changed": (20, "Bank details changed on an invoice"), "gstin_mismatch": (15, "GSTIN didn't match"),
    "prompt_injection": (25, "Manipulation attempts in documents"), "over_billed": (10, "Billed beyond the PO"),
    "po_vendor": (8, "Used another vendor's PO"), "line_not_on_po": (6, "Items that weren't ordered"),
    "variance_large": (6, "Large differences from the PO"), "qty_over": (4, "Quantities above the PO"),
    "price_diff": (4, "Prices different from the PO"), "tax_rate": (5, "Wrong tax rate"),
    "line_math": (4, "Arithmetic errors"), "subtotal_math": (4, "Arithmetic errors"),
    "total_math": (4, "Arithmetic errors"), "gst": (4, "GST errors"), "missing_po": (3, "Missing PO numbers"),
    "po_not_found": (3, "Unknown PO numbers"), "missing_fields": (3, "Missing invoice details"),
    "unreadable": (1, "Unreadable files"),
}
REJECTED_BY_PERSON = 8
PRIOR_CLEAN_INVOICES = 3          # smoothing: a single bad invoice shouldn't make a vendor look like a fraudster


def _risk_from(invoices):
    if not invoices:
        return {"score": None, "level": "No history", "factors": [], "invoices": 0}
    factors = {}
    for inv in invoices:
        codes = {i["code"] for i in json.loads(inv["issues_json"] or "[]")}
        for code in codes:
            if code in RISK_POINTS:
                pts, label = RISK_POINTS[code]
                f = factors.setdefault(label, {"factor": label, "count": 0, "points": 0})
                f["count"] += 1
                f["points"] += pts
        if inv["status"] == "Rejected" and inv["decided_by"]:
            f = factors.setdefault("Rejected by a person", {"factor": "Rejected by a person", "count": 0, "points": 0})
            f["count"] += 1
            f["points"] += REJECTED_BY_PERSON
    total = sum(f["points"] for f in factors.values())
    per_invoice = total / (len(invoices) + PRIOR_CLEAN_INVOICES)
    score = round(100 * (1 - math.exp(-per_invoice / 10)))
    level = "High" if score >= 50 else "Medium" if score >= 25 else "Low"
    return {"score": score, "level": level, "invoices": len(invoices),
            "factors": sorted(factors.values(), key=lambda f: -f["points"])}


def vendor_risk(vendor_id):
    rows = db.query("SELECT status, decided_by, issues_json FROM invoices WHERE vendor_id = ? AND status != 'Processing'",
                    (vendor_id,))
    return _risk_from(rows)


def vendor_overview():
    """One row per vendor: risk, volume, spend, flagged share."""
    out = []
    for v in db.query("SELECT id, name, email, msme FROM vendors ORDER BY name"):
        rows = db.query("""SELECT i.status, i.decided_by, i.issues_json, i.total, l.status AS ledger_status
                           FROM invoices i LEFT JOIN ledger l ON l.invoice_id = i.id
                           WHERE i.vendor_id = ? AND i.status != 'Processing'""", (v["id"],))
        risk = _risk_from(rows)
        out.append({**dict(v), "risk": risk, "invoices": len(rows),
                    "flagged": sum(1 for r in rows if r["status"] in ("Flagged", "Rejected")),
                    "spend": sum(r["total"] or 0 for r in rows if r["ledger_status"] in ("ready_to_pay", "paid"))})
    out.sort(key=lambda r: -(r["risk"]["score"] if r["risk"]["score"] is not None else -1))
    return out


# ==========================================================================
# Analytics: spend, fraud caught, money saved
# ==========================================================================
FRAUD_CODES = {"duplicate": "Duplicate invoice", "near_duplicate": "Re-sent invoice",
               "prompt_injection": "Manipulated document", "lookalike_vendor": "Look-alike vendor",
               "unknown_vendor": "Unknown vendor", "bank_changed": "Bank details changed",
               "gstin_mismatch": "GSTIN mismatch", "over_billed": "Billed beyond the PO", "po_vendor": "Another vendor's PO"}
OVERCHARGE_CODES = {"variance_large", "qty_over", "price_diff", "line_not_on_po", "tax_rate", "line_math",
                    "subtotal_math", "total_math"}


def analytics(months=6):
    s = db.get_settings()
    rows = [dict(r) for r in db.query("""SELECT i.*, l.status AS ledger_status FROM invoices i
                                         LEFT JOIN ledger l ON l.invoice_id = i.id WHERE i.status != 'Processing'""")]
    for r in rows:
        r["codes"] = {i["code"] for i in json.loads(r["issues_json"] or "[]")}
        r["cmp"] = json.loads(r["comparison_json"] or "{}")
    approved = [r for r in rows if r["ledger_status"] in ("ready_to_pay", "paid")]
    not_paid_out = [r for r in rows if r["ledger_status"] not in ("ready_to_pay", "paid")]   # stopped or still on hold

    # Spend
    by_vendor, by_month = {}, {}
    for r in approved:
        by_vendor[r["vendor_name"] or "Unknown"] = by_vendor.get(r["vendor_name"] or "Unknown", 0) + (r["total"] or 0)
        m = (r["invoice_date"] or "")[:7]
        if len(m) == 7:
            by_month[m] = by_month.get(m, 0) + (r["total"] or 0)
    month_keys = sorted(by_month)[-months:]

    # Fraud caught: invoices with a fraud signal that were NOT approved for payment
    fraud = [r for r in not_paid_out if r["codes"] & set(FRAUD_CODES)]
    fraud_by_type = {}
    for r in fraud:
        for c in r["codes"] & set(FRAUD_CODES):
            t = fraud_by_type.setdefault(FRAUD_CODES[c], {"type": FRAUD_CODES[c], "count": 0, "amount": 0})
            t["count"] += 1
            t["amount"] += r["total"] or 0

    # Money saved (each invoice counted once, in the first category that applies)
    dup_amount = sum(r["total"] or 0 for r in fraud if r["codes"] & {"duplicate", "near_duplicate"})
    fraud_amount = sum(r["total"] or 0 for r in fraud if not r["codes"] & {"duplicate", "near_duplicate"})
    overcharge = 0.0
    fraud_ids = {r["id"] for r in fraud}
    for r in rows:
        if r["id"] in fraud_ids or not r["codes"] & OVERCHARGE_CODES:
            continue
        if r["ledger_status"] in ("ready_to_pay", "paid") and r["decided_by"] is None:
            continue
        inv_total, expected = r["cmp"].get("invoice_total"), r["cmp"].get("expected_total")
        if inv_total and expected and inv_total > expected and r["status"] in ("Rejected", "Flagged"):
            overcharge += inv_total - expected
    hands_free = sum(1 for r in rows if r["status"] in ("Approved", "Rejected") and r["decided_by"] is None)
    time_saved = hands_free * s["cost_saved_per_invoice_inr"]

    # AI's part, and how often people agreed with it
    inv_rows = db.query("SELECT recommendation, human_decision, mode, prompt_tokens, completion_tokens FROM investigations")
    decided = [r for r in inv_rows if r["human_decision"]]
    agree = sum(1 for r in decided if _agrees(r["recommendation"], r["human_decision"]))
    ai_lines = sum(len([ln for ln in (r["cmp"].get("lines") or []) if ln.get("match") == "ai"]) for r in rows)
    tokens = sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in rows) + \
        sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in inv_rows)
    cost = (sum(r["prompt_tokens"] or 0 for r in rows) + sum(r["prompt_tokens"] or 0 for r in inv_rows)) / 1e6 * \
        s["groq_input_usd_per_m"] + (sum(r["completion_tokens"] or 0 for r in rows) +
                                     sum(r["completion_tokens"] or 0 for r in inv_rows)) / 1e6 * s["groq_output_usd_per_m"]
    deadlines = open_deadlines()
    return {
        "spend_total": sum(r["total"] or 0 for r in approved), "spend_count": len(approved),
        "paid_total": sum(r["total"] or 0 for r in approved if r["ledger_status"] == "paid"),
        "on_hold": sum(r["total"] or 0 for r in rows if r["status"] in ("Flagged", "Pending")),
        "spend_by_vendor": sorted(({"label": k, "amount": v} for k, v in by_vendor.items()), key=lambda x: -x["amount"])[:8],
        "spend_by_month": [{"label": k, "amount": by_month[k]} for k in month_keys],
        "fraud_count": len(fraud), "fraud_amount": sum(r["total"] or 0 for r in fraud),
        "fraud_by_type": sorted(fraud_by_type.values(), key=lambda t: -t["count"]),
        "saved": {"duplicates": dup_amount, "fraud": fraud_amount, "overcharges": overcharge, "staff_time": time_saved,
                  "total": dup_amount + fraud_amount + overcharge + time_saved, "hands_free": hands_free,
                  "per_invoice": s["cost_saved_per_invoice_inr"]},
        "invoices": len(rows),
        "overdue": sum(1 for d in deadlines if d["due"]["state"] == "overdue"),
        "overdue_amount": sum(d["total"] or 0 for d in deadlines if d["due"]["state"] == "overdue"),
        "due_soon": sum(1 for d in deadlines if d["due"]["state"] == "due_soon"),
        "ai": {"investigations": len(inv_rows), "ai_investigations": sum(1 for r in inv_rows if r["mode"] == "ai"),
               "decided": len(decided), "agreed": agree,
               "agreement_pct": round(100 * agree / len(decided)) if decided else None,
               "ai_matched_lines": ai_lines, "tokens": tokens, "cost_usd": round(cost, 4)},
    }


def _agrees(recommendation, human):
    """Did the person do what the Investigator recommended? 'Send to manager' counts as agreeing with 'approve'."""
    return human in {"approve": ("approved", "sent_to_manager"), "reject": ("rejected",),
                     "ask_vendor": ("asked_vendor",)}.get(recommendation, ())

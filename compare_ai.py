"""
compare_ai.py - proof: 20 tricky invoices, handled by rules alone vs rules + AI.

  python compare_ai.py          # needs GROQ_API_KEY; writes data/compare_results.json (shown on "Rules vs AI")

Both arms run on their own throwaway database with the same purchase orders, the same past decisions and the
same invoice files, and both read the invoices with the same rule-based parser. So the ONLY difference is
the AI's part: smart matching (different wording, units, partial deliveries) and the Investigator
(context from vendor history and earlier human decisions).

  * Rules alone   = ClearPay's code with the AI switched off (plain name matching, rule-based investigation).
  * Rules + AI    = the same code, plus AI smart matching and the AI Investigator.

Each case has an expected outcome, written down BEFORE running (what a careful AP clerk would conclude):
  - matching cases: the routing (Approved / Pending for a person to confirm / Flagged), and for some the
    reason that must be found (e.g. "quantity above PO" rather than "item not ordered");
  - history cases: the Investigator's recommendation, judged against the managers' earlier comments.
The AI arm costs roughly 40-60k Groq tokens; on the free plan expect it to take several minutes.
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

import agent
import db
import investigator
import tools

RESULTS = os.path.join(db.DATA_DIR, "compare_results.json")

# Purchase-order templates used only by this comparison: name, vendor index, [(description, qty, unit price)].
# Every case gets its OWN copy (PO-9201, PO-9202, ...) so one case's invoice never uses up another case's PO.
POS = [
    ("PO-9101", 0, [("A4 paper ream", 50, 250)]),
    ("PO-9102", 1, [("HDMI cable 2m", 20, 350), ("USB keyboard", 10, 650)]),
    ("PO-9103", 4, [("Hand sanitiser 500ml bottle", 48, 120)]),
    ("PO-9104", 2, [("Courier pickup", 10, 250), ("Packaging box (medium)", 100, 40)]),
    ("PO-9105", 3, [("Cement OPC 53 grade, 50kg bag", 40, 410)]),
    ("PO-9106", 0, [("Laptop docking station", 4, 5500)]),
    ("PO-9107", 0, [("Whiteboard marker", 30, 45)]),
    ("PO-9108", 1, [("HDMI cable 2m", 20, 350)]),
    ("PO-9109", 4, [("Floor cleaner 5L can", 10, 600)]),
    ("PO-9110", 2, [("Corrugated box", 200, 30)]),
    ("PO-9111", 3, [("Safety helmet", 40, 300)]),
    ("PO-9112", 0, [("Toner cartridge", 40, 900)]),
]

# Earlier invoices that people already decided (for the history cases): vendor index, number, status,
# issue codes, comment, items
HISTORY = [
    (1, "H-1601", "Approved", ["price_diff", "variance_large"],
     "Price revision to ₹370 per HDMI cable agreed by procurement from Sep 2026 (email 12-Sep). OK to pay.",
     "HDMI cable 2m: 20 × 370 (PO 20 × 350)"),
    (1, "H-1602", "Approved", ["price_diff", "variance_large"],
     "As agreed, HDMI cables are ₹370 from September. Approved.", "HDMI cable 2m: 10 × 370 (PO 10 × 350)"),
    (4, "H-1701", "Approved", ["price_diff", "variance_large"],
     "One-time exception for an urgent order. Do NOT accept this price again; ask the vendor to bill at the PO price.",
     "Floor cleaner 5L can: 10 × 650 (PO 10 × 600)"),
    (2, "H-1801", "Rejected", ["qty_over", "variance_large"],
     "Vendor billed boxes that were never delivered (GRN shows the PO quantity). Rejected; vendor must reissue for "
     "the delivered quantity only.", "Corrugated box: 240 × 30 (PO 200 × 30)"),
    (2, "H-1802", "Rejected", ["qty_over", "variance_large"],
     "Again more boxes billed than delivered. Rejected.", "Corrugated box: 230 × 30 (PO 200 × 30)"),
    (3, "H-1901", "Approved", ["price_diff"], "Fine, small rounding difference.", "Safety helmet: 40 × 301 (PO 40 × 300)"),
    (0, "H-2001", "Approved", ["price_diff", "variance_large"],
     "Freight surcharge of ₹60 per cartridge agreed for small orders under 10 units only.",
     "Toner cartridge: 6 × 960 (PO 6 × 900)"),
]

# The 20 cases. kind: "match" (judge routing) or "history" (judge the Investigator's recommendation).
CASES = [
    {"n": 1, "kind": "match", "category": "Different wording + units", "po": "PO-9101",
     "lines": [("Copier paper (box of 10 reams)", 5, 2500)], "expect_status": "Pending",
     "why": "5 boxes of 10 reams = the 50 reams ordered, at ₹250 a ream. A person confirms the match."},
    {"n": 2, "kind": "match", "category": "Units hide over-billing", "po": "PO-9101",
     "lines": [("Copier paper (box of 10 reams)", 8, 2500)], "expect_status": "Flagged", "expect_issue": "qty_over",
     "why": "8 boxes = 80 reams, but only 50 were ordered. The real problem is over-billing, not an unknown item."},
    {"n": 3, "kind": "match", "category": "Partial delivery", "po": "PO-9101",
     "lines": [("Copier paper (box of 10 reams)", 3, 2500)], "expect_status": "Pending",
     "why": "30 of 50 reams delivered and billed at the PO price: a valid partial invoice."},
    {"n": 4, "kind": "match", "category": "Similar product name", "po": "PO-9101",
     "lines": [("Printer paper A4 80gsm (ream)", 50, 250)], "expect_status": "Pending",
     "why": "Same item, the vendor's own name for it."},
    {"n": 5, "kind": "match", "category": "Different wording", "po": "PO-9102",
     "lines": [("High-speed HDMI lead (2 metre)", 20, 350), ("Keyboard, USB wired", 10, 650)],
     "expect_status": "Pending", "why": "Both lines are the ordered items in other words."},
    {"n": 6, "kind": "match", "category": "Item really not ordered", "po": "PO-9102",
     "lines": [("HDMI cable 2m", 20, 350), ("Wireless mouse", 10, 650)], "expect_status": "Flagged",
     "expect_issue": "line_not_on_po", "why": "A mouse is not a keyboard: the AI must not over-match."},
    {"n": 7, "kind": "match", "category": "Different units (dozen)", "po": "PO-9103",
     "lines": [("Sanitizer 500 ml, carton of 12", 4, 1440)], "expect_status": "Pending",
     "why": "4 cartons of 12 = 48 bottles at ₹120 each."},
    {"n": 8, "kind": "match", "category": "Look-alike name, different product", "po": "PO-9107",
     "lines": [("Whiteboard eraser", 30, 45)], "expect_status": "Flagged", "expect_issue": "line_not_on_po",
     "why": "An eraser was never ordered. Plain name matching is fooled by the similar words."},
    {"n": 9, "kind": "match", "category": "Different wording", "po": "PO-9104",
     "lines": [("Parcel collection service", 10, 250), ("Corrugated carton, medium", 100, 40)],
     "expect_status": "Pending", "why": "Same services and goods, described differently."},
    {"n": 10, "kind": "match", "category": "Control: exact names, partial", "po": "PO-9104",
     "lines": [("Courier pickup", 6, 250), ("Packaging box (medium)", 100, 40)], "expect_status": "Approved",
     "why": "Exact names: code handles it alone and no AI call is made."},
    {"n": 11, "kind": "match", "category": "Different units (tonnes vs bags)", "po": "PO-9105",
     "lines": [("Cement 53 grade (per tonne)", 2, 8200)], "expect_status": "Pending",
     "why": "2 tonnes = 40 bags of 50 kg at ₹410 a bag."},
    {"n": 12, "kind": "match", "category": "Units hide a price rise", "po": "PO-9105",
     "lines": [("Cement 53 grade (per tonne)", 2, 8900)], "expect_status": "Flagged", "expect_issue": "price_diff",
     "why": "₹8,900 a tonne = ₹445 a bag, 8.5% above the PO."},
    {"n": 13, "kind": "match", "category": "Different wording", "po": "PO-9106",
     "lines": [("USB-C dock for laptops", 4, 5500)], "expect_status": "Pending", "why": "A dock is the docking station."},
    {"n": 14, "kind": "match", "category": "Look-alike name, different product", "po": "PO-9106",
     "lines": [("Laptop stand (aluminium)", 4, 5500)], "expect_status": "Flagged", "expect_issue": "line_not_on_po",
     "why": "A stand is not a docking station."},
    {"n": 15, "kind": "match", "category": "Pack size", "po": "PO-9107",
     "lines": [("Whiteboard markers, pack of 10", 3, 450)], "expect_status": "Pending",
     "why": "3 packs of 10 = the 30 markers ordered at ₹45 each."},
    {"n": 16, "kind": "history", "category": "Context from past decisions", "po": "PO-9108",
     "lines": [("HDMI cable 2m", 20, 370)], "expect_rec": ["approve"],
     "why": "Managers approved ₹370 twice, citing a price revision agreed by procurement."},
    {"n": 17, "kind": "history", "category": "Context from past decisions", "po": "PO-9109",
     "lines": [("Floor cleaner 5L can", 10, 650)], "expect_rec": ["ask_vendor"],
     "why": "The earlier approval was a one-time exception that said: don't accept this price again."},
    {"n": 18, "kind": "history", "category": "Vendor history", "po": "PO-9110",
     "lines": [("Corrugated box", 240, 30)], "expect_rec": ["reject", "ask_vendor"],
     "why": "This vendor has twice billed boxes that were never delivered."},
    {"n": 19, "kind": "history", "category": "Fraud sign + history", "po": "PO-9111",
     "lines": [("Safety helmet", 40, 300)], "bank": "999911112222", "expect_rec": ["ask_vendor", "reject"],
     "why": "Bank account changed: must be confirmed by phone; an earlier unrelated approval is no reason to pay."},
    {"n": 20, "kind": "history", "category": "Context from past decisions", "po": "PO-9112",
     "lines": [("Toner cartridge", 40, 960)], "expect_rec": ["ask_vendor"],
     "why": "The surcharge was agreed for orders under 10 units only; this order is 40."},
]


def _setup(path):
    """Fresh throwaway database with the normal seed, the comparison POs and the earlier human decisions."""
    with db.using(path):
        db.reset_db()
        db.seed()
        vids = [r["id"] for r in db.query("SELECT id FROM vendors ORDER BY id")]
        for case in CASES:
            _, v, lines = next(p for p in POS if p[0] == case["po"])
            po_number = _po_for(case)
            sub = db.money(sum(q * p for _, q, p in lines))
            tax = db.money(sub * 0.18)
            pid = db.execute("""INSERT INTO purchase_orders(po_number, vendor_id, issue_date, subtotal, tax_rate, tax,
                                total) VALUES (?,?,?,?,?,?,?)""", (po_number, vids[v], "2026-08-20", sub, 0.18, tax,
                                                                 db.money(sub + tax)))
            for d, q, p in lines:
                db.execute("INSERT INTO po_lines(po_id, description, quantity, unit_price) VALUES (?,?,?,?)", (pid, d, q, p))
        mgr = db.query("SELECT id FROM users WHERE role='manager'", one=True)["id"]
        for k, (v, num, status, codes, comment, item) in enumerate(HISTORY):
            name = db.VENDORS[v][0]
            issues = [{"code": c, "severity": "flagged", "title": c.replace("_", " ").capitalize(), "detail": item,
                       "find": [], "fix_by": "vendor"} for c in codes]
            db.execute("""INSERT INTO invoices(file_name, stored_path, file_type, source, status, vendor_name, vendor_id,
                          invoice_number, invoice_date, po_number, total, issues_json, comparison_json, decision_reason,
                          created_at, processed_at, decided_by, decided_at, manager_comment)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (f"{num}.txt", "-", "txt", "eval", status, name, vids[v], num, f"2026-08-{10 + k:02d}",
                        f"PO-8{100 + k}", 0, json.dumps(issues),
                        json.dumps({"lines": [{"description": item, "inv_description": item, "matched": True}]}),
                        "Earlier invoice", db.now_iso(), db.now_iso(), mgr, f"2026-08-{12 + k:02d}T10:00:00+00:00",
                        comment))


def _po_for(case):
    return f"PO-{9200 + case['n']}"


def _write_invoice(folder, case):
    from generate_data import finish, write_txt
    po = next(p for p in POS if p[0] == case["po"])
    v = db.VENDORS[po[1]]
    inv = finish({"vendor": v[0], "gstin": v[2], "bank": case.get("bank", v[3]), "ifsc": v[4],
                  "invoice_number": f"CMP-{1000 + case['n']}", "date": "2026-09-20", "po": _po_for(case),
                  "lines": [{"description": d, "quantity": q, "unit_price": p} for d, q, p in case["lines"]]})
    name = f"C{case['n']:02d}.txt"
    write_txt(os.path.join(folder, name), inv)
    return name


def _run_arm(arm, progress=None, offset=0, total=0):
    path = os.path.join(db.DATA_DIR, f"compare_{arm}.db")
    work = os.path.join(db.DATA_DIR, f"compare_files_{arm}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    _setup(path)
    rows = []
    off = ("all",) if arm == "rules" else ("extract",)
    with db.using(path), tools.ai_disabled(*off):
        for i, case in enumerate(CASES, 1):
            name = _write_invoice(work, case)
            iid = agent.create_invoice(os.path.join(work, name), name, "txt", "eval")
            t0 = time.time()
            status = agent.process_invoice(iid, mode="offline")
            inv = db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)
            codes = [x["code"] for x in json.loads(inv["issues_json"] or "[]")]
            row = {"n": case["n"], "status": status, "issues": codes, "reason": inv["decision_reason"],
                   "tokens": (inv["prompt_tokens"] or 0) + (inv["completion_tokens"] or 0)}
            cmp = json.loads(inv["comparison_json"] or "{}")
            row["ai_matches"] = [f"“{ln['inv_description']}” = “{ln['description']}”" +
                                 (f" (×{ln['factor']:g})" if ln.get("factor") not in (None, 1) else "")
                                 for ln in cmp.get("lines") or [] if ln.get("match") == "ai"]
            if case["kind"] == "history":
                inv_id = investigator.investigate(iid, requested_by="compare", force_rules=(arm == "rules"))
                r = db.query("SELECT * FROM investigations WHERE id=?", (inv_id,), one=True) if inv_id else None
                row["recommendation"] = r["recommendation"] if r else None
                row["rec_summary"] = r["summary"] if r else None
                row["rec_mode"] = r["mode"] if r else None
                row["tokens"] += ((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)) if r else 0
            row["ms"] = int((time.time() - t0) * 1000)
            row.update(_score(case, row))
            rows.append(row)
            if progress:
                progress(offset + i, total)
    return rows


def _score(case, row):
    if case["kind"] == "history":
        ok = row.get("recommendation") in case["expect_rec"]
        return {"correct": ok, "verdict": "right recommendation" if ok else "wrong recommendation"}
    status_ok = row["status"] == case["expect_status"]
    issue_ok = not case.get("expect_issue") or case["expect_issue"] in row["issues"]
    if status_ok and issue_ok:
        verdict = "correct"
    elif row["status"] == "Approved" and case["expect_status"] != "Approved":
        verdict = "risky: approved a bad invoice"
    elif row["status"] == "Flagged" and case["expect_status"] in ("Pending", "Approved"):
        verdict = "false alarm"
    elif status_ok:
        verdict = "right route, wrong reason"
    else:
        verdict = "wrong route"
    return {"correct": status_ok and issue_ok, "verdict": verdict}


def _summary(rows):
    match = [r for r, c in zip(rows, CASES) if c["kind"] == "match"]
    hist = [r for r, c in zip(rows, CASES) if c["kind"] == "history"]
    return {"correct": sum(r["correct"] for r in rows), "total": len(rows),
            "match_correct": sum(r["correct"] for r in match), "match_total": len(match),
            "history_correct": sum(r["correct"] for r in hist), "history_total": len(hist),
            "false_alarms": sum(r["verdict"] == "false alarm" for r in rows),
            "risky": sum(r["verdict"].startswith("risky") for r in rows),
            "tokens": sum(r["tokens"] for r in rows)}


def run(progress=None):
    if not tools.llm_enabled():
        raise RuntimeError("The AI arm needs GROQ_API_KEY in .env (and LLM_MODE not set to offline).")
    total = 2 * len(CASES)
    rules = _run_arm("rules", progress, 0, total)
    ai = _run_arm("ai", progress, len(CASES), total)
    out = {"meta": {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": tools.MODEL},
           "rules": {"summary": _summary(rules), "rows": rules}, "ai": {"summary": _summary(ai), "rows": ai}}
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str, ensure_ascii=False)
    return out


def load():
    if not os.path.exists(RESULTS):
        return None
    with open(RESULTS, encoding="utf-8") as f:
        return json.load(f)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    if not tools.llm_enabled():
        sys.exit("Needs GROQ_API_KEY in .env (and LLM_MODE not offline): the 'rules + AI' arm calls Groq.")
    out = run(progress=lambda i, n: print(f"\r  case {i}/{n}", end="", flush=True))
    print()
    for arm in ("rules", "ai"):
        s = out[arm]["summary"]
        print(f"{'Rules alone' if arm == 'rules' else 'Rules + AI':12}: {s['correct']}/{s['total']} correct "
              f"(matching {s['match_correct']}/{s['match_total']}, history {s['history_correct']}/{s['history_total']}), "
              f"false alarms {s['false_alarms']}, risky approvals {s['risky']}, tokens {s['tokens']:,}")
    for c, r, a in zip(CASES, out["rules"]["rows"], out["ai"]["rows"]):
        print(f"  {c['n']:2} {c['category'][:34]:34} rules: {r['verdict']:28} ai: {a['verdict']}")


if __name__ == "__main__":
    main()

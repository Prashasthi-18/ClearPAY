"""
evaluate.py - measure the agent against labelled invoices.

Runs on a throwaway copy of the database (data/eval_*.db), so the live app is never touched.

  python evaluate.py                       # test set + red-team, results shown in the UI
  python evaluate.py --mode offline        # force the rule-based mode (no Groq calls)
  python evaluate.py --save-baseline       # store current decisions as the regression baseline
  python evaluate.py --check-regression    # exit code 1 if a previously-correct decision broke (for CI)

Prints: extraction accuracy, correct routing %, duplicates caught, red-team blocked, average time.
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

import agent
import db
import tools

RESULTS = {
    "test": os.path.join(db.DATA_DIR, "eval_results.json"),
    "redteam": os.path.join(db.DATA_DIR, "redteam_results.json"),
}
BASELINE = os.path.join(db.BASE_DIR, "regression_baseline.json")   # committed to git; CI compares against it
LATEST_REGRESSION = os.path.join(db.DATA_DIR, "regression_latest.json")
FIELDS = ("vendor", "invoice_number", "date", "po_number", "total", "line_items")


def _field_ok(field, expected, got):
    if field == "total":
        return got is not None and expected is not None and abs(got - expected) <= 0.01
    if field == "line_items":
        return got == expected
    norm = lambda x: (str(x).strip().upper() if x is not None else None)
    return norm(expected) == norm(got)


def run_set(name, folder, mode, progress=None):
    """Process every file in `folder` (in order) on a fresh database. Returns per-invoice results."""
    with open(os.path.join(folder, "expected_outcomes.json")) as f:
        expected = json.load(f)
    work = os.path.join(db.DATA_DIR, f"eval_files_{name}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    rows = []
    with db.using(os.path.join(db.DATA_DIR, f"eval_{name}.db")):
        db.reset_db()
        db.seed()
        for i, case in enumerate(expected, 1):
            src = os.path.join(folder, case["file"])
            dst = os.path.join(work, case["file"])
            shutil.copy(src, dst)
            iid = agent.create_invoice(dst, case["file"], case["file"].rsplit(".", 1)[-1], "eval")
            t0 = time.time()
            status = agent.process_invoice(iid, mode=mode)
            inv = db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)
            ex = json.loads(inv["extracted_json"] or "null")
            got = None
            if ex:
                got = {"vendor": ex["vendor"], "invoice_number": ex["invoice_number"], "date": ex["date"],
                       "po_number": ex["po_number"], "total": ex["total"], "line_items": len(ex["line_items"])}
            rows.append({**case, "status": status, "reason": inv["decision_reason"], "extracted": got,
                         "issues": [x["code"] for x in json.loads(inv["issues_json"] or "[]")],
                         "ms": int((time.time() - t0) * 1000), "confidence": inv["confidence"],
                         "prompt_tokens": inv["prompt_tokens"] or 0, "completion_tokens": inv["completion_tokens"] or 0})
            if progress:
                progress(name, i, len(expected))
    return rows


def summarise_test(rows, settings):
    field_hits = {f: [0, 0] for f in FIELDS}
    for r in rows:
        if r["expected"] is None:
            continue
        for f in FIELDS:
            field_hits[f][1] += 1
            if r["extracted"] and _field_ok(f, r["expected"][f], r["extracted"][f]):
                field_hits[f][0] += 1
    total_hits = sum(h for h, _ in field_hits.values())
    total_fields = sum(n for _, n in field_hits.values())
    by_case = {}
    for r in rows:
        c = by_case.setdefault(r["case_type"], {"case_type": r["case_type"], "expected": r["expected_status"],
                                                "passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += r["status"] == r["expected_status"]
    dups = [r for r in rows if r["case_type"] == "Duplicate"]
    pt = sum(r["prompt_tokens"] for r in rows)
    ct = sum(r["completion_tokens"] for r in rows)
    cost = pt / 1e6 * settings["groq_input_usd_per_m"] + ct / 1e6 * settings["groq_output_usd_per_m"]
    return {
        "extraction_accuracy": round(100 * total_hits / total_fields, 1) if total_fields else 0,
        "field_accuracy": {f: round(100 * h / n, 1) if n else 0 for f, (h, n) in field_hits.items()},
        "routing_correct": sum(r["status"] == r["expected_status"] for r in rows),
        "routing_total": len(rows),
        "routing_pct": round(100 * sum(r["status"] == r["expected_status"] for r in rows) / len(rows), 1),
        "duplicates_caught": sum(r["status"] == "Rejected" for r in dups),
        "duplicates_total": len(dups),
        "avg_ms": int(sum(r["ms"] for r in rows) / len(rows)),
        "prompt_tokens": pt, "completion_tokens": ct,
        "avg_tokens": int((pt + ct) / len(rows)),
        "est_cost_usd": round(cost, 4),
        "est_cost_per_invoice_usd": round(cost / len(rows), 5),
        "by_case": list(by_case.values()),
        "failures": [r for r in rows if r["status"] != r["expected_status"]],
    }


def summarise_redteam(rows):
    attacks = [r for r in rows if not r["setup"]]
    for r in attacks:
        r["blocked"] = r["status"] in r["blocked_if"]
    cats = {}
    for r in attacks:
        c = cats.setdefault(r["category"], {"category": r["category"], "blocked": 0, "total": 0})
        c["total"] += 1
        c["blocked"] += r["blocked"]
    return {"blocked": sum(r["blocked"] for r in attacks), "total": len(attacks),
            "reached_approved": sum(r["status"] == "Approved" for r in attacks),
            "categories": list(cats.values()), "attacks": attacks}


def regression_report(test_rows, meta):
    """Compare this run's decisions with the saved baseline."""
    if not os.path.exists(BASELINE):
        return {"has_baseline": False}
    with open(BASELINE) as f:
        base = json.load(f)
    changes = []
    for r in test_rows:
        b = base["decisions"].get(r["file"])
        if b is None:
            continue
        if b["status"] != r["status"] or b.get("issues") != r["issues"]:
            if b["status"] == r["status"]:
                verdict = "reason changed"
            elif r["status"] == r["expected_status"]:
                verdict = "fixed"
            elif b["status"] == r["expected_status"]:
                verdict = "new bug"
            else:
                verdict = "still wrong"
            changes.append({"file": r["file"], "baseline": b["status"], "candidate": r["status"],
                            "expected": r["expected_status"], "verdict": verdict,
                            "baseline_issues": b.get("issues"), "candidate_issues": r["issues"],
                            "reason": r["reason"]})
    return {"has_baseline": True, "baseline_meta": base["meta"], "candidate_meta": meta,
            "unchanged": len(test_rows) - len(changes), "changes": changes,
            "fixed": sum(c["verdict"] == "fixed" for c in changes),
            "new_bugs": sum(c["verdict"] == "new bug" for c in changes),
            "reason_only": sum(c["verdict"] == "reason changed" for c in changes)}


def save_baseline(test_rows, meta):
    with open(BASELINE, "w") as f:
        json.dump({"meta": meta, "decisions": {r["file"]: {"status": r["status"], "issues": r["issues"]}
                                               for r in test_rows}}, f, indent=2)


def run_all(mode=None, sets=("test", "redteam"), progress=None):
    """Used by the CLI and by the 'Run evaluation' button in the UI."""
    resolved = mode or ("llm" if tools.llm_enabled() else "offline")
    meta = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": resolved,
            "model": tools.MODEL if resolved == "llm" else "rule-based", "prompt_version": agent.PROMPT_VERSION}
    settings = db.get_settings()
    out = {}
    if "test" in sets:
        rows = run_set("test", db.SAMPLE_DIR, resolved, progress)
        summary = {"meta": meta, **summarise_test(rows, settings), "rows": rows}
        with open(RESULTS["test"], "w") as f:
            json.dump(summary, f, indent=2, default=str)
        reg = regression_report(rows, meta)
        with open(LATEST_REGRESSION, "w") as f:
            json.dump(reg, f, indent=2, default=str)
        out["test"], out["regression"] = summary, reg
    if "redteam" in sets:
        rows = run_set("redteam", db.REDTEAM_DIR, resolved, progress)
        summary = {"meta": meta, **summarise_redteam(rows)}
        with open(RESULTS["redteam"], "w") as f:
            json.dump(summary, f, indent=2, default=str)
        out["redteam"] = summary
    return out


def load(name):
    path = {"test": RESULTS["test"], "redteam": RESULTS["redteam"], "regression": LATEST_REGRESSION}[name]
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description="Evaluate the AP agent on labelled invoices.")
    ap.add_argument("--mode", choices=["auto", "llm", "offline"], default="auto")
    ap.add_argument("--set", choices=["test", "redteam", "both"], default="both")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--check-regression", action="store_true")
    a = ap.parse_args()
    if a.mode == "llm" and not tools.llm_enabled():
        sys.exit("LLM mode needs GROQ_API_KEY in .env")
    mode = None if a.mode == "auto" else a.mode
    sets = ("test", "redteam") if a.set == "both" else (a.set,)
    if not os.path.exists(os.path.join(db.SAMPLE_DIR, "expected_outcomes.json")):
        sys.exit("No test data yet. Run: python generate_data.py")

    out = run_all(mode, sets, progress=lambda s, i, n: print(f"\r  {s}: {i}/{n}", end="", flush=True))
    print()
    if "test" in out:
        t = out["test"]
        print(f"\n== Test set ({t['meta']['mode']}, {t['meta']['model']}) ==")
        print(f"Extraction accuracy : {t['extraction_accuracy']}%")
        print(f"Correct routing     : {t['routing_correct']}/{t['routing_total']} ({t['routing_pct']}%)")
        print(f"Duplicates caught   : {t['duplicates_caught']}/{t['duplicates_total']}")
        print(f"Avg processing time : {t['avg_ms'] / 1000:.2f}s")
        print(f"Tokens / est. cost  : {t['avg_tokens']} per invoice, ${t['est_cost_usd']} total")
        for r in t["failures"]:
            print(f"  ✗ {r['file']}: expected {r['expected_status']}, got {r['status']} ({r['reason']})")
        if a.save_baseline:
            save_baseline(t["rows"], t["meta"])
            print("Saved as regression baseline.")
    if "redteam" in out:
        rt = out["redteam"]
        print(f"\n== Red-team ==\nAttacks blocked     : {rt['blocked']}/{rt['total']} "
              f"(reached Approved: {rt['reached_approved']})")
        for r in rt["attacks"]:
            if not r["blocked"]:
                print(f"  ✗ {r['file']}: {r['status']} ({r['reason']})")
    reg = out.get("regression")
    if reg and reg.get("has_baseline"):
        print(f"\n== Regression vs baseline ==\nUnchanged {reg['unchanged']}, fixed {reg['fixed']}, "
              f"new bugs {reg['new_bugs']}, reason-only {reg['reason_only']}")
        if a.check_regression and reg["new_bugs"]:
            sys.exit(1)


if __name__ == "__main__":
    main()

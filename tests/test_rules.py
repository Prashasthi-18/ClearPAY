"""Business rules are pure code: same checks + same settings = same decision."""
import tools

SETTINGS = {"auto_approve_limit": 5000, "variance_tolerance_pct": 5, "min_confidence": 0.7, "split_window_days": 14}


def clean_checks(**over):
    checks = {
        "extraction": {"ok": True, "confidence": 0.95, "injection_phrases": [], "hidden_chars": 0},
        "vendor": {"status": "known", "vendor_id": 1, "bank_changed": False, "gstin_mismatch": False},
        "duplicate": {"duplicate_of": None, "near_duplicate_of": None},
        "math": {"ok": True, "missing_fields": [], "line_errors": [], "subtotal_ok": True, "total_ok": True,
                 "gst_issues": [], "stated_total": 1180, "computed_total": 1180, "stated_subtotal": 1000,
                 "computed_subtotal": 1000},
        "po": {"found": True, "po_number": "PO-1", "po_vendor": "X", "vendor_ok": True, "lines": [], "unmatched_lines": [],
               "variance_pct": 0.0, "expected_total": 1180, "invoice_total": 1180, "po_total": 1180,
               "committed_before": 0, "recent_totals_on_po": [], "po_tax_rate": 0.18, "invoice_tax_rate": 0.18},
    }
    for k, v in over.items():
        checks[k].update(v)
    return checks


def run(checks, total=1180, **settings):
    return tools.decide(checks, {**SETTINGS, **settings}, {"total": total, "tax": 180, "invoice_number": "A-1"})


def test_exact_match_under_limit_is_approved():
    assert run(clean_checks())[0] == "Approved"


def test_over_limit_goes_to_manager():
    assert run(clean_checks(po={"invoice_total": 6000, "expected_total": 6000, "po_total": 6000}), total=6000)[0] == "Pending"


def test_small_variance_goes_to_manager_large_is_flagged():
    assert run(clean_checks(po={"variance_pct": 3.0}))[0] == "Pending"
    assert run(clean_checks(po={"variance_pct": 12.0}))[0] == "Flagged"


def test_duplicate_is_rejected_even_if_everything_else_is_fine():
    assert run(clean_checks(duplicate={"duplicate_of": {"id": 1, "invoice_number": "A-1", "status": "Approved"}}))[0] == "Rejected"


def test_fraud_signals_are_flagged():
    for over in ({"vendor": {"status": "unknown", "vendor_id": None}},
                 {"vendor": {"status": "lookalike", "similar_to": "Acme Corp", "similarity": 0.82}},
                 {"vendor": {"bank_changed": True, "bank_on_file": "••••1111", "bank_on_invoice": "••••2222"}},
                 {"extraction": {"injection_phrases": ["ignore previous instructions"]}}):
        assert run(clean_checks(**over))[0] == "Flagged", over


def test_low_confidence_needs_a_human():
    assert run(clean_checks(extraction={"confidence": 0.5}))[0] == "Pending"


def test_unreadable_file_is_flagged():
    status, reason, issues = tools.decide({"extraction": {"ok": False, "error": "bad"}}, SETTINGS, None)
    assert status == "Flagged" and issues[0]["code"] == "unreadable"


def test_split_invoices_need_a_manager():
    checks = clean_checks(po={"recent_totals_on_po": [4248], "po_total": 8496, "committed_before": 4248,
                              "invoice_total": 4248, "expected_total": 4248})
    assert run(checks, total=4248)[0] == "Pending"


def test_math_is_done_in_code():
    ctx = {"checks": {"extraction": {"ok": True}}, "invoice_id": None, "extracted": {
        "vendor": "X", "invoice_number": "1", "date": "2026-01-01", "total": 1000.0, "subtotal": 900.0, "tax": 100.0,
        "vendor_gstin": None, "cgst": None, "sgst": None, "igst": None,
        "line_items": [{"description": "a", "quantity": 3, "unit_price": 300, "amount": 950}]}}
    tools.log = lambda *a, **k: None
    res = tools.tool_validate_math(ctx)
    assert res["ok"] is False and res["line_errors"] == 1


def test_parser_and_po_normaliser():
    assert tools.normalize_po("P.O. #2040") == "PO-2040"
    raw = tools.parse_invoice_text("Vendor: Acme Corp\nInvoice No: ACM-1\nInvoice Date: 2026-09-01\nPO Number: PO-2008\n"
                                   "1. Kit | Qty: 3 | Unit Price: INR 900.00 | Amount: INR 2,700.00\n"
                                   "Subtotal: INR 2,700.00\nCGST (9%): INR 243.00\nSGST (9%): INR 243.00\nTotal: INR 3,186.00")
    assert raw["total"] == 3186.0 and raw["tax"] == 486.0 and len(raw["line_items"]) == 1


def test_tax_is_summed_from_cgst_sgst_when_the_model_leaves_it_blank():
    raw = {"vendor": "A", "invoice_number": "1", "date": "2026-01-01", "total": 3617.62, "subtotal": 3065.78,
           "cgst": 275.92, "sgst": 275.92, "tax": None, "line_items": []}
    assert tools.normalize_extraction(raw)["tax"] == 551.84


def test_tax_is_read_from_the_text_when_the_model_skips_all_tax_fields():
    text = "Subtotal: Rs. 3,065.78\nCGST @ 9%: Rs. 275.92\nSGST @ 9%: Rs. 275.92\nTotal: Rs. 3,617.62"
    data = tools.normalize_extraction({"vendor": "A", "total": 3617.62, "subtotal": 3065.78})
    assert data["tax"] is None and tools.fill_missing_tax(data, text) and data["tax"] == 551.84


class _FakeRateLimit(Exception):
    status_code = 429

    def __init__(self, wait):
        self.response = type("R", (), {"headers": {"retry-after": str(wait)}})()


def _fake_groq(monkeypatch, errors):
    """Make tools.call_groq see Groq answering with these errors, then success. Returns the list of sleeps."""
    sleeps, answers = [], list(errors)

    class Completions:
        @staticmethod
        def create(**kw):
            if answers:
                raise answers.pop(0)
            return type("Resp", (), {"usage": None, "choices": []})()

    client = type("C", (), {"chat": type("Chat", (), {"completions": Completions})()})()
    monkeypatch.setattr(tools, "_client", client)
    monkeypatch.setattr(tools.time, "sleep", sleeps.append)
    monkeypatch.setattr(tools, "log", lambda *a, **k: None)
    return sleeps


def _ctx():
    return {"prompt_tokens": 0, "completion_tokens": 0, "invoice_id": 1}


def test_groq_rate_limit_follows_the_wait_hint(monkeypatch):
    sleeps = _fake_groq(monkeypatch, [_FakeRateLimit(7)])
    tools.call_groq(_ctx(), messages=[])
    assert sleeps == [7.5]                      # waited what Groq asked, then succeeded


def test_groq_long_rate_limit_gives_up_at_once_with_a_reason(monkeypatch):
    import pytest
    sleeps = _fake_groq(monkeypatch, [_FakeRateLimit(300)])
    ctx = _ctx()
    with pytest.raises(RuntimeError, match="asks to wait 300"):
        tools.call_groq(ctx, messages=[])
    assert sleeps == [] and "rate limit" in ctx["llm_down"]


def test_fallback_reading_is_explained_as_ai_unavailable():
    checks = {"extraction": {"ok": True, "confidence": 0.6, "fallback": True, "injection_phrases": [], "hidden_chars": 0},
              "vendor": {"status": "known", "vendor_id": 1, "bank_changed": False, "gstin_mismatch": False},
              "duplicate": {}, "math": {"ok": True, "missing_fields": [], "line_errors": [], "subtotal_ok": True,
                                        "total_ok": True, "gst_issues": []},
              "po": {"found": True, "po_number": "PO-1", "vendor_ok": True, "lines": [], "unmatched_lines": [],
                     "variance_pct": 0.0, "po_total": 1180, "committed_before": 0, "recent_totals_on_po": []}}
    status, _, issues = tools.decide(checks, {"auto_approve_limit": 5000, "variance_tolerance_pct": 5,
                                              "min_confidence": 0.7, "split_window_days": 14}, {"total": 1180})
    assert status == "Pending" and "didn't answer" in issues[0]["title"]


def test_groq_limit_messages_are_read_in_plain_words():
    class E(Exception):
        status_code = 429
        response = type("R", (), {"headers": {}})()

    name, wait = tools._limit_info(E("... on tokens per minute (TPM): Limit 8000 ... Please try again in 8.1s."))
    assert name == "per-minute token limit" and wait == 8.1
    name, wait = tools._limit_info(E("... on tokens per day (TPD): Limit 200000 ... Please try again in 2m40.4s. Need more?"))
    assert name == "daily token limit" and round(wait, 1) == 160.4


def test_gpt_oss_gets_low_reasoning_effort_and_other_models_do_not(monkeypatch):
    monkeypatch.setattr(tools, "MODEL", "openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_REASONING_EFFORT", raising=False)
    assert tools._model_options() == {"extra_body": {"reasoning_effort": "low"}}
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "off")
    assert tools._model_options() == {}
    monkeypatch.delenv("GROQ_REASONING_EFFORT")
    monkeypatch.setattr(tools, "MODEL", "llama-3.1-8b-instant")
    assert tools._model_options() == {}

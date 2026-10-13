# ClearPay: project context

An end-to-end reference for anyone (person or AI agent) working on this codebase. It covers what the product does, how an invoice flows through it, where each piece of logic lives, the data model, security, testing and deployment. For user-facing setup steps, see [README.md](README.md) and [deploy/](deploy/).

---

## 1. What ClearPay is

ClearPay is an **accounts-payable (AP) automation agent** for Indian businesses. It takes vendor invoices (PDF, text or scanned image), reads them, checks them against the matching **purchase order (PO)**, and routes each one to one of four outcomes:

| Status | Meaning | Ledger |
|---|---|---|
| **Approved** | Clean: exact PO match, maths correct, under the auto-approve limit | `ready_to_pay` |
| **Pending** | Needs a manager: small variance, over the limit, low confidence, AI item match, possible split | `pending_approval` |
| **Flagged** | Needs AP review: fraud signs, wrong maths, missing or unknown PO, big mismatch, unreadable file | none |
| **Rejected** | Exact duplicate | none / `cancelled` |

(`Processing` is the transient status while the agent works.)

It drafts emails (to manager, vendor, AP team), writes a tamper-evident audit log of every step, and gives people the tools to decide on exceptions.

### Core design principle

> **The LLM reads documents and chooses tool order. Code does every calculation and makes every decision. People approve and record payment.**

- ClearPay **never pays anyone**. There is no payment code. "Ready for payment" is the last automated state; a person records a payment made outside the app (**Record payment**), or exports a bank CSV.
- The AI **never makes a payment decision**. This is structural, not only a prompt rule:
  - Routing is decided by the pure function `tools.decide()`.
  - `write_ledger_entry` is refused by a code guardrail unless all checks ran and the code decision is Approved or Pending.
  - Tools read numbers from Python state (`ctx`), not from LLM-supplied arguments.
  - The AI features (Investigator, Ask ClearPay, plain-English search) reach data **only** through `retrieval.py`, which rejects anything except a single `SELECT`.
  - Any invoice needing an AI item match goes to a person.
  - Fraud signs can never receive an "approve" recommendation (overridden in code).
- **Everything falls back to rules** when there's no Groq key, `LLM_MODE=offline`, or Groq is rate-limited or down.

India-specific: ₹ formatting (₹1,25,000.00; ₹5.4L / ₹1.2Cr), GSTIN format and CGST = SGST checks, GST rate vs PO, MSME 45-day payment cap, IST timestamps.

---

## 2. Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 (`.python-version`). The local `.venv` was built with 3.14. |
| Web | Flask 3.1 with Jinja templates, Bootstrap (bundled locally in `static/vendor/`, works offline), vanilla JS |
| LLM | Groq API, model `openai/gpt-oss-120b`, tool calling (`groq` SDK). Reasoning effort set by `GROQ_REASONING_EFFORT` |
| Database | SQLite (`data/ap_agent.db`) by default; PostgreSQL 16 via `psycopg` when `DATABASE_URL` is set |
| Documents | `pdfplumber` (PDF text and word boxes), `pytesseract` + Tesseract binary (OCR, optional), `Pillow`, `reportlab` (generating test PDFs) |
| Auth extras | Google OIDC (`httpx`), TOTP MFA (`qrcode`, `cryptography` Fernet) |
| Email | mock / SMTP / Brevo HTTPS API |
| Production | gunicorn (1 worker, 4 threads), Caddy for HTTPS, systemd |
| CI | GitHub Actions: pytest (SQLite and Postgres), offline evaluation and regression gate, bandit, pip-audit |

Pinned dependencies are in [requirements.txt](requirements.txt); dev extras (pytest, bandit, pip-audit) in [requirements-dev.txt](requirements-dev.txt).

---

## 3. Repository layout

The project lives in `clearpay/` inside the workspace folder. It is not a git repository locally.

```
clearpay/
├── app.py                 Flask app: every route, permission rules (can()), approval flow, account pages, startup()
├── wsgi.py                Production entry point (gunicorn wsgi:app); calls startup()
├── agent.py               Invoice agent: Groq tool loop, rule loop, finalize(), background worker queue
├── tools.py               The 8 agent tools, extraction, maths/GST/PO checks, decide(), emails, PDF highlights
├── db.py                  DB layer (SQLite/Postgres), schema, migrations, settings, seed data, hash-chained audit log
├── security.py            Lockout, sessions, role decorators, CSRF, rate limits, headers, upload checks, injection scanner
├── smart_match.py         AI-assisted invoice-line ↔ PO-line matching (wording, units)
├── investigator.py        AI Investigator for flagged invoices (read-only tools → recommendation + email draft)
├── retrieval.py           The ONLY data access for AI features: SELECT-only, masked results
├── assistant.py           "Ask ClearPay" chat + "Ask in plain English" search
├── helpbot.py             Help guide (fixed topics, no data access)
├── insights.py            Payment deadlines, vendor risk score, analytics (pure code, no AI)
├── notifications.py       In-app notifications (who is told about which event)
├── mailer.py              Real email sending (smtp | brevo | mock), redirect test mode
├── email_inbox.py         IMAP mailbox pickup of invoices
├── watcher.py             Background thread watching invoices_inbox/
├── google_auth.py         "Continue with Google" (OIDC + PKCE + state + nonce)
├── access_requests.py     Google sign-up requests approved by admins via one-time email links
├── mfa.py                 TOTP two-factor + hashed recovery codes, Fernet-encrypted secrets
├── evaluate.py            Accuracy / routing / red-team evaluation, regression baseline and gate
├── compare_ai.py          "Rules alone vs rules + AI" on 20 tricky invoices
├── generate_data.py       Rebuilds the DB + 30 labelled test invoices + 18 red-team attacks
├── import_kaggle_pos.py   Builds seed_data.json and test files from the Kaggle procurement CSV
├── load_po_data.py        Loads the remaining Kaggle rows as PO-2040+ (master-data loader)
├── migrate_sqlite_to_postgres.py   Copies an existing SQLite DB into Postgres and verifies it
├── seed_data.json         Kaggle-derived vendors/POs; db.py prefers it over the built-in demo data
├── regression_baseline.json        Approved decisions; CI fails if a correct one breaks
├── templates/             Jinja pages (base.html layout, macros.html, one template per page)
├── static/                app.js, login.js, motion.js, palette.js (Ctrl+K), theme.js, style.css, icons.svg (Lucide), brand/
├── tests/                 pytest suites (see §11)
├── deploy/                DEPLOY.md, POSTGRES.md, AUTH_SETUP.md, Caddyfile, clearpay.service, backup.sh, env.production.example
├── sample_invoices/       30 labelled test invoices + expected_outcomes.json (generated)
├── redteam_invoices/      18 attacks + 3 setup invoices + expected_outcomes.json (generated)
├── data/                  Runtime: DB file, uploads/ (private), eval/compare DBs and results JSON, backups/
└── .github/workflows/ci.yml
```

`data/`, `sample_invoices/`, `redteam_invoices/`, `invoices_inbox/`, `.env` and the Kaggle CSV are git-ignored. `invoices_inbox/`, `data/` and `data/uploads/` are created on import of `db.py`.

---

## 4. End-to-end invoice flow

```
 Upload page (AP/admin) ─┐
 invoices_inbox/ watcher ┼─► security.check_upload → store_file (random name, data/uploads/)
 IMAP inbox pickup ──────┘          │
                                    ▼
                    agent.create_invoice()  → invoices row, status 'Processing'
                                    │
                                    ▼
                    agent.submit() → in-memory queue → single "agent-worker" thread
                                    │
                                    ▼
                    agent.process_invoice(id)
                      ├─ LLM_MODE=auto + key   → run_llm_loop (Groq picks tools, ≤14 turns)
                      ├─ LLM_MODE=extract_only → run_rule_loop (Groq only reads the invoice)
                      └─ offline / no key      → run_rule_loop with the rule-based parser
                                    │
                                    ▼
                    finalize(ctx)  ← always runs, in code
                      1. runs any check the LLM skipped (logged as "guardrail")
                      2. tools.decide() → status, reason, ranked issues
                      3. ledger entry (Approved → ready_to_pay, Pending → pending_approval)
                      4. drafts the required emails (deduplicated; never emails a suspected-fraud vendor)
                      5. PDF highlight boxes, plain-English summary, due date, tokens, timing
                      6. audit "decision" row
                                    │
                    ┌───────────────┼────────────────────┐
                    ▼               ▼                    ▼
           notifications     Investigator queue    Dashboard / invoice page
           (on_processed)    (if Flagged and        (live step feed via /api/feed,
                              auto_investigate=1)    /invoice/<id>/status)
                                    │
                                    ▼
                 People: AP reviews Flagged → Send to manager / Reject / Process again
                         Manager decides Pending → Approve / Reject (not on their own upload)
                         AP/admin → Record payment (ledger 'paid') or Payment file (CSV)
```

If anything throws inside `process_invoice`, the invoice is set to **Flagged** ("Processing error — needs manual review"); it is never left stuck. On startup, any invoice still in `Processing` is requeued.

### The 8 agent tools (`agent.TOOL_SCHEMAS` → `tools.TOOL_FUNCS`)

| Tool | What the code does |
|---|---|
| `extract_invoice_data` | `read_document` (pdfplumber text, OCR fallback for images and scanned PDFs) → injection scan + hidden-text count → Groq extraction with `EXTRACTION_PROMPT` (JSON, validated by `normalize_extraction`) or `parse_invoice_text` offline. Computes confidence. |
| `check_vendor` | Exact or normalised match against `vendors`; look-alike detection (e.g. "Acrne Corp", "Gamma C0"); bank account / IFSC and GSTIN compared to the record on file. |
| `check_duplicate` | Same vendor + invoice number → duplicate; similar bill → near-duplicate. |
| `validate_math` | Required fields, each line qty × price, subtotal, tax, total (±₹0.011), GST checks (GSTIN regex, CGST = SGST). |
| `lookup_po` | Finds the PO (or the PO typed on upload), checks the vendor, pairs lines via `smart_match`, checks quantity/price/amount per line, tax rate, variance %, amount already billed on the PO, recent invoices (split detection), partial delivery. |
| `write_ledger_entry` | Guardrail: refused unless every required check ran and `decide()` returns Approved/Pending. |
| `draft_email` | Template-built draft (never free LLM text); vendor emails always go to the address on file. |
| `escalate_to_human` | Records an escalation reason. |

Every tool call's arguments are validated against an allowlist (`agent.run_tool`); unknown tools are refused.

### LLM modes (`LLM_MODE` in `.env`)

| Mode | Groq usage | Tokens per invoice |
|---|---|---|
| `auto` | Groq orchestrates the tools | ~6,000–11,000 |
| `extract_only` (default, recommended for the free plan) | Groq only reads the invoice; code runs checks in a fixed order | ~1,000 |
| `offline` | None; rule-based parser (works for the generated test invoices) | 0 |

`tools.call_groq` retries 3 times, follows short rate-limit waits, and stops immediately on the daily limit. Then the invoice is read with rules and confidence is capped, which routes it to a person ("The AI didn't answer").

---

## 5. Decision rules (`tools.decide`)

`decide(checks, settings, extracted)` is a **pure function**: the same checks and settings always give the same answer. That is what makes What-if replay and regression testing possible. Each problem becomes an issue `{code, severity, title, detail, find, fix_by}`; the worst severity wins: `rejected` > `flagged` > `pending` > `info` (info doesn't change the route).

| Severity | Issue codes |
|---|---|
| **rejected** | `duplicate` |
| **flagged** | `unreadable`, `not_an_invoice`, `prompt_injection` (phrases or >5 hidden characters), `unknown_vendor`, `lookalike_vendor`, `bank_changed`, `gstin_mismatch`, `missing_fields`, `line_math`, `subtotal_math`, `total_math`, `gst`, `missing_po`, `po_not_found`, `po_vendor`, `line_not_on_po`, `tax_rate`, `near_duplicate`, `over_billed` (PO used up), `variance_large` (> tolerance) |
| **pending** | `ai_match`, `unit_unverified`, `variance_small` (0 < variance ≤ tolerance), `split_invoice`, `low_confidence`, `over_limit` |
| **info** | `qty_over`, `price_diff` (shown, but route is decided through variance) |

No issues → **Approved** ("Exact match with PO, math checks out, under the limit", or "partial delivery").

Thresholds come from the `settings` table, editable by admins on **Settings**:

| Key | Default | Notes |
|---|---|---|
| `auto_approve_limit` | 5000 (₹, incl. tax) | `seed_data.json` sets **50000** for the Kaggle data |
| `variance_tolerance_pct` | 5 | |
| `min_confidence` | 0.7 | |
| `split_window_days` | 14 | |
| `default_payment_terms_days` | 30 | |
| `msme_max_days` | 45 | MSMED Act cap |
| `auto_investigate` | 1 | Investigator on every flagged invoice |
| `smart_match_min_confidence` | 0.7 | Lower AI matches count as "not on the PO" |
| `cost_saved_per_invoice_inr` | 860 | For "money saved" analytics |
| `groq_input_usd_per_m` / `groq_output_usd_per_m` | 0.59 / 0.79 | Cost estimate (USD, as Groq bills) |
| `ap_team_email`, `manager_email` | `*.example` placeholders | `.example` addresses are refused when sending |

---

## 6. AI features (all read-only, all with a rules fallback)

| Feature | Module | How it stays safe |
|---|---|---|
| **Invoice reading** | `tools.py` | Invoice text is marked untrusted; injection scanner runs in code; JSON output validated. |
| **Smart matching** | `smart_match.py` | Code pairs identical names first (no AI). Only differently-worded lines go to the AI, one call per invoice, which returns the PO line + unit factor ("box of 10 reams" → 10). Code then checks all numbers. Any AI match → `ai_match` (pending). |
| **Investigator** | `investigator.py` | Tool-calling agent over `get_invoice`, `get_purchase_order`, `get_vendor_history`, `find_similar_cases` (past human decisions and comments). Outputs approve / reject / ask_vendor plus evidence and a vendor email draft. `_validate` enforces vocabulary, caps lengths, masks bank numbers, forces fraud cases away from "approve", and drops vendor emails for fraud. Saved to `investigations`, never touches the invoice or ledger. Runs in its own queue. Agreement with human decisions is tracked (`human_decision`). |
| **Ask ClearPay** | `assistant.py` | Chat over 7 read-only tools. Answers that mention invoice/PO numbers not actually retrieved are discarded and replaced with a rule-built answer. |
| **Ask in plain English** | `assistant.py` | Regex rules turn text into filters ("flagged", "over ₹50,000", "from Alpha", "overdue"); the AI only fills a filter form that code validates when the rules understand nothing. |
| **Help guide** | `helpbot.py` | Fixed help topics, keyword search; the AI only rephrases. No data access. |

`retrieval.py` is the security boundary: `_select()` raises `ReadOnlyViolation` for anything but a single `SELECT`, results are small dicts, bank accounts are masked, no passwords/sessions/user emails are returned.

---

## 7. Data model (`db.SCHEMA`)

| Table | Purpose / key columns |
|---|---|
| `users` | `username`, `full_name`, `email`, `role` (`ap`/`manager`/`admin`), `password_hash` (scrypt), lockout fields, `active`, `theme`, `has_password`, `google_sub`, `mfa_*` |
| `user_sessions` | One row per sign-in (id, UA, IP, last_seen, ended_at/end_reason); enables server-side sign-out and idle timeout |
| `access_requests` | Google sign-up requests; only SHA-256 of the one-time token is stored |
| `notifications` | Per-recipient rows; `kind` = approved / rejected / needs_approval / attention / failed / security |
| `vendors` | `name`, `email`, `gstin`, `bank_account`, `ifsc`, `active`, `msme` |
| `purchase_orders` / `po_lines` | PO header (subtotal, tax_rate, tax, total, status open/closed) and lines (description, quantity, unit_price) |
| `invoices` | File info, `source` (upload/inbox/eval), `uploaded_by`, `status`, extracted fields, `confidence`, JSON blobs (`extracted_json`, `checks_json`, `issues_json`, `comparison_json`, `highlights_json`), `decision_reason`, `summary`, `agent_mode`, tokens, `processing_ms`, decision fields, `payment_terms_days`, `due_date`, `email_from` |
| `ledger` | One per invoice: `amount`, `status` (pending_approval / ready_to_pay / paid / cancelled), `paid_at`/`paid_by` (a person recorded it) |
| `investigations` | Investigator output: `mode` (ai/rules), `recommendation`, findings, steps, similar cases, `human_decision` |
| `emails` | Drafts: `recipient_type`, `purpose`, `to_addr`, `subject`, `body`, `status` (draft/sent), `last_error` |
| `inbox_messages` | IMAP messages already processed (dedup by `message_id`) |
| `audit_log` | `actor`, `actor_type`, `step`, `result` (ok/problem/info/blocked), `reason`, `detail_json`, **`prev_hash` → `hash`** (SHA-256 chain; `verify_audit_chain()` powers "Log integrity: verified") |
| `settings` | Key/value business rules (§5) |

**Migrations:** `init_db()` runs `CREATE TABLE IF NOT EXISTS` and adds missing columns with `ALTER TABLE` in place. There's no migration framework.

**DB abstraction:** all code uses `db.query()` / `db.execute()` with `?` placeholders; `_to_pg` rewrites SQL for Postgres. Evaluation and tests use throwaway databases: separate files in SQLite, separate **schemas** (`s_eval_test_…`, `s_eval_redteam_…`) in Postgres. `db.using(path)` switches database for the current thread only.

**Seed data:** built-in demo data is 8 vendors, PO-2001…PO-2025 and 4 users. If `seed_data.json` exists (written by `import_kaggle_pos.py`), its vendors, POs and settings replace the built-in ones. The current file has **5 vendors, 39 POs, limit ₹50,000**. `load_po_data.py` adds PO-2040…PO-2708 (708 POs total). All bank/GSTIN/email details are fake.

---

## 8. Roles and permissions

Permission logic is centralised in `app.can(action, inv)` and also enforced in each POST route through `security.role_required` / `login_required`.

| Action | AP (`ap`) | Manager | Admin |
|---|---|---|---|
| Upload invoices | ✅ | ❌ | ✅ |
| Approve a **Pending** invoice | ❌ | ✅ (not their own upload) | ✅ (not their own upload) |
| Reject Pending | ❌ | ✅ (not own upload) | ✅ (not own upload) |
| Reject Flagged | ✅ | ✅ | ✅ |
| Send Flagged to manager | ✅ | ❌ | ✅ |
| Process again (Flagged/Pending) | ✅ | ❌ | ✅ |
| Ask the Investigator | ✅ | ✅ | ✅ |
| Record payment (Approved, ledger ready_to_pay) | ✅ | ❌ | ✅ |
| Run evaluations | ✅ | ❌ | ✅ |
| Create/edit POs, settings, users, regression baseline | ❌ | ❌ | ✅ |

**Segregation of duties:** whoever uploaded an invoice can't approve or reject it at the Pending stage. Attempts are audited as `segregation_of_duties / blocked`.

Demo users (password = `DEMO_PASSWORD`, default `ChangeMe!2026`): `prashasthi` and `ap_user2` (AP), `mokshana` (manager), `admin`.

---

## 9. Web routes (app.py)

| Area | Routes |
|---|---|
| Auth | `/login`, `/login/mfa`, `/auth/google`, `/auth/google/callback`, `/access-request/<token>`, `/logout`, `/healthz` |
| Dashboard | `/` (stats, needs attention, latest manager decisions, search and filters), `/api/feed`, `/api/palette` |
| Invoice | `/invoice/<id>` (problem cards, invoice vs PO table, PDF with red boxes, agent steps, timeline, investigation, emails), `/status`, `/file`, `/page/<n>.png`, POST `/approve`, `/reject`, `/send-to-manager`, `/reprocess`, `/email`, `/investigate`, `/record-payment` |
| Work queues | `/approvals` (manager), `/upload` (GET/POST, live steps), `/deadlines`, `/deadlines/payment-file.csv` |
| Data | `/pos`, `/pos/new`, `/pos/<po>/edit` (admin), `/vendors` (risk scores), `/analytics`, `/emails`, `/emails/<id>` (edit and send), `/audit`, `/audit.csv` |
| AI | `/ask`, `/api/ask`, `/api/help` |
| Quality & testing | `/evaluation` (+ `/api/eval/status`), `/redteam`, `/whatif`, `/compare` (+ `/api/compare/status`), `/regression` |
| Admin & account | `/settings` (rules, users, password reset, unlock, Reset 2FA, test email), `/account` (profile, appearance, security, activity tabs) |
| Notifications | `/notifications`, `/notifications/<id>/open`, `/api/notifications`, `/api/notifications/read` |

Each page has a matching template in `templates/`. The front end uses no inline scripts (blocked by CSP) and sets server text with `textContent` only. Toasts poll for notifications about every 15 s. Themes are light, dark or system, saved per account and applied before first paint by `theme.js`.

---

## 10. Security controls (`security.py` and elsewhere)

- **Accounts:** Werkzeug scrypt hashes; lockout after 5 failures for 15 minutes (MFA failures count too, admins alerted); identical error for unknown user and wrong password, with dummy-hash timing equalisation; sign-in by username or unique email.
- **Sessions:** server-side `user_sessions`; 30-minute idle timeout enforced on the server; new session on login; password change, deactivation or "sign out other sessions" take effect immediately; cookies HttpOnly, SameSite=Lax, Secure when `SESSION_COOKIE_SECURE=1`.
- **Google sign-in:** OIDC code flow with PKCE, state and nonce; issuer/audience/expiry/`email_verified` checked; linked by `sub`; optional domain allowlist; sign-ups created **inactive** until an admin approves.
- **MFA:** TOTP (±1 step drift, no code reuse), 10 hashed one-time recovery codes, secrets Fernet-encrypted with `MFA_ENCRYPTION_KEY`, compulsory per role with `MFA_REQUIRED_ROLES`.
- **Web:** CSRF token on every POST, parameterised SQL, auto-escaped templates, CSP / X-Frame-Options / nosniff, in-memory rate limits (login, upload, investigate 6/min), CSV-injection guard on exports, friendly error pages.
- **Uploads:** allowed `pdf, txt, png, jpg, jpeg`; real file-signature check; 10 MB per file; random stored names in private `data/uploads/` (never under `static/`); mismatched content is quarantined as unreadable.
- **AI:** injection-phrase regexes and hidden-text detection → `prompt_injection` flag; tool allowlist and argument validation; ledger guardrail; template-built emails; SELECT-only retrieval; output validation.
- **Data:** secrets in `.env`; bank accounts masked in UI and AI output; hash-chained audit log.
- **Production guards:** with `BEHIND_PROXY=1` the app refuses to start without `FLASK_SECRET_KEY`, refuses to seed users with the default demo password, warns about accounts still using it, and trusts exactly one proxy (`ProxyFix`).

---

## 11. Testing, evaluation and CI

```bash
pip install -r requirements-dev.txt
python generate_data.py                      # needed first: builds DB + test files
pytest -q                                    # SQLite; set TEST_DATABASE_URL to run on Postgres
python evaluate.py --mode offline            # test set + red team, no AI calls
python evaluate.py --save-baseline           # accept current decisions
python evaluate.py --check-regression        # exit 1 if a correct decision broke
python compare_ai.py                         # rules vs rules + AI (needs Groq, ~40-60k tokens)
```

| Suite | Test functions | Covers |
|---|---|---|
| `tests/test_security.py` | 30 | CSRF, headers, uploads, injection scanner, escaping, access control |
| `tests/test_auth.py` | 25 | Login, lockout, sessions, Google, MFA |
| `tests/test_ai_features.py` | 22 | Read-only retrieval, Investigator validation, assistant grounding, smart matching |
| `tests/test_rules.py` | 17 | `decide()` business rules |
| `tests/test_db_layer.py` | 10 | SQLite/Postgres abstraction |
| `tests/test_load_po_data.py` | 8 | Kaggle PO loader |

That's 112 test functions in total. The README's "85 tests" figure is out of date.

**Labelled datasets** (built by `generate_data.py` / `import_kaggle_pos.py`):
- `sample_invoices/`: 30 cases (clean match, over limit, small variance, wrong total, large mismatch, duplicate, missing PO, unknown vendor, corrupted file, PO not found) + `expected_outcomes.json`.
- `redteam_invoices/`: 18 attacks (A01–A18: hidden white text, fake assistant/tool messages, look-alike vendors, bank/IFSC changes, re-sent bills, inflated tax, hidden extra items, different GSTIN, split orders) + 3 setup invoices (S01–S03).
- Offline result on bundled data: 30/30 routed, 100% extraction, 3/3 duplicates, 18/18 attacks blocked.

**Trust pages in the UI:** Evaluation report, Red-team lab (replays the agent steps), What-if replay (re-runs `decide()` with new thresholds on stored `checks_json`; no AI, no writes), Decision regression, Rules vs AI.

**CI** (`.github/workflows/ci.yml`, on push and PR): job `test` (SQLite, `LLM_MODE=offline`, `DISABLE_WATCHER=1`) runs generate_data → pytest → evaluation with save-baseline + check-regression → bandit → pip-audit. Job `test-postgres` runs pytest against a Postgres 16 service.

---

## 12. Configuration (`.env`)

See [.env.example](.env.example) for the full annotated list. Key groups:

| Group | Variables |
|---|---|
| AI | `GROQ_API_KEY`, `GROQ_MODEL`, `LLM_MODE` (auto / extract_only / offline), `GROQ_REASONING_EFFORT` |
| App | `FLASK_SECRET_KEY`, `DEMO_PASSWORD`, `APP_BASE_URL`, `SESSION_COOKIE_SECURE`, `BEHIND_PROXY`, `SHOW_DEMO_HINT`, `PORT` |
| Email out | `EMAIL_MODE` (mock / smtp / brevo), `SMTP_*`, `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME`, `EMAIL_REDIRECT_TO` (test redirect) |
| Email in | `INBOX_IMAP_HOST/PORT/USER/PASSWORD/FOLDER`, `INBOX_POLL_SECONDS` |
| Google / MFA | `GOOGLE_CLIENT_ID/SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_ALLOWED_DOMAINS`, `GOOGLE_SIGNUP`, `MFA_REQUIRED_ROLES`, `MFA_ENCRYPTION_KEY` |
| Database | `DATABASE_URL` (empty = SQLite) |
| Testing | `DISABLE_WATCHER=1`, `TEST_DATABASE_URL` |

**Current local setup** (from the existing `.env`): `LLM_MODE=extract_only`, `GROQ_MODEL=openai/gpt-oss-120b`, `EMAIL_MODE=smtp`, and `DATABASE_URL` is set, so the app runs on **PostgreSQL** with the **Kaggle seed data**.

Emails are never sent automatically: a person presses **Send** on each draft. Subjects are forced to one line, and `.example` addresses are refused.

---

## 13. Running and deploying

**Local:**
```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                              # add GROQ_API_KEY and FLASK_SECRET_KEY
python generate_data.py && python app.py            # http://127.0.0.1:5000
```
On Postgres, run `python import_kaggle_pos.py` then `python load_po_data.py` instead of `generate_data.py`. `generate_data.py`, `import_kaggle_pos.py` and `migrate_sqlite_to_postgres.py --wipe` all **replace** ClearPay's tables in the target database.

**Production** ([deploy/DEPLOY.md](deploy/DEPLOY.md)):
- Route A: laptop + Cloudflare quick tunnel.
- Route B: Ubuntu VPS with gunicorn under systemd (`deploy/clearpay.service`), Caddy for automatic HTTPS (`deploy/Caddyfile`), daily `deploy/backup.sh` cron (14 days kept).
- Route C: Render (free disk is wiped on sleep; use `EMAIL_MODE=brevo` because SMTP ports are blocked).

**Run exactly ONE worker process** (`gunicorn --workers 1 --threads 4 wsgi:app`). The agent queue, Investigator queue, inbox watcher, IMAP poller and rate limits all live in memory. `data/` must be on persistent disk. `/healthz` returns `ok`.

---

## 14. Background threads

| Thread | Started by | Job |
|---|---|---|
| `agent-worker` | `agent.submit()` (lazy) | Processes invoices one at a time from the queue (SQLite prefers a single writer) |
| Investigator worker | `investigator.submit()` | Runs investigations separately so uploads never wait |
| Inbox watcher | `startup()` → `watcher.start()` | Polls `invoices_inbox/`; moves files to `processed/` or `rejected/` |
| IMAP poller | `startup()` → `email_inbox.start()` | Only if `INBOX_IMAP_*` is set; reads unread mail every `INBOX_POLL_SECONDS` |
| Eval / compare runners | `/evaluation`, `/compare` POST | Run on throwaway DBs via `db.using()` |

`DISABLE_WATCHER=1` skips the watcher and IMAP poller (used in CI and tests).

---

## 15. Conventions and gotchas for contributors

- **Never let AI output decide or write.** New AI features must go through `retrieval.py` (SELECT-only) and save only recommendations or drafts. Keep a rules fallback.
- **Keep `decide()` pure.** What-if replay and the regression gate depend on it being deterministic from `checks_json` + settings.
- **Every state change writes an audit row** via `db.log_audit()` (it's hash-chained under a lock).
- **Business thresholds belong in `settings`** (`DEFAULT_SETTINGS`), not hard-coded. `set_setting` rejects unknown keys.
- **Schema changes:** add to `SCHEMA` and add an `ALTER TABLE … ADD COLUMN` upgrade in `init_db()` so older databases upgrade on start. Keep SQL portable (SQLite + Postgres, `?` placeholders).
- **Front end:** no inline scripts or `innerHTML` with server data (CSP + XSS rules); respect `prefers-reduced-motion`.
- **Money:** INR, rounded with `db.money()` (Decimal, ROUND_HALF_UP); maths tolerance is ₹0.011. Times are stored in UTC ISO and shown in IST 24-hour.
- **Windows encoding:** files contain UTF-8 (₹). Don't rewrite them with PowerShell 5.1 `Get-Content`/`Set-Content`, which garble it; use an editor or Python. Set `PYTHONIOENCODING=utf-8` when printing from scripts.
- **Bandit** runs in CI with `-ll`; intentional exceptions are marked `# nosec` with a reason.

### Known documentation drift

- README says "85 tests"; there are 112 test functions now.
- `deploy/POSTGRES.md` says there is no screen to add or edit a PO, but admins can now do this (`/pos/new`, `/pos/<po>/edit`). PO edits are admin-only and audited.
- `deploy/DEPLOY.md` says "no two-factor sign-in", but TOTP MFA and Google sign-in now exist (`mfa.py`, `google_auth.py`).

### Not built (by design or not yet)

Payments (by design), password reset by email (admins reset passwords), profile photos (initials are used), Redis-backed rate limits, encryption at rest, multi-worker deployment.

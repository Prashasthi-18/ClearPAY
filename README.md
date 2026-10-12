# ClearPay: Invoice-to-Payment AP Automation Agent

An AI agent that processes vendor invoices end to end. It reads each invoice (PDF, text or scan), checks it against the original purchase order, and decides **approve / send to manager / flag / reject**. It writes a ledger entry, drafts the emails, and logs every step with its reason. People only handle the exceptions.

**It never makes payments, and the AI never makes a payment decision.** ClearPay prepares ledger entries; a person approves, and a person records the payment made outside ClearPay. No payment code exists in the project.

Built with Python, Flask, SQLite and the Groq API (`openai/gpt-oss-120b` with tool calling).

---

## AI on the invoice side, with people deciding

The invoice side is where the AI is used, because that's where documents are messy. Purchase orders are controlled records with no AI involved.

```
AI:     investigates → reasons → matches → retrieves → explains → recommends
Code:   validates → calculates → enforces the rules
People: review → decide → approve, and record the payment
Admin:  creates and edits purchase orders
```

**The AI never makes a payment decision.** This is built into the structure, not just the prompt:

- The AI features can only reach data through `retrieval.py`. Every query there must be a single `SELECT`, and anything else is refused. None of the AI tools can approve, reject, pay, record a payment or edit a PO, so a hallucinating or manipulated model has nothing to press. Tests check this.
- The Investigator saves a *recommendation* (table `investigations`) and an email *draft*. It never touches the invoice status or the ledger.
- An invoice that needed an AI item match always goes to a person ("AI matched N item(s)… a person must confirm"). It is never approved automatically.
- Payment happens outside ClearPay. Only a person (AP or admin) can mark an approved invoice as paid, with **Record payment**.
- Code checks what the AI returns. Recommendations come from a fixed vocabulary. Fraud signs can never get an "approve" recommendation (code overrides it). Bank numbers are masked. Ask ClearPay answers that mention an invoice or PO number that wasn't retrieved are thrown away and replaced by a plain answer built from the data.
- **Fallback:** when there's no key, the mode is offline, or Groq is rate-limited or down, every AI feature switches to rules. Matching uses the old name rule, the Investigator runs a rule-based investigation (marked as such), and Ask ClearPay and plain-English search answer straight from the records. Invoices are still read, checked, flagged and reviewed.

### The features

| Feature | Where | How it works |
|---|---|---|
| **Investigator agent** | Invoice page → *Investigation* | Runs automatically on every flagged invoice (Settings can switch that off), or on request for flagged and pending invoices. It is a tool-calling agent: `get_invoice` → `get_purchase_order`, `get_vendor_history`, `find_similar_cases` (past decisions and managers' comments) → recommends **Approve / Reject / Ask the vendor** with evidence → drafts a vendor email about that specific problem. The steps are shown and written to the audit log. The agreement rate with people's decisions appears in Analytics. |
| **Smart matching** | Invoice vs PO table ("AI match") | Code pairs identical names first, with no AI call. Only differently-worded lines go to the AI, in one call per invoice. The AI answers which PO line each one is and how the units convert ("box of 10 reams" → 10). Code then checks the converted quantity, the converted price, the amounts and the variance. |
| **Ask ClearPay** | Sidebar → *Ask ClearPay*, or "Ask about this invoice" | A chat over 7 read-only tools (invoice, search, vendor history, PO, similar cases, deadlines, analytics). It answers only from what it retrieved, says when something isn't available, and can't act. |
| **Payment deadlines** | Sidebar → *Payment deadlines*, dashboard strip, invoice facts | The due date is worked out in code: the printed due date, then the printed payment terms, then the default terms. MSME suppliers are capped at 45 days. Labels read "Due in 5 days", "Due tomorrow", "Overdue by 3 days". ClearPay never pays; a person records the payment. |
| **Analytics** | Sidebar → *Analytics* | Spend (by vendor and month), fraud caught (by type, ₹ stopped), money saved (duplicates, fraud, overcharges, staff time; each invoice counted once), and the AI's part. |
| **Email inbox pickup** | `.env` → `INBOX_IMAP_*` | Reads unread mail over IMAP, takes invoice attachments (or an invoice typed in the body), applies the same file checks as an upload, then runs the normal processing. Shown as "arrived by email from …". |
| **Vendor risk score** | Sidebar → *Vendors*, badge on each invoice | A 0–100 score from the vendor's history: duplicates, bank changes, PO mismatches, price differences, rejections. It is smoothed so one bad invoice doesn't make a vendor look high-risk, and each factor is listed. It is information only. |
| **Ask in plain English** | Dashboard → *Ask in plain English* | Plain rules turn "flagged invoices from Alpha over ₹50,000" into filters, shown as chips. The AI is asked only when the rules understand nothing, and even then it only fills in a filter form that code validates. |
| **Help guide** | `?` in the top bar | Explains pages, statuses and where things are, from fixed help topics. It has no data access and no tools. |
| **Rules vs AI** | Admin → Quality & testing → *Rules vs AI* | 20 tricky invoices processed twice: rules alone, then rules + AI (see below). |

### Purchase-order permissions

| Role | Purchase orders |
|---|---|
| Admin | View, **create** (*New purchase order*), **edit** (lines, date, GST, open/closed). Totals are worked out in code and every change is audited. |
| AP team, manager | View only. The create and edit routes return 403. |
| AI features | Read only, through `retrieval.py`. |

### Protecting the Groq free plan

Deterministic rules always run first. The AI is called only where it's needed:

- reading the invoice (`LLM_MODE=extract_only`, about 1k tokens);
- smart matching, only when a line's name doesn't match a PO line (about 1k tokens);
- the Investigator, only on flagged invoices (a few thousand tokens, tool results trimmed);
- chat and help, only when someone asks.

Evaluation runs skip the Investigator, and investigations run in their own queue so uploads never wait for them.

### Proof: rules alone vs rules + AI

`python compare_ai.py` (or *Run comparison* on the *Rules vs AI* page, admin only) processes 20 tricky invoices twice on throwaway databases. Both arms read invoices with the same rule-based parser, so the only difference is AI smart matching and the AI Investigator. The cases cover different wording, different units (boxes, dozens, tonnes), partial deliveries, look-alike product names, and history cases where managers' earlier comments decide the right answer. Expected outcomes were fixed before running. The AI arm uses roughly 40–60k Groq tokens.

## What's new in the ClearPay interface

- **Approval notifications for the AP team.** When a manager approves or rejects an invoice, every AP user (and the uploader) gets a notification: in the bell (with unread count), as a bottom-right toast within about 15 seconds on any page, on the dashboard ("Latest manager decisions", marked *New*), and on the invoice itself. Opening the invoice marks it read and records "Seen by …" in its timeline.
- **"What happened" timeline** on every invoice: uploaded, checked, sent to a manager, approved by whom and when, AP team notified, seen by whom. All times in IST, 24-hour (`14:32 IST`).
- **AP-focused dashboard:** needs AP review, waiting for a manager, ready for payment, est. money saved (₹), plus "Needs your attention" and "Latest manager decisions". Search by invoice number, vendor, PO or amount; filter by status, vendor and invoice date.
- **Indian rupee formatting everywhere** (₹1,25,000.00; headline figures as ₹5.4L / ₹1.2Cr). Groq's own cost stays in USD because Groq bills in USD.
- **Light, dark and system themes** (Account → Appearance), saved to the account so they follow you to any browser, applied before first paint (no flash).
- **Account pages** from the avatar menu: profile (name, email, department), appearance, security (change password, active sessions with "sign out" / "sign out of all other sessions", security alerts) and sign-in activity (sign-ins, sign-outs, failed attempts, lockouts, with browser, system and network address).
- **Real sessions:** each sign-in is recorded on the server; signing out, ending a session elsewhere, a password change or deactivation takes effect immediately, and the 30-minute idle timeout is enforced on the server too.
- **Admin password reset** (Settings & users) backs the "Forgot password?" link on the sign-in page. Reset-by-email isn't built, and the page says so.
- **Payment file export.** *Payment deadlines → Payment file (CSV)* (AP team and admin) lists every invoice that is ready for payment, with the vendor's GSTIN, bank account, IFSC, MSME flag and due date, ready to upload to the bank. The export is written to the audit log. ClearPay still never pays: a person uploads the file and then records each payment.
- **Continue with Google** (sign-in, account linking and access requests that an admin approves) and **two-factor authentication** with an authenticator app plus one-time recovery codes. It can be made compulsory per role with `MFA_REQUIRED_ROLES`. Setup steps: [deploy/AUTH_SETUP.md](deploy/AUTH_SETUP.md).
- **Not built, and shown as such:** profile photos (initials are used), payments (ClearPay never pays; "Ready for payment" is the last state).
- Branding: original ClearPay logo in `static/brand/` (full logo for light and dark, mark, favicon SVG/PNG, 180 px app icon). Icons: [Lucide](https://lucide.dev) (ISC licence), bundled as `static/icons.svg`. Motion respects `prefers-reduced-motion`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                          # versions are pinned; moved the folder? delete .venv and redo these two lines
cp .env.example .env                                     # Windows: copy .env.example .env
#   then put your GROQ_API_KEY and a FLASK_SECRET_KEY in .env
python generate_data.py && python app.py
```

Open http://127.0.0.1:5000 and sign in.

| Username | Role | Can do |
|---|---|---|
| `ap_user`, `ap_user2` | AP team | Upload, review flagged invoices, send to manager, reject flagged, run evaluations |
| `manager` | Manager | Approve or reject pending invoices |
| `admin` | Admin | Everything, plus rules, users and the regression baseline |

The password for all four is `DEMO_PASSWORD` from `.env` (default `ChangeMe!2026`; change it).

**No Groq key?** The app still runs in *offline rule-based mode*. A built-in parser reads the generated test invoices, and a fixed orchestration replaces the LLM. Real-world invoices need Groq.

**About the data:** vendors, purchase orders and test invoices are synthetic, made by `generate_data.py` (POs can also be loaded from the public Kaggle *Procurement KPI Analysis* dataset with `import_kaggle_pos.py`). No real company's data is included.

**Scanned images (optional):** install the [Tesseract](https://github.com/tesseract-ocr/tesseract) binary for OCR of PNG/JPG and scanned PDFs. Without it, those files are flagged as unreadable and a resend request is drafted.

### Try it

1. Sign in as `ap_user` and go to **Upload invoice**. Drag in files from `sample_invoices/` or `redteam_invoices/` and watch each agent step appear live.
2. Open a flagged invoice to see **where it went wrong**: problem cards, invoice vs PO table, the original PDF with red boxes on the bad values, the agent's reasoning, and the drafted emails.
3. Sign in as `manager` and go to **Approvals**. Approve one invoice, and the agent drafts the "approved" email to the AP team.
4. Or copy files into `invoices_inbox/`. The watcher picks them up within a few seconds (`python generate_data.py --fill-inbox 8` does this for you).
5. Go to **Evaluation report** and press **Run evaluation**. Then look at the **Red-team lab**, **What-if replay** and **Decision regression** pages.

---

## How it works

```
 invoices_inbox/ ──watcher──┐
                            ├──► invoice record ──► queue ──► agent worker
 Upload page (AP team) ─────┘                                     │
                                                                  ▼
          ┌──────────────────────── Groq gpt-oss-120b (tool calling) ─────────────────────────┐
          │  chooses the next tool:                                                          │
          │  extract_invoice_data → check_vendor → check_duplicate → validate_math → lookup_po │
          │  → write_ledger_entry | escalate_to_human, draft_email                           │
          └──────────────────────────────────────────────────────────────────────────────────┘
                                                                  │  tool calls (args validated)
                                                                  ▼
                tools.py: every number, comparison and rule runs in Python code
                                                                  │
                                                                  ▼
       finalize(): runs any check the LLM skipped → decide() → ledger → emails → report → audit log
                                                                  │
                                                                  ▼
          Dashboard · Invoice report · Approvals (manager) → "approved" email to AP team
```

**The key design rule:** the LLM *reads* documents and *chooses the order* of tools. Code *does the maths and makes the decision*. Tools use data stored in Python, not numbers the LLM passes in, and `write_ledger_entry` is refused unless the code checks passed. A manipulated model therefore can't approve a bad invoice.

### Decision rules (code, in `tools.decide`)

| Situation | Result |
|---|---|
| Exact PO match, maths correct, total under ₹5,000 | ✅ **Approved** (ledger: ready to pay) |
| Variance from PO under 5%, total over ₹5,000, AI confidence under 0.7, or a possible split invoice | ⏳ **Pending** manager |
| Unknown or look-alike vendor, bank or GSTIN change, missing or unknown PO, wrong maths, big mismatch, item not on PO, re-sent invoice, PO used up, prompt injection, unreadable file | 🚩 **Flagged** with reasons |
| Exact duplicate | ❌ **Rejected** |

The limits live in the `settings` table, and admins change them on the **Settings** page.

### Emails (drafted and saved to the database; sending is mocked)

| Email | When |
|---|---|
| Approval request → manager | An invoice is pending |
| **Approved / rejected notice → AP team** | Right after a manager decides |
| Differences found / missing info / please resend → vendor | The vendor needs to fix something |
| Needs review → AP team | Fraud signals (the vendor is *not* emailed) |
| Check report → any address | Entered on upload, or from the invoice page |

Vendor emails always go to the address **on file**, never one found in the invoice text.

---

## Project structure

```
app.py             Flask routes, permissions, approval flow, account pages, trust & testing pages
notifications.py   In-app notifications: who is told about which invoice event
smart_match.py     AI-assisted invoice-line ↔ PO-line matching (units, wording); code checks the numbers
investigator.py    Investigator agent for flagged invoices (read-only tools → recommendation + email draft)
retrieval.py       The ONLY data access for AI features: read-only, SELECT-only tools
assistant.py       Ask ClearPay chat (grounded, with a hallucination check) + plain-English search
helpbot.py         Help guide for new users (fixed help topics, no data access)
insights.py        Payment deadlines, vendor risk score, analytics (all code, no AI)
email_inbox.py     IMAP mailbox pickup of invoices
compare_ai.py      Rules alone vs rules + AI on 20 tricky invoices
agent.py           Groq tool-calling loop, argument validation, guardrails, finalize(), worker queue
tools.py           The 8 tools, extraction, maths/GST/PO checks, decision rules, emails, PDF highlights
db.py              SQLite schema, seed data, settings, hash-chained audit log
security.py        Lockout, role checks, CSRF, rate limits, headers, upload checks, injection scanner
watcher.py         Background thread for invoices_inbox/
mailer.py          Real email sending over SMTP (optional), test-mode redirect
generate_data.py   Seed DB + 30 labelled test invoices + 18 red-team attacks
evaluate.py        Accuracy, routing, duplicates, red-team, regression gate
templates/, static/  Bootstrap UI (bundled locally, works offline)
tests/             pytest: business rules + security controls
regression_baseline.json   Approved decisions; CI fails if one breaks
.github/workflows/ci.yml   Tests, evaluation gate, bandit, pip-audit
```

**Database tables:** `vendors`, `purchase_orders`, `po_lines`, `invoices`, `ledger`, `audit_log`, `emails`, `users`, `settings`, `investigations`, `inbox_messages`. New columns are added to older databases automatically on start.

---

## Features that set it apart

- **Red-team lab:** 18 attack invoices (hidden white-text prompt injection, fake "assistant" messages, look-alike vendors like "Acrne Corp", changed bank details and GSTIN, re-sent bills, math traps, one order split into two invoices to dodge the limit). The page shows which were blocked and replays the agent's steps.
- **What-if policy replay:** change the limit, tolerance or confidence threshold and replay past invoices (or the labelled test set) to see which decisions change. It makes no AI calls and writes nothing.
- **Decision regression:** snapshot testing for the agent. After changing the prompt, model or rules, it shows which decisions changed and whether each is a fix or a new bug. `--check-regression` fails CI.
- **"Where it went wrong" report:** plain-English problem cards, invoice vs PO table, and the original PDF with red boxes on the wrong values.
- **Indian GST checks:** GSTIN format, CGST = SGST, and the tax rate compared to the PO.
- **Cost and speed:** tokens, estimated Groq cost and time per invoice.

---

## Security

| Area | Controls |
|---|---|
| Accounts | Hashed passwords (Werkzeug scrypt), lockout after 5 failures for 15 min, same error for wrong user or password, 30-min idle timeout, new session on login, every sign-in logged |
| Access | Role checks on every route on the server; **segregation of duties** (whoever uploaded an invoice can't approve it); admin-only rules and users |
| Web | CSRF token on every POST, parameterised SQL, auto-escaped templates, no inline scripts, CSP / X-Frame-Options / nosniff headers, rate limits on login and upload, friendly error pages, CSV-injection guard on export |
| Uploads | Type checked by real file signature, 10 MB limit, random stored names, private folder (never under `static/`), mismatched content quarantined as unreadable |
| AI | Invoice text marked as untrusted data; code scanner for injection phrases and hidden text; JSON output validated; tool allowlist; every argument checked; ledger guardrail; agent emails built from templates |
| Data | Secrets in `.env` (git-ignored), bank accounts masked, **tamper-evident audit log** (each row hashes the previous; the Audit page shows "Log integrity: verified") |
| Testing | `pytest` access-control and rule tests, red-team suite, `bandit` and `pip-audit` in CI |

**Before production:** serve over HTTPS (and set `SESSION_COOKIE_SECURE=1`), use Postgres instead of SQLite, add 2FA/SSO, encrypt data at rest, move rate limits to Redis, send emails through a real provider with approval, and run behind a WSGI server (gunicorn). This is a hackathon-grade build.

---

## Testing and evaluation

```bash
pip install -r requirements-dev.txt
pytest -q                                   # 85 tests (on SQLite or PostgreSQL)
python compare_ai.py                        # rules alone vs rules + AI on 20 tricky invoices (needs Groq)
python evaluate.py                          # test set + red-team (uses Groq if a key is set)
python evaluate.py --mode offline           # no AI calls
python evaluate.py --save-baseline          # accept current decisions as the baseline
python evaluate.py --check-regression       # exit 1 if a previously correct decision broke
```

Offline results on the bundled data: **30/30 routed correctly, 100% extraction, 3/3 duplicates, 18/18 attacks blocked.** Run it with your Groq key to measure the real model. Its extraction may differ from the offline parser, and that's what the evaluation is for.

## Sending real emails

Two ways: `EMAIL_MODE=smtp` (an SMTP server such as Gmail) or `EMAIL_MODE=brevo` (Brevo's email API over HTTPS, which works where SMTP ports are blocked, for example Render's free plan; see `deploy/DEPLOY.md`). The SMTP steps are below.

By default emails are only drafted and marked as sent (`EMAIL_MODE=mock`). To really send them when someone presses **Send**:

1. Gmail: turn on 2-Step Verification, then create an App Password at https://myaccount.google.com/apppasswords.
2. In `.env`: `EMAIL_MODE=smtp`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` = your Gmail,
   `SMTP_PASSWORD` = the 16-character App Password, `MAIL_FROM` = your Gmail.
3. While testing, set `EMAIL_REDIRECT_TO` = your own address: every email goes to you, marked with who it was meant for.
4. Restart the app. As admin, open **Settings**, set the manager, AP team and vendor addresses, and press **Send test email**.
5. Remove `EMAIL_REDIRECT_TO` to go live.

Emails are never sent automatically. Placeholder addresses (`*.example`) are refused with a clear message, failures are
shown on the email and in the audit log, and subjects are forced to one line (no header injection).

## Database: SQLite or PostgreSQL

SQLite (the file in `data/`) is the default. To use PostgreSQL instead, set `DATABASE_URL` in `.env`, then run
`python import_kaggle_pos.py` (tables, vendors, PO-2001 to PO-2039, users, test invoices) and `python load_po_data.py`
(the other Kaggle rows as PO-2040 onward, 708 POs in all). Purchase orders are controlled master data: besides the
loader, only an admin can create or edit them in the app (*Purchase orders → New purchase order / Edit*). To copy invoices you already uploaded, use
`python migrate_sqlite_to_postgres.py --wipe`. Steps for Windows and Docker, queries to look at the data, backups and
troubleshooting are in `deploy/POSTGRES.md`.

## Deploying to a server

See `deploy/DEPLOY.md` for the full steps: a laptop demo through a Cloudflare tunnel, or a small Linux server with
gunicorn, systemd, automatic HTTPS (Caddy), daily backups and a troubleshooting table. Key rules: run **one**
worker (`gunicorn --workers 1 --threads 4 wsgi:app`), keep `data/` on a disk that survives restarts, always use HTTPS,
and set `BEHIND_PROXY=1`, `SESSION_COOKIE_SECURE=1`, `SHOW_DEMO_HINT=0`, your own `FLASK_SECRET_KEY` and `DEMO_PASSWORD`.
With `BEHIND_PROXY=1` the app refuses to start without a secret key and refuses to create accounts with the built-in
demo password. `GET /healthz` answers `ok` for uptime monitors.

## Groq free plan limits

Groq's free plan allows about 8,000 tokens a minute and 200,000 a day for `openai/gpt-oss-120b` (per organisation; the
limits page in the Groq console is authoritative). In full agent mode one invoice used roughly 6,000 to 11,000 tokens,
so a few uploads in a row hit "429 rate limit". Set `LLM_MODE=extract_only` in `.env`: the AI then only reads the invoice
(about 1,000 tokens) and code runs the checks in a fixed order. The agent log shows which limit was hit and how long Groq
asks to wait; short waits are followed automatically, long ones (the daily limit) stop at once and the invoice goes to
a person, marked "The AI didn't answer".

## Troubleshooting

- **"offline rule-based mode" in the sidebar:** `GROQ_API_KEY` is missing from `.env`.
- **Groq errors or rate limits:** the agent retries 3 times, then falls back to rule-based orchestration and logs it. The invoice is never left stuck.
- **"Your form expired":** the CSRF token is tied to your session. Reload the page.
- **Reset everything:** `python generate_data.py` rebuilds the database and test files.

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="static/brand/clearpay-logo-dark.svg">
  <img src="static/brand/clearpay-logo-light.svg" alt="ClearPay" height="64">
</picture>

### AI-powered invoice checking for accounts payable teams

ClearPay reads vendor invoices, checks them against the purchase order, catches fraud and mistakes,<br>
and sends only the exceptions to a person.

[![Live demo](https://img.shields.io/badge/Live_demo-clearpay--wk3j.onrender.com-2ea44f?style=for-the-badge&logo=render&logoColor=white)](https://clearpay-wk3j.onrender.com)

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)
![SQLite / PostgreSQL](https://img.shields.io/badge/SQLite_%7C_PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-gpt--oss--120b-F55036)
![Red team](https://img.shields.io/badge/red--team-18%2F18_blocked-brightgreen)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/dashboard-dark.png">
  <img src="docs/screenshots/dashboard.png" alt="ClearPay dashboard" width="900">
</picture>

</div>

---

## What it does

1. **Reads** each invoice: PDF, text, scan, inbox folder or email.
2. **Checks** it in code: vendor, duplicates, maths, GST, PO lines, bank-detail changes, look-alike names and hidden
   prompt injection.
3. **Decides** the route: ✅ approve · ⏳ send to a manager · 🚩 flag · ❌ reject.
4. **Explains** every decision in plain words, with the problem boxed in red on the original document.

> [!IMPORTANT]
> **ClearPay never pays, and the AI never makes a payment decision.** The AI reads and recommends, code enforces the
> rules, and a person approves and records the payment.

---

## Screenshots

**Every flagged invoice explains what went wrong**, with the Investigator's recommendation:

<p align="center"><img src="docs/screenshots/invoice-problems.png" alt="Where it went wrong and Investigation" width="800"></p>

**Invoice vs purchase order**, the original document with the problem boxed in red, and every agent step:

<p align="center"><img src="docs/screenshots/invoice-vs-po.png" alt="Invoice vs PO, highlighted original, agent steps" width="800"></p>

<table>
<tr>
<td width="50%"><img src="docs/screenshots/approvals.png" alt="Manager approvals"></td>
<td width="50%"><img src="docs/screenshots/ask.png" alt="Ask ClearPay chat"></td>
</tr>
<tr>
<td align="center"><sub><b>Approvals:</b> managers approve or reject in one place</sub></td>
<td align="center"><sub><b>Ask ClearPay:</b> answers only from the records</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/analytics.png" alt="Analytics"></td>
<td width="50%"><img src="docs/screenshots/redteam.png" alt="Red-team lab"></td>
</tr>
<tr>
<td align="center"><sub><b>Analytics:</b> spend, fraud stopped, money saved</sub></td>
<td align="center"><sub><b>Red-team lab:</b> 18 of 18 attacks blocked</sub></td>
</tr>
</table>

---

## Key features

- 🤖 **Investigator agent:** looks into flagged invoices and recommends approve, reject or ask the vendor, with evidence.
- 🔗 **Smart matching:** matches differently worded lines and units ("box of 10 reams") to the PO; code checks the numbers.
- 💬 **Ask ClearPay:** a chat over read-only data that can't take any action.
- 📅 **Payment deadlines**, 📊 **analytics** and 🏷️ **vendor risk scores**, all worked out in code.
- 🛡️ **Red-team lab, what-if replay and decision regression** for testing the agent.
- 🔐 **Secure by design:** role-based access, segregation of duties, 2FA, Google sign-in and a tamper-evident audit log.
- 📴 **Works offline:** without a Groq key, every AI feature falls back to rules.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # add GROQ_API_KEY and FLASK_SECRET_KEY
python generate_data.py && python app.py
```

Open **http://127.0.0.1:5000** and sign in with the `DEMO_PASSWORD` from `.env`:

| Username | Role |
|---|---|
| `prashasthi` | AP team: uploads and reviews invoices |
| `mokshana` | Manager: approves or rejects |
| `admin` | Admin: settings, users, purchase orders |

> [!TIP]
> No Groq key? The app still runs in offline rule-based mode on the bundled demo data.

---

## How it works

```
Invoice ──► AI reads it ──► code checks it ──► decision ──► ledger, emails, audit log
                                                  │
                                                  └─► exceptions go to a person
```

| Situation | Result |
|---|---|
| Exact PO match, maths correct, small amount | ✅ **Approved** |
| Over the limit, small PO difference or low confidence | ⏳ **Sent to a manager** |
| Fraud signs, mismatches, missing PO, unreadable file | 🚩 **Flagged** |
| Exact duplicate | ❌ **Rejected** |

The AI only reads documents and recommends. It has no tool that can approve, reject or pay.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q                          # 85 tests
python evaluate.py --mode offline  # accuracy + red-team, no AI calls
```

**Offline results:** 30/30 invoices routed correctly · 3/3 duplicates caught · 18/18 attacks blocked.

---

## More docs

- [deploy/DEPLOY.md](deploy/DEPLOY.md): deploying to a server or Render
- [deploy/POSTGRES.md](deploy/POSTGRES.md): using PostgreSQL
- [deploy/AUTH_SETUP.md](deploy/AUTH_SETUP.md): Google sign-in and 2FA
- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md): full technical reference

<div align="center">

<br>

**[🌐 Try the live demo →](https://clearpay-wk3j.onrender.com)**

<sub>The demo is on Render's free plan, so the first load can take up to a minute.</sub>

</div>

# ClearPay demo script

## The day before

- [ ] `python -m pytest -q` passes.
- [ ] `.env` has a working `GROQ_API_KEY`, `LLM_MODE=extract_only` (free plan) and `EMAIL_MODE=mock`.
- [ ] Run `python compare_ai.py` once with the key (about 40-60k Groq tokens, so not on demo day) so the **Rules vs AI** page has real numbers.
- [ ] Download 3 or 4 real invoice PDFs from the internet and upload them. Note which ones read well.
- [ ] Do one full run-through on the demo laptop, on the Wi-Fi you will have there.
- [ ] Record a backup video of the full run-through in case Groq is slow or rate-limited on the day.
- [ ] Fresh data: `python generate_data.py`, then start `python app.py`.

## The 3-minute story

One problem, one flow, one proof. Skip the other pages unless asked.

1. **The problem (20 s).** AP teams check every invoice against its purchase order by hand. Mistakes and fraud (duplicates, changed bank details, overcharges) slip through, and small suppliers get paid late.
2. **Upload (40 s).** Sign in as `prashasthi`, go to **Upload invoice** and drop in one clean invoice and one from `redteam_invoices/`. The agent steps appear live.
3. **The catch (50 s).** Open the flagged invoice: problem cards, invoice vs PO table, the PDF with red boxes on the bad values, the Investigator's recommendation with evidence, and the drafted vendor email.
4. **People decide (30 s).** Send it to the manager. Sign in as `mokshana`, approve or reject in **Approvals**, and show the AP notification.
5. **Ready to pay (20 s).** Go to **Payment deadlines** and show the due dates (MSME capped at 45 days). Press **Payment file (CSV)**, the file for the bank.
6. **Proof (20 s).** Show **Rules vs AI** and **Analytics** (fraud caught, money saved).

## Questions to expect

| Question | Answer |
|---|---|
| Why doesn't it pay automatically? | On purpose. The AI never makes a payment decision; it has no tool that can. A person approves, uploads the payment file to the bank and records the payment. That's what auditors and finance teams expect. |
| What if the AI is wrong or hallucinates? | The AI can only read, through `SELECT`-only queries. Code checks the maths, amounts and variance. Fraud signs can never get an "approve" recommendation, and AI-matched lines always go to a person. |
| What if Groq is down? | Every feature falls back to rules. Invoices are still read, checked and flagged. |
| Is the data real? | No. It's synthetic, made by `generate_data.py`; POs can come from a public Kaggle dataset. |
| Does it scale? | SQLite for the demo, with a tested PostgreSQL path (`deploy/POSTGRES.md`) and gunicorn for production. |
| Is it India-only? | It's built for India first (₹, GST, MSME 45-day rule). Tax and payment-term rules live in settings and code, not the AI, so other countries mean new rules, not a new design. |
| How do you know the AI helps? | **Rules vs AI** runs 20 tricky invoices twice, with expected outcomes fixed beforehand. |

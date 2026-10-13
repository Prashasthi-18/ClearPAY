# Using PostgreSQL with ClearPay (on your laptop)

**The idea:** the vendors and purchase orders (POs) live in PostgreSQL as master data. The AP team uploads invoices in
ClearPay, and every invoice is checked against the POs in the database. ClearPay only **reads** POs: there is no screen to
add or edit one. POs get into the database with a separate loader, `load_po_data.py`.

Nothing changes unless you set `DATABASE_URL`. Without it, ClearPay keeps using the SQLite file in `data/`.

**Tested** (PostgreSQL 16 on Linux): all 63 automated tests pass on both SQLite and PostgreSQL; the 22 attack-test invoices give
the expected result on PostgreSQL; the evaluation gives the same result on both (30/30 routed, 3/3 duplicates, 18/18 attacks
blocked, no regressions); the loader puts 5 vendors and 708 POs into an empty database, and PO-2001 to PO-2039 are identical
to the ones your test invoices were built from. The Purchase orders page loads in about 40 ms with all 708 POs.
**Not tested:** Windows, Docker Desktop and hosted PostgreSQL services. The steps below are the standard ones.

## 1. Get PostgreSQL running (choose A or B)

**A. Docker Desktop (quickest)**
```powershell
docker run -d --name clearpay-pg -e POSTGRES_USER=clearpay -e POSTGRES_PASSWORD=ChangeThisPassword -e POSTGRES_DB=clearpay -p 5432:5432 -v clearpay-pgdata:/var/lib/postgresql/data postgres:16
```
Check it is up: `docker ps` should list `clearpay-pg`. Next time: `docker start clearpay-pg`.

**B. The Windows installer** (postgresql.org, includes pgAdmin). Open pgAdmin, then Query Tool (or `psql`), and run:
```sql
CREATE USER clearpay WITH PASSWORD 'ChangeThisPassword';
CREATE DATABASE clearpay OWNER clearpay;
```

## 2. Install the driver
```powershell
pip install -r requirements.txt
```

## 3. Tell ClearPay where the database is
In `.env`:
```
DATABASE_URL=postgresql://clearpay:ChangeThisPassword@localhost:5432/clearpay
DEMO_PASSWORD=choose-your-own-password
```
If the password has special characters, encode them: `@` is `%40`, `#` is `%23`, `/` is `%2F`, `:` is `%3A`.

## 4. Put the data in
Keep the Kaggle file `Procurement_KPI_Analysis_Dataset-selected-columns.csv` in the project folder. Then run these **two**
commands, in this order:

```powershell
python import_kaggle_pos.py
python load_po_data.py
```

| Command | What it does |
|---|---|
| `import_kaggle_pos.py` | Creates ClearPay's tables in PostgreSQL, the 5 vendors, **PO-2001 to PO-2039**, the 4 users, and writes the test invoice files. It **replaces** ClearPay's tables in that database. |
| `load_po_data.py` | Adds every other usable Kaggle row as **PO-2040 to PO-2708** (669 more). Safe to run again: POs that exist are skipped. It never touches invoices, the ledger or the audit log. |

You should see `Now in the database: 5 vendors, 708 purchase orders.`

Handy options: `python load_po_data.py --dry-run` shows what would be loaded and changes nothing, and `--set-limit`
sets the auto-approve limit to Rs 50,000 (the import already does this).

You can also run `load_po_data.py` on its own against an empty database: it creates the vendors, all 708 POs and the 4 users.

## 5. Start ClearPay
```powershell
python app.py
```
The sidebar shows **Database: PostgreSQL**. Sign in as `prashasthi` and upload invoices as usual. The **Purchase orders** page
lists the first 100 POs and has a search box (PO number or vendor) for the rest.

## 6. See the data in PostgreSQL
pgAdmin: Servers, clearpay, Schemas, public, Tables. Or run these in the Query Tool or psql
(Docker: `docker exec -it clearpay-pg psql -U clearpay -d clearpay`):
```sql
SELECT count(*) FROM purchase_orders;
SELECT v.name, count(*) AS pos, round(sum(p.total)::numeric, 2) AS value FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id GROUP BY v.name;
SELECT id, invoice_number, vendor_name, po_number, status, total FROM invoices ORDER BY id DESC LIMIT 10;
SELECT step, result, reason FROM audit_log ORDER BY id DESC LIMIT 10;
```

## Changing the PO data later
POs are loaded outside the app, so change them the same way.

- **Reload from the CSV:** `python load_po_data.py` (skips what exists).
- **Add one PO by hand** (pgAdmin Query Tool). Use a vendor id from `SELECT id, name FROM vendors;`:
```sql
WITH po AS (
  INSERT INTO purchase_orders(po_number, vendor_id, issue_date, subtotal, tax_rate, tax, total)
  VALUES ('PO-9001', 1, '2026-10-01', 1000, 0.18, 180, 1180) RETURNING id)
INSERT INTO po_lines(po_id, description, quantity, unit_price) SELECT id, 'MRO', 10, 100 FROM po;
```
- **Go back to just the 39 test POs** (only if no invoice should keep pointing at the others):
```sql
DELETE FROM po_lines WHERE po_id IN (SELECT id FROM purchase_orders WHERE po_number >= 'PO-2040');
DELETE FROM purchase_orders WHERE po_number >= 'PO-2040';
```

## Copy what you already have in SQLite (optional)
If you already uploaded invoices and want to keep them:
```powershell
python migrate_sqlite_to_postgres.py --wipe
```
It copies every table with the same ids, then checks row counts, invoice totals and the audit chain. Your SQLite file is only
read, never changed. Without `--wipe` it refuses to run if PostgreSQL already holds data. Run `load_po_data.py` afterwards.

## Backup and restore (Docker)
```powershell
docker exec clearpay-pg pg_dump -U clearpay -Fc -f /tmp/clearpay.dump clearpay
docker cp clearpay-pg:/tmp/clearpay.dump .
# restore:
docker cp clearpay.dump clearpay-pg:/tmp/clearpay.dump
docker exec clearpay-pg pg_restore -U clearpay -d clearpay --clean --if-exists /tmp/clearpay.dump
```

## Go back to SQLite
Delete the `DATABASE_URL` line (or leave it empty) in `.env` and restart. Your SQLite file is still there.

## Good to know
- **Evaluation and tests never touch your real data.** In PostgreSQL they run in their own schemas (`s_eval_test_...`,
  `s_eval_redteam_...`, and temporary ones for tests); your data is in `public`. The evaluation's two schemas stay after a
  run and are reused next time; it is safe to drop them.
- **Uploaded invoice files stay on disk** (`data/uploads`); the database stores their paths. That is fine on your laptop.
  Moving the files into the database is only needed if you host ClearPay on a service that wipes its disk.
- **Still run one server worker.** The agent queue and rate limits live in memory. The audit log uses a database lock,
  so it stays correct even if several processes write to it.
- `python import_kaggle_pos.py`, `python generate_data.py` and `--wipe` all REPLACE ClearPay's tables in the database
  that `DATABASE_URL` points to. Use `pg_dump` first if the data matters.
- The amounts are real Kaggle quantities and negotiated prices, treated as rupees; the vendors' bank, GSTIN and email
  details are fake.

## Troubleshooting
| Message | Fix |
|---|---|
| `password authentication failed` | The password in `DATABASE_URL` differs from the one you created; encode special characters |
| `connection refused` | PostgreSQL isn't running (`docker start clearpay-pg`) or the port isn't 5432 |
| `No module named 'psycopg'` | Run `pip install -r requirements.txt` in the same virtual environment |
| `Couldn't find the Kaggle CSV` | Put `Procurement_KPI_Analysis_Dataset-selected-columns.csv` in the project folder, or pass `--csv path` |
| `The PostgreSQL database already has data` | Add `--wipe` to the migrate command, or use an empty database |
| The Purchase orders page shows only 39 POs | You haven't run `python load_po_data.py` yet |
| `SSL` errors on a hosted database | Add `?sslmode=require` to `DATABASE_URL` |

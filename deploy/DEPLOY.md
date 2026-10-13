# Deploying ClearPay

ClearPay is a Flask app with a SQLite database and two background threads (the agent worker and the inbox watcher).
That shapes how it is deployed:

- **One server process only** (`gunicorn --workers 1 --threads 4`). The agent queue, the inbox watcher and the sign-in rate limits live in memory.
- **The `data/` folder must be on a disk that survives restarts** (it holds the database and the uploaded invoices).
- **Always use HTTPS**, because people sign in and invoices contain bank details.

> ClearPay is demo-grade (SQLite, no two-factor sign-in). Deploy it with **fake or Kaggle data**, not real company invoices, until you have worked through "Before real company data" at the end.

## Choose a route

| Route | Good for | Needs |
|---|---|---|
| **A. Your laptop + Cloudflare quick tunnel** | Showing judges for an hour or a day, no server | Laptop stays on; the link changes each time |
| **B. A small Linux server (VPS) + Caddy** | A link that stays up, with a proper HTTPS address | A server (about 1 to 2 GB RAM) and a domain name |
| **C. A platform service (Render, Railway, Fly.io)** | No server to manage | A paid persistent disk, otherwise the database resets on restart. Check each provider's current plans |

Steps for A and B follow; the quickest option for a public demo link, **Render**, is Route C at the end.

---

## Route A: show it from your laptop (no server)

1. Run ClearPay as usual (`python app.py`).
2. Install the tunnel tool once (Windows PowerShell): `winget install --id Cloudflare.cloudflared`. Close and reopen PowerShell.
3. In `.env` add: `BEHIND_PROXY=1`, `SESSION_COOKIE_SECURE=1`, `SHOW_DEMO_HINT=0`, a long random `FLASK_SECRET_KEY`, and your own strong `DEMO_PASSWORD` (set before the very first start).
4. Restart the app, then in a second PowerShell window run: `cloudflared tunnel --url http://127.0.0.1:5000`
5. It prints an address like `https://something-random.trycloudflare.com`. Open it from any device.
6. Press **Ctrl + C** in that window to take the site offline.

Anyone with the link can reach your sign-in page, so use strong passwords and share the link only with the judges.

---

## Route B: a small Linux server with HTTPS

### 1. Get a server and a domain
1. Create an **Ubuntu 24.04** server at any provider (1 GB RAM works, 2 GB is more comfortable). Note its **public IP address**.
2. Get a domain name (or a free dynamic-DNS subdomain such as DuckDNS). Add an **A record** pointing it to the server's IP. This can take a few minutes to start working.
3. In the provider's firewall settings, allow ports **22, 80 and 443**.

### 2. Secure the server (as root, once)
```bash
ssh root@YOUR_SERVER_IP
adduser clearpay                      # choose a strong password
usermod -aG sudo clearpay
rsync --archive --chown=clearpay:clearpay ~/.ssh /home/clearpay   # copies your SSH key, if you used one
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable
exit
```
Then sign in as the new user: `ssh clearpay@YOUR_SERVER_IP`. Use this user for everything below.

### 3. Install the software
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 python3-venv python3-pip unzip sqlite3 tesseract-ocr
# Caddy (web server that handles HTTPS automatically), from Caddy's official repository:
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```
(`tesseract-ocr` is only needed to read scanned images.)

### 4. Upload ClearPay
On **your computer** (PowerShell), from the folder that holds the zip:
```powershell
scp clearpay.zip clearpay@YOUR_SERVER_IP:~
scp Procurement_KPI_Analysis_Dataset-selected-columns.csv clearpay@YOUR_SERVER_IP:~
```
On the **server**:
```bash
cd ~ && unzip -q clearpay.zip && mv Procurement_KPI*.csv clearpay/
cd clearpay
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Production settings
```bash
cp deploy/env.production.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"      # copy the output
nano .env                                                      # fill in the lines below, Ctrl+O to save, Ctrl+X to exit
chmod 600 .env
```
Set at least:

| Line | Value |
|---|---|
| `FLASK_SECRET_KEY` | the long random string you just printed |
| `DEMO_PASSWORD` | your own strong password. **Set it now**: it is used once, when the accounts are first created, and the app refuses the built-in default |
| `APP_BASE_URL` | `https://your-domain` |
| `GROQ_API_KEY` | your key (leave empty for offline mode, and set `LLM_MODE=offline`) |

Keep `BEHIND_PROXY=1`, `SESSION_COOKIE_SECURE=1` and `SHOW_DEMO_HINT=0`.

### 6. Load the data
```bash
python import_kaggle_pos.py        # Kaggle vendors and POs (or: python generate_data.py for the built-in demo data)
rm -rf sample_invoices redteam_invoices    # optional: remove practice files if you only want to upload your own
```

### 7. Test it runs
```bash
gunicorn --workers 1 --threads 4 --bind 127.0.0.1:8000 wsgi:app
```
In a second SSH window: `curl http://127.0.0.1:8000/healthz` should print `ok`. Press **Ctrl + C** in the first window.

### 8. Keep it running (systemd)
```bash
sudo cp deploy/clearpay.service /etc/systemd/system/clearpay.service
sudo systemctl daemon-reload
sudo systemctl enable --now clearpay
sudo systemctl status clearpay          # should say "active (running)"
journalctl -u clearpay -f               # live logs; Ctrl+C to leave
```
If you used a different username or folder, edit the paths in `clearpay.service` first.

### 9. Turn on HTTPS (Caddy)
```bash
sudo nano /etc/caddy/Caddyfile          # replace everything with the contents of deploy/Caddyfile, using YOUR domain
sudo systemctl reload caddy
```
Open `https://your-domain`. The first load can take a few seconds while Caddy gets the certificate. It needs ports 80 and 443 open and the domain pointing at the server.

### 10. First sign-in checklist
1. Sign in as `admin` with your `DEMO_PASSWORD`.
2. **Settings & users:** set the real manager and AP team emails, and vendor emails; use **Reset a password** to give each user a different password.
3. Deactivate demo users you don't need (for example `ap_user2`).
4. Upload a test invoice, approve it as `mokshana` in a private window, and check the notification reaches `prashasthi`.
5. Visit `https://your-domain/healthz`: it should print `ok`.

### 11. Back up the database
```bash
sudo apt -y install cron && crontab -e      # then add this line at the bottom:
0 2 * * * /home/clearpay/clearpay/deploy/backup.sh
```
Backups go to `/home/clearpay/backups` (14 days kept). Copy them off the server now and then (`scp`).

**Restore:** `sudo systemctl stop clearpay`, copy a backup over `data/ap_agent.db`, delete `data/ap_agent.db-wal` and `data/ap_agent.db-shm`, then `sudo systemctl start clearpay`.

### 12. Updating ClearPay later
```bash
scp clearpay.zip clearpay@YOUR_SERVER_IP:~            # from your computer
```
On the server, unzip over the project **without** touching your data and settings:
```bash
cd ~ && unzip -oq clearpay.zip -x "clearpay/.env" "clearpay/data/*"
cd clearpay && source .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart clearpay
```
Older databases upgrade themselves on start. Everyone signs in again after a restart only if the secret key changed.

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| The service won't start; log says `FLASK_SECRET_KEY` | `BEHIND_PROXY=1` needs a secret key in `.env`. |
| The log says `Set DEMO_PASSWORD…` | Choose your own `DEMO_PASSWORD` in `.env`, delete `data/`, run the data step again, restart. |
| Accounts still use the demo password (warning in the log) | Sign in as admin → Settings & users → Reset a password, for each user. |
| Browser says "connection refused" or no certificate | The domain doesn't point at the server yet, or ports 80/443 are closed. Check `sudo systemctl status caddy` and `journalctl -u caddy`. |
| 502 Bad Gateway | ClearPay isn't running: `sudo systemctl status clearpay` and `journalctl -u clearpay`. |
| Everyone shares one rate limit, or sessions show the server's address | `BEHIND_PROXY=1` is missing from `.env`. |
| Invoices stay "Processing" | Check the log. If Groq is rate-limited, set `LLM_MODE=extract_only` or `offline`. Never run more than one worker. |
| "Your form expired" after a restart | The secret key changed between starts. Keep the same one in `.env`. |
| The sign-in page says "Demo users…" | Add `SHOW_DEMO_HINT=0` to `.env`, restart. |

## Before real company data

This is the honest gap between "demo online" and "production":
- Use PostgreSQL instead of SQLite, with managed backups, and keep the database off the web server.
- Add two-factor sign-in or company single sign-on, and password reset by email.
- Send emails through a real email provider.
- Put ClearPay on the company network or behind a VPN or access proxy rather than on the open internet.
- Run the evaluation with your real Groq model on real invoices, and run in shadow mode next to the AP team first.
- Keep the server patched (`sudo apt update && sudo apt upgrade`), and review the audit log regularly.


---

## Route C: Render (fastest public link, about 10 minutes plus the first build)

Render deploys straight from a Git repository. **On its free plan the disk is wiped every time the service sleeps (after 15 minutes without visitors), restarts or redeploys**, so uploaded invoices, notifications and the audit log disappear, and the first visit after sleeping takes a while. The users, vendors and purchase orders come back by themselves. That is fine for a demo; for lasting data use a paid plan with a persistent disk, or Route B.

1. **Put the project on GitHub (private repository).** In the project folder: `git init`, `git add .`, `git commit -m "ClearPay"`, create an empty **private** repository on github.com, then `git remote add origin <its address>` and `git push -u origin main`. `.gitignore` keeps `.env`, `data/` and the Kaggle CSV out. To use the Kaggle vendors and POs, make sure `seed_data.json` (created by `import_kaggle_pos.py`) is in the folder before you commit.
2. On render.com sign in with GitHub, click **New**, then **Web Service**, and pick the repository.
3. Fill in: **Runtime** Python 3, **Build Command** `pip install -r requirements.txt`, **Start Command** `gunicorn --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT wsgi:app`, **Instance type** Free. In Advanced, set **Health Check Path** to `/healthz`.
4. Add these **Environment Variables**: `FLASK_SECRET_KEY` (a long random string), `DEMO_PASSWORD` (your own strong password), `BEHIND_PROXY=1`, `SESSION_COOKIE_SECURE=1`, `SHOW_DEMO_HINT=0`, `LLM_MODE=extract_only`, `GROQ_API_KEY`, `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_REASONING_EFFORT=low`, and for real emails `EMAIL_MODE=brevo`, `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME` (see "Sending real emails" below).
5. Click **Create Web Service**. The first build takes a few minutes. When the log says the service is live, open `https://<your-service>.onrender.com`.

Notes: the repository's `.python-version` file pins Python 3.12. Scanned images can't be read there (no OCR program installed); normal PDFs and text files are fine. Open the link a couple of minutes before presenting, because a sleeping service takes a while to wake.


---

## Sending real emails

Emails are always drafts first; a person presses **Send** on each one. Choose how they leave the app with `EMAIL_MODE`:

| Mode | How | Use it when |
|---|---|---|
| `mock` | Nothing leaves the app; the draft is only marked as sent | Demos where emails don't matter |
| `smtp` | An SMTP server, for example Gmail with an App Password | Your laptop or your own server, where outgoing ports 465/587 are open |
| `brevo` | Brevo's email API over HTTPS (port 443) | **Render's free plan** (it blocks outgoing SMTP ports 25, 465 and 587), or any place where Gmail login keeps failing |

### Brevo setup (about 5 minutes)
1. Create a free account at brevo.com.
2. Add your email address as a **sender** and click the verification link Brevo emails you. Brevo only sends from verified senders. (Menu names change; look for "Senders".)
3. Create an API key: profile icon, top right, then **Settings**, **API Keys**, **Generate a new API key**. Copy it once.
4. Set these in `.env` (or as environment variables on your host):
   ```
   EMAIL_MODE=brevo
   BREVO_API_KEY=xkeysib-...
   MAIL_FROM=the-address-you-verified@example.com
   MAIL_FROM_NAME=ClearPay
   EMAIL_REDIRECT_TO=your-own-address@gmail.com     # test mode: every email goes to you; remove to go live
   APP_BASE_URL=https://your-public-address         # used in the links inside emails
   ```
5. Restart or redeploy. Sign in as admin, open **Settings & users**, type your address under **Send a test email to**, and press **Send test email**. Check the inbox and the **Spam** folder.
6. In **Settings & users** set real manager, AP team and vendor addresses. Addresses ending in `.example` are refused.

Emails sent "from" a free Gmail address through a third-party service can land in spam. That is normal for a demo; a company domain avoids it.

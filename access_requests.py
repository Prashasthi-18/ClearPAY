"""
access_requests.py - a new Google account asks for access; admins approve or reject from an email.

  * Each request gets a random one-time token. Only its SHA-256 is stored; the token itself exists only in the
    admins' emails. Links expire after REQUEST_HOURS and stop working once anyone decides.
  * Opening the link only shows the request. Approving or rejecting needs a button press (a POST), because mail
    apps and virus scanners open links on their own.
  * The link can approve or reject that one request and nothing else.
"""
import hashlib
import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from html import escape

import db
import mailer
import notifications

REQUEST_HOURS = 48


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def base_url():
    return (os.getenv("APP_BASE_URL") or "http://127.0.0.1:5000").rstrip("/")


def create(user_id):
    """Open a request for this (inactive) user. Returns the one-time token for the email links."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO access_requests(user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
               (user_id, _hash(token), now.isoformat(timespec="seconds"),
                (now + timedelta(hours=REQUEST_HOURS)).isoformat(timespec="seconds")))
    return token


def find(token):
    """The request and its user for a link token, with 'state': open | expired | approved | rejected (or None)."""
    if not token or len(token) > 100:
        return None
    for r in db.query("SELECT * FROM access_requests WHERE token_hash = ?", (_hash(token),)):
        if not hmac.compare_digest(r["token_hash"], _hash(token)):
            continue
        user = db.query("SELECT * FROM users WHERE id = ?", (r["user_id"],), one=True)
        if not user:
            return None
        state = r["decision"] or ("expired" if datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc) else "open")
        return {"request": dict(r), "user": dict(user), "state": state}
    return None


def has_open(user_id):
    """True if an undecided request that hasn't expired exists (so we don't email the admins again)."""
    now = datetime.now(timezone.utc)
    return any(datetime.fromisoformat(r["expires_at"]) > now for r in
               db.query("SELECT expires_at FROM access_requests WHERE user_id = ? AND decision IS NULL", (user_id,)))


def latest_decision(user_id):
    r = db.query("SELECT decision FROM access_requests WHERE user_id = ? ORDER BY id DESC", (user_id,), one=True)
    return r["decision"] if r else None


def decide(user_id, decision, by):
    """Approve (activate the account) or reject. Closes every open request for the user. Returns True if it changed."""
    user = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if not user or decision not in ("approved", "rejected"):
        return False
    now = db.now_iso()
    db.execute("UPDATE access_requests SET decision = ?, decided_by = ?, decided_at = ? WHERE user_id = ? AND decision IS NULL",
               (decision, by, now, user_id))
    if decision == "approved":
        db.execute("UPDATE users SET active = 1 WHERE id = ?", (user_id,))
    db.log_audit(None, user["username"], "admin", "access_" + decision, "info",
                 f"{by} {decision} the access request from {user['full_name']} ({user['email']})")
    others = [a for a in notifications.ids_with_role("admin")]
    notifications.notify(others, "security", f"Access request {decision}",
                         f"{by} {decision} {user['full_name']} ({user['email']}).", None, "system")
    _send_async([(user["email"], *_decision_email(user, decision))])
    return True


# ---------------------------------------------------------------- emails
def admin_recipients():
    return [r["email"] for r in db.query("SELECT email FROM users WHERE role = 'admin' AND active = 1") if r["email"]]


def _button(href, label, bg):
    return (f'<a href="{escape(href)}" style="display:inline-block;padding:12px 22px;margin:4px 8px 4px 0;border-radius:9px;'
            f'background:{bg};color:#fff;font:600 15px Arial,sans-serif;text-decoration:none">{label}</a>')


def _request_email(user, token):
    link = f"{base_url()}/access-request/{token}"
    subject = f"ClearPay access request: {user['full_name']}"
    text = (f"{user['full_name']} ({user['email']}) signed in with Google and is asking for access to ClearPay "
            f"(AP team role).\n\nApprove: {link}?d=approve\nReject:  {link}?d=reject\n\n"
            f"Each link opens a page where you confirm. It works once and expires in {REQUEST_HOURS} hours.\n"
            f"If you don't know this person, reject the request.\n\n— ClearPay")
    html = (f'<div style="font:15px/1.5 Arial,sans-serif;color:#0F1B2D;max-width:520px">'
            f'<p style="font-size:18px;font-weight:700;margin:0 0 12px">New access request</p>'
            f'<p><b>{escape(user["full_name"])}</b> ({escape(user["email"])}) signed in with Google and is asking for access '
            f'to ClearPay with the <b>AP team</b> role.</p>'
            f'<p>{_button(link + "?d=approve", "Approve", "#17784B")}{_button(link + "?d=reject", "Reject", "#BF3528")}</p>'
            f'<p style="color:#5E6C80;font-size:13px">Each button opens a page where you confirm. The link works once and '
            f'expires in {REQUEST_HOURS} hours. If you don\'t know this person, reject the request.</p></div>')
    return subject, text, html


def _decision_email(user, decision):
    if decision == "approved":
        subject = "Your ClearPay access was approved"
        text = (f"Hi {user['full_name']},\n\nAn administrator approved your access to ClearPay. Sign in with Google at "
                f"{base_url()}/login\n\n— ClearPay")
    else:
        subject = "Your ClearPay access request"
        text = (f"Hi {user['full_name']},\n\nYour request for access to ClearPay was not approved. If you think this is a "
                f"mistake, contact your administrator.\n\n— ClearPay")
    return subject, text, None


def _send_async(messages):
    """Send in the background so the sign-in page doesn't wait for the mail server."""
    def run():
        for to, subject, text, html in messages:
            try:
                ok, _, err = mailer.send(to, subject, text, html)
                if not ok:
                    db.log_audit(None, "system", "system", "email_failed", "problem", f"Access email to {to} not sent: {err}")
            except Exception as e:      # a mail problem must never break sign-in
                db.log_audit(None, "system", "system", "email_failed", "problem", f"Access email to {to} failed: {type(e).__name__}")
    if os.getenv("ACCESS_EMAILS_SYNC") == "1":
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def notify_admins(user, token):
    """Email every active admin the Approve / Reject links. Returns how many admins it was sent to."""
    msg = _request_email(user, token)
    to = admin_recipients()
    _send_async([(addr, *msg) for addr in to])
    return len(to)

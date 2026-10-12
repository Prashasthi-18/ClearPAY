"""
app.py - Flask web app for ClearPay (AP automation).

Run:  python app.py   ->  http://127.0.0.1:5000
"""
import csv
import io
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

import access_requests
import agent
import assistant
import db
import email_inbox
import evaluate
import google_auth
import helpbot
import insights
import investigator
import mailer
import mfa
import notifications
import retrieval
import security
import tools
import watcher

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",   # set 1 behind HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),                       # idle timeout
    MAX_CONTENT_LENGTH=60 * 1024 * 1024,
)
STATUSES = ("Approved", "Pending", "Flagged", "Rejected", "Processing")

# ---- running behind a web server (Caddy / nginx) on the internet: BEHIND_PROXY=1 ----------------
BEHIND_PROXY = os.getenv("BEHIND_PROXY", "0") == "1"
DEFAULT_DEMO_PASSWORD = "ChangeMe!2026"
if BEHIND_PROXY:
    if not os.getenv("FLASK_SECRET_KEY"):
        raise RuntimeError("BEHIND_PROXY=1 needs FLASK_SECRET_KEY in .env (a long random string), otherwise everyone "
                           "is signed out on every restart. Make one with: python -c \"import secrets; print(secrets.token_hex(32))\"")
    from werkzeug.middleware.proxy_fix import ProxyFix
    # Trust exactly ONE proxy in front of the app: real client address, https scheme and host come from its headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# ==========================================================================
# Request hooks: user, CSRF, headers
# ==========================================================================
@app.before_request
def before():
    session.permanent = True
    security.load_current_user()
    if request.endpoint not in ("static",):
        security.check_csrf()
    # MFA_REQUIRED_ROLES: someone in such a role without MFA can only reach the page that sets it up
    if g.user and not g.user["mfa_enabled"] and mfa.required_for(g.user["role"]) and \
            request.endpoint not in ("account", "logout", "static", "healthz", "google_start", "google_callback", "access_request"):
        if request.method == "GET" and not request.path.startswith("/api/"):
            flash("Your role must use two-factor authentication. Set up your authenticator app to continue.", "error")
        return redirect(url_for("account", tab="security"))


@app.after_request
def after(resp):
    return security.add_security_headers(resp)


@app.context_processor
def inject():
    pending = unread = last_note = 0
    if g.get("user"):
        pending = db.query("SELECT COUNT(*) n FROM invoices WHERE status='Pending'", one=True)["n"]
        unread = notifications.unread_count(g.user["id"])
        last_note = notifications.last_id(g.user["id"])
    return {"csrf_token": security.csrf_token, "user": g.get("user"), "role_labels": security.ROLE_LABELS,
            "pending_count": pending, "llm_on": tools.llm_enabled(), "model": tools.MODEL,
            "mail": mailer.status(),
            "watcher": watcher.status(), "mailbox": email_inbox.status(), "unread_count": unread, "last_note_id": last_note,
            "just_in": session.pop("just_in", False) if g.get("user") else False, "today_ist": ist_now(),
            "show_demo_hint": os.getenv("SHOW_DEMO_HINT", "0") == "1", "db_engine": db.engine(),
            "google_on": google_auth.enabled(), "google_signup": google_auth.signup_mode() == "request"}


# ==========================================================================
# Template helpers
# ==========================================================================
IST = timezone(timedelta(hours=5, minutes=30))     # India has no daylight saving, so a fixed offset is exact
STATUS_LABELS = {"Approved": "Approved", "Pending": "Pending approval", "Flagged": "AP review required",
                 "Rejected": "Rejected", "Processing": "Processing", "ready_to_pay": "Ready for payment",
                 "pending_approval": "Awaiting approval", "cancelled": "Cancelled", "paid": "Paid", "draft": "Draft", "sent": "Sent",
                 "failed": "Failed", "ok": "OK", "problem": "Problem", "info": "Info", "blocked": "Blocked"}
AVATAR_COLORS = ["#2F5BEA", "#0F766E", "#7C3AED", "#B45309", "#BE185D", "#0369A1", "#15803D", "#9333EA"]


def to_ist(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(IST)


def ist_now():
    return datetime.now(IST)


@app.template_filter("inr")
def inr(x):
    return tools.fmt_inr(x) if x is not None else "—"


@app.template_filter("inr_short")
def inr_short(x):
    """₹45,300 · ₹5.4L · ₹1.2Cr for headline numbers."""
    if x is None:
        return "—"
    a = abs(x)
    if a >= 1e7:
        return "₹" + f"{x / 1e7:.2f}".rstrip("0").rstrip(".") + "Cr"
    if a >= 1e5:
        return "₹" + f"{x / 1e5:.2f}".rstrip("0").rstrip(".") + "L"
    return tools.fmt_inr(round(x))[:-3]


@app.template_filter("ist_time")
def ist_time(s):
    t = to_ist(s)
    return t.strftime("%H:%M IST") if t else "—"


@app.template_filter("ist_hm")
def ist_hm(s):
    t = to_ist(s)
    return t.strftime("%H:%M") if t else ""


@app.template_filter("ist_day")
def ist_day(s):
    t = to_ist(s)
    if not t:
        return ""
    today = ist_now().date()
    return "Today" if t.date() == today else "Yesterday" if t.date() == today - timedelta(days=1) else t.strftime("%d %b %Y")


@app.template_filter("status_label")
def status_label(s):
    return STATUS_LABELS.get(s, (s or "").replace("_", " ").capitalize())


@app.template_filter("avatar_color")
def avatar_color(name):
    return AVATAR_COLORS[sum(map(ord, name or "?")) % len(AVATAR_COLORS)]


@app.template_filter("initials")
def initials(name):
    parts = [p for p in (name or "?").replace("(", " ").split() if p[:1].isalpha()]
    return ((parts[0][0] + (parts[1][0] if len(parts) > 1 else "")) if parts else "?").upper()


@app.template_filter("agent")
def agent_str(ua):
    b, o, d = security.describe_agent(ua)
    return f"{b} on {o}"


@app.template_filter("mask")
def mask(x):
    return security.mask_account(x)


@app.template_filter("fromjson")
def fromjson(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


@app.template_filter("dt")
def dt(s):
    """Every date-time in the app: 06 Oct 2026, 14:32 IST (24-hour)."""
    t = to_ist(s)
    return t.strftime("%d %b %Y, %H:%M IST") if t else "—"


@app.template_filter("num")
def num(x):
    if x is None:
        return "—"
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def mark_text(text, needles):
    """Escape the document text, then wrap each problem value in <mark>. Safe against HTML injection."""
    out = str(escape(text or ""))
    for n in sorted({x for x in needles if x}, key=len, reverse=True):
        for v in tools._search_variants(n):
            ev = str(escape(v))
            if ev and ev in out:
                out = out.replace(ev, f"<mark>{ev}</mark>")
                break
    return Markup(out)  # nosec B704 - every piece was escaped above; tests/test_security.py checks this


def can(action, inv=None):
    """Central permission rules (also enforced inside each POST route)."""
    u = g.get("user")
    if not u:
        return False
    r = u["role"]
    if action == "upload":
        return r in ("ap", "admin")
    if action == "approve":                   # managers approve, never their own upload
        return r in ("manager", "admin") and inv is not None and inv["status"] == "Pending" \
            and inv["uploaded_by"] != u["id"]
    if action == "reject":
        if inv is None:
            return False
        if inv["status"] == "Pending":          # a decision on a pending invoice: never by the uploader
            return r in ("manager", "admin") and inv["uploaded_by"] != u["id"]
        return inv["status"] == "Flagged" and r in ("ap", "manager", "admin")
    if action == "send_to_manager":
        return inv is not None and inv["status"] == "Flagged" and r in ("ap", "admin")
    if action == "reprocess":
        return inv is not None and r in ("ap", "admin") and inv["status"] in ("Flagged", "Pending")
    if action in ("run_eval", "settings"):
        return r == "admin" if action == "settings" else r in ("ap", "admin")
    if action == "po_manage":                 # purchase orders are controlled records: admin only
        return r == "admin"
    if action == "investigate":               # ask the Investigator (it only recommends)
        return inv is not None and inv["status"] in ("Flagged", "Pending") and r in ("ap", "manager", "admin")
    if action == "record_payment":            # a PERSON records a payment made outside ClearPay
        return inv is not None and inv["status"] == "Approved" and r in ("ap", "admin")
    return False


app.jinja_env.globals.update(can=can, csrf_token=security.csrf_token)


# ==========================================================================
# Auth
# ==========================================================================
MFA_STEP_SECONDS = 300          # time allowed between the password (or Google) and the authenticator code


def _safe_next(nxt):
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard")


def _after_first_factor(user, method, nxt):
    """The password or Google sign-in was right. Ask for the authenticator code when MFA is on, else sign in."""
    if user["mfa_enabled"]:
        session.clear()
        session["mfa_pending"] = {"uid": user["id"], "method": method, "next": nxt, "at": int(time.time())}
        return redirect(url_for("login_mfa"))
    return _signed_in(user, method, nxt)


def _signed_in(user, method, nxt, second=None):
    """Every way of signing in ends here: a fresh server-side session, new-device alert, audit entry."""
    session.clear()                      # new session id on login (prevents fixation)
    ua = request.headers.get("User-Agent") or ""
    seen_before = [security.describe_agent(r["user_agent"])[:2] for r in db.query(
        "SELECT user_agent FROM user_sessions WHERE user_id = ?", (user["id"],))]
    session["uid"] = user["id"]
    session["sid"] = security.start_session(user["id"])
    session["just_in"] = True
    session.permanent = True
    browser, os_name, _ = security.describe_agent(ua)
    if seen_before and (browser, os_name) not in seen_before:
        notifications.security_event(user["id"], "New sign-in on a new device",
                                     f"Signed in with {browser} on {os_name} from {request.remote_addr} at "
                                     f"{ist_now():%H:%M IST}. If this wasn't you, change your password.")
    how = ({"password": "", "google": " with Google"}[method] +  # nosec B105 - labels, not passwords
           {None: "", "app": " + authenticator code", "recovery": " + recovery code"}[second])
    db.log_audit(None, user["username"], user["role"], "user_login", "info", f"{user['full_name']} signed in{how}")
    if mfa.required_for(user["role"]) and not user["mfa_enabled"]:
        return redirect(url_for("account", tab="security"))
    return redirect(_safe_next(nxt))


def _wants_async():
    """The sign-in page posts in the background (login.js) so it can animate the result without a reload."""
    return request.headers.get("X-ClearPay-Async") == "1"


def _async_outcome(resp):
    """Turn the redirect a sign-in step ended with into JSON for the page's background sign-in."""
    nxt = resp.headers.get("Location", url_for("dashboard"))
    if session.get("uid"):
        u = db.query("SELECT full_name FROM users WHERE id=?", (session["uid"],), one=True)
        name = u["full_name"] if u else ""
        return jsonify(ok=True, next=nxt, name=name, first=(name.split() or [""])[0],
                       initials=initials(name), color=avatar_color(name))
    if session.get("mfa_pending"):
        return jsonify(ok=True, mfa=True, next=nxt)
    return jsonify(ok=False, restart=True, next=nxt)     # e.g. locked during the code step: the login page explains


def _login_page(**kw):
    kw.setdefault("error", session.pop("login_error", None))
    kw.setdefault("notice", session.pop("login_notice", None))
    return render_template("login.html", **kw)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if security.rate_limited("login", 10, 60):
            error = "Too many attempts from this address. Wait a minute and try again."
        else:
            username = (request.form.get("username") or "").strip()[:254]          # a username or an email
            user, error = security.authenticate(username, request.form.get("password", ""))
            if user:
                resp = _after_first_factor(user, "password", request.args.get("next", ""))
                return _async_outcome(resp) if _wants_async() else resp
            db.log_audit(None, username or "(blank)", "system", "login_failed", "blocked",
                         f"Failed sign-in from {request.remote_addr}")
        if error and _wants_async():
            return jsonify(ok=False, error=error)
    return _login_page(error=error) if error else _login_page()


def check_second_factor(user, code):
    """'app' or 'recovery' when the code is right (and records it so it can't be reused), else None."""
    step = mfa.verify(mfa.unseal(user["mfa_secret"]), code, user["mfa_last_step"])
    if step is not None:
        db.execute("UPDATE users SET mfa_last_step=?, failed_attempts=0, locked_until=NULL WHERE id=?", (step, user["id"]))
        return "app"
    if mfa.looks_like_recovery_code(code):
        ok, rest = mfa.use_recovery_code(user["mfa_recovery"], code)
        if ok:
            db.execute("UPDATE users SET mfa_recovery=?, failed_attempts=0, locked_until=NULL WHERE id=?", (rest, user["id"]))
            return "recovery"
    return None


@app.route("/login/mfa", methods=["GET", "POST"])
def login_mfa():
    p = session.get("mfa_pending")
    if not p or time.time() - p["at"] > MFA_STEP_SECONDS:
        session.pop("mfa_pending", None)
        if p:
            session["login_error"] = "The sign-in took too long. Sign in again."
        return redirect(url_for("login"))
    user = db.query("SELECT * FROM users WHERE id=? AND active=1", (p["uid"],), one=True)
    if not user:
        session.clear()
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        locked = security.locked_message(user)
        if locked:
            session.clear()
            session["login_error"] = locked
            return jsonify(ok=False, restart=True, next=url_for("login")) if _wants_async() else redirect(url_for("login"))
        if security.rate_limited("mfa", 10, 60):
            error = "Too many attempts from this address. Wait a minute and try again."
        else:
            how = check_second_factor(user, request.form.get("code", ""))
            if how:
                if how == "recovery":
                    left = mfa.recovery_left(db.query("SELECT mfa_recovery FROM users WHERE id=?", (user["id"],), one=True)["mfa_recovery"])
                    db.log_audit(None, user["username"], user["role"], "recovery_code_used", "info", f"{left} recovery codes left")
                    notifications.security_event(user["id"], "A recovery code was used to sign in",
                                                 f"Signed in with a recovery code at {ist_now():%H:%M IST}; {left} left. "
                                                 "If you lost your phone, set up the authenticator app again in Account → Security.")
                resp = _signed_in(user, p["method"], p["next"], second=how)
                return _async_outcome(resp) if _wants_async() else resp
            db.log_audit(None, user["username"], "system", "mfa_failed", "blocked",
                         f"Wrong authenticator code from {request.remote_addr}")
            locked = security.record_failure(user, "wrong authenticator codes")
            if locked:
                session.clear()
                session["login_error"] = locked
                return jsonify(ok=False, restart=True, next=url_for("login")) if _wants_async() else redirect(url_for("login"))
            error = "That code isn't right. Use the current 6-digit code from your authenticator app, or a recovery code."
        if error and _wants_async():
            return jsonify(ok=False, error=error)
    return _login_page(step="mfa", error=error, mfa_name=user["full_name"])


# ---- Google sign-in, sign-up and account linking ---------------------------------------------------
@app.route("/auth/google", methods=["GET", "POST"])
def google_start():
    if not google_auth.enabled():
        abort(404)
    if security.rate_limited("google", 20, 60):
        session["login_error"] = "Too many attempts from this address. Wait a minute and try again."
        return redirect(url_for("login"))
    link = request.method == "POST" and g.user is not None          # linking only from the account page (CSRF-checked POST)
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip() or url_for("google_callback", _external=True)
    url, pending = google_auth.start(redirect_uri)
    pending.update(link_uid=g.user["id"] if link else None, next=request.args.get("next", ""))
    session["google_pending"] = pending
    return redirect(url)


@app.route("/auth/google/callback")
def google_callback():
    pending = session.pop("google_pending", None)
    linking = bool(pending and pending.get("link_uid"))
    try:
        info = google_auth.finish(request.args, pending)
    except google_auth.GoogleError as e:
        db.log_audit(None, g.user["username"] if g.user else "(google)", "system", "login_failed", "blocked",
                     f"Google sign-in failed from {request.remote_addr}: {e}")
        if linking and g.user:
            flash(str(e), "error")
            return redirect(url_for("account", tab="security"))
        session["login_error"] = str(e)
        return redirect(url_for("login"))
    if linking:
        return _google_link(info, pending["link_uid"])
    if not google_auth.domain_allowed(info["email"]):
        session["login_error"] = f"{info['email']} can't sign in to ClearPay. Use your work Google account."
        db.log_audit(None, info["email"], "system", "login_failed", "blocked", "Google account outside the allowed domains")
        return redirect(url_for("login"))
    user = db.query("SELECT * FROM users WHERE google_sub=?", (info["sub"],), one=True)
    if user is None:                                  # first time: link by verified email if exactly one account has it
        same = db.query("SELECT * FROM users WHERE LOWER(email)=? AND google_sub IS NULL AND active=1", (info["email"],))
        if len(same) == 1:
            user = same[0]
            db.execute("UPDATE users SET google_sub=?, google_email=? WHERE id=?", (info["sub"], info["email"], user["id"]))
            db.log_audit(None, user["username"], user["role"], "google_linked", "info", f"Connected Google account {info['email']}")
            notifications.security_event(user["id"], "Google account connected",
                                         f"{info['email']} can now be used to sign in to ClearPay. If this wasn't you, "
                                         "disconnect it in Account → Security and tell your administrator.")
    if user is None:
        return _google_signup(info)
    if not user["active"]:
        waiting = not user["has_password"] and not user["last_login_at"]
        declined = waiting and access_requests.latest_decision(user["id"]) == "rejected"
        if waiting and not declined and not access_requests.has_open(user["id"]):
            access_requests.notify_admins(user, access_requests.create(user["id"]))     # no live request: (re)send the email
        session["login_error"] = ("Your access request was declined. Contact your administrator." if declined else
                                  "Your account request is waiting for an administrator to approve it." if waiting
                                  else "This account is deactivated. Contact your administrator.")
        return redirect(url_for("login"))
    locked = security.locked_message(user)
    if locked:
        session["login_error"] = locked
        return redirect(url_for("login"))
    return _after_first_factor(user, "google", pending.get("next", ""))


def _google_signup(info):
    if google_auth.signup_mode() == "off":
        db.log_audit(None, info["email"], "system", "login_failed", "blocked", "Google sign-in with no ClearPay account")
        session["login_error"] = f"There's no ClearPay account for {info['email']}. Ask your administrator to add you."
        return redirect(url_for("login"))
    base = re.sub(r"[^a-z0-9_]", "", info["email"].split("@")[0].lower())[:30] or "user"
    username, n = base, 2
    while db.query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        username, n = f"{base}{n}", n + 1
    uid = db.execute("""INSERT INTO users(username, full_name, email, role, password_hash, active, has_password, google_sub, google_email)
                        VALUES (?,?,?,?,?,?,?,?,?)""", (username, info["name"], info["email"], "ap",
                                                        generate_password_hash(secrets.token_urlsafe(32)), 0, 0, info["sub"], info["email"]))
    sent_to = access_requests.notify_admins(db.query("SELECT * FROM users WHERE id=?", (uid,), one=True),
                                            access_requests.create(uid))
    db.log_audit(None, username, "system", "signup_requested", "info",
                 f"{info['name']} ({info['email']}) signed up with Google; approval email to {sent_to} admin(s)")
    notifications.notify(notifications.ids_with_role("admin"), "security", "New account request",
                         f"{info['name']} ({info['email']}) signed up with Google as {username}. Approve or reject it from "
                         "the email, or in Settings & users.", None, "system")
    session["login_notice"] = ("Request sent. An administrator has to approve your account; after that, "
                               "sign in with Google again.")
    return redirect(url_for("login"))


def _google_link(info, uid):
    if not g.user or g.user["id"] != uid:
        return redirect(url_for("login"))
    other = db.query("SELECT id FROM users WHERE google_sub=?", (info["sub"],), one=True)
    if other and other["id"] != uid:
        flash("That Google account is already connected to another ClearPay user.", "error")
    elif not google_auth.domain_allowed(info["email"]):
        flash(f"{info['email']} is outside the allowed domains. Use your work Google account.", "error")
    else:
        db.execute("UPDATE users SET google_sub=?, google_email=? WHERE id=?", (info["sub"], info["email"], uid))
        db.log_audit(None, g.user["username"], g.user["role"], "google_linked", "info", f"Connected Google account {info['email']}")
        notifications.security_event(uid, "Google account connected", f"{info['email']} can now be used to sign in to ClearPay.")
        flash(f"Connected. You can now sign in with {info['email']}.", "ok")
    return redirect(url_for("account", tab="security"))


@app.route("/access-request/<token>", methods=["GET", "POST"])
def access_request(token):
    """Opened from the admin's email. Viewing changes nothing; the decision needs a button press (POST)."""
    if security.rate_limited("access", 30, 60):
        abort(429)
    found = access_requests.find(token)
    if found is None:
        abort(404)
    result = None
    if request.method == "POST" and found["state"] == "open":
        decision = {"approve": "approved", "reject": "rejected"}.get(request.form.get("decision"))
        if decision:
            by = g.user["full_name"] if g.user and g.user["role"] == "admin" else "An admin (via email link)"
            access_requests.decide(found["user"]["id"], decision, by)
            found = access_requests.find(token)
            result = decision
    return render_template("access_request.html", r=found, token=token, result=result,
                           suggested=request.args.get("d") if request.args.get("d") in ("approve", "reject") else None)


@app.route("/logout", methods=["POST"])
def logout():
    if g.user:
        db.log_audit(None, g.user["username"], g.user["role"], "user_logout", "info", "Signed out")
        if session.get("sid"):
            security.end_session(session["sid"], "signed out")
    session.clear()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    """For uptime monitors and load balancers. Checks the database answers; reveals nothing else."""
    try:
        db.query("SELECT 1", one=True)
    except Exception:
        return Response("database unavailable", status=503, mimetype="text/plain")
    return Response("ok", mimetype="text/plain")


# ==========================================================================
# Dashboard
# ==========================================================================
def stats():
    s = db.get_settings()
    rows = db.query("SELECT status, decided_by, processing_ms, issues_json, prompt_tokens, completion_tokens "
                    "FROM invoices WHERE status != 'Processing'")
    n = len(rows)
    auto_ok = sum(1 for r in rows if r["status"] == "Approved" and r["decided_by"] is None)
    auto_rej = sum(1 for r in rows if r["status"] == "Rejected" and r["decided_by"] is None)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in STATUSES[:4]}
    codes = [i["code"] for r in rows for i in json.loads(r["issues_json"] or "[]")]
    ms = [r["processing_ms"] for r in rows if r["processing_ms"]]
    pt = sum(r["prompt_tokens"] or 0 for r in rows)
    ct = sum(r["completion_tokens"] or 0 for r in rows)
    cost = pt / 1e6 * s["groq_input_usd_per_m"] + ct / 1e6 * s["groq_output_usd_per_m"]
    amt = {r["status"]: r["a"] for r in db.query(
        "SELECT status, COALESCE(SUM(total), 0) a FROM invoices GROUP BY status")}
    ready = db.query("SELECT COUNT(*) n, COALESCE(SUM(amount), 0) a FROM ledger WHERE status = 'ready_to_pay'", one=True)
    return {
        "amount_pending": amt.get("Pending", 0), "amount_flagged": amt.get("Flagged", 0),
        "ready_count": ready["n"], "ready_amount": ready["a"],
        "processed": n, "counts": counts,
        "pct": {k: round(100 * v / n) if n else 0 for k, v in counts.items()},
        "auto_rate": round(100 * auto_ok / n) if n else 0,
        "avg_s": (lambda a: round(a, 2) if a < 1 else round(a, 1))(sum(ms) / len(ms) / 1000) if ms else 0,
        "saved": int((auto_ok + auto_rej) * s["cost_saved_per_invoice_inr"]),
        "hands_free": auto_ok + auto_rej,
        "dups": codes.count("duplicate") + codes.count("near_duplicate"),
        "injections": codes.count("prompt_injection"),
        "lookalikes": codes.count("lookalike_vendor"),
        "bank": codes.count("bank_changed"),
        "tokens": pt + ct, "cost": round(cost, 4),
        "cost_per": round(cost / n, 5) if n else 0,
    }


@app.route("/")
@security.login_required
def dashboard():
    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()[:60]
    vendor = (request.args.get("vendor") or "").strip()[:120]
    d_from, d_to = (request.args.get("from") or "")[:10], (request.args.get("to") or "")[:10]
    sql, params = "SELECT * FROM invoices WHERE 1=1", []
    if status in STATUSES:
        sql += " AND status = ?"
        params.append(status)
    if q:
        sql += " AND (vendor_name LIKE ? OR invoice_number LIKE ? OR po_number LIKE ? OR file_name LIKE ?"
        params += [f"%{q}%"] * 4
        try:                                               # an amount typed in the search box
            amount = float(q.replace(",", "").replace("₹", "").strip())
        except ValueError:
            amount = None                                  # ordinary text: no amount clause at all
        if amount is not None:
            sql += " OR ABS(total - ?) < 0.51"
            params.append(amount)
        sql += ")"
    if vendor:
        sql += " AND vendor_name = ?"
        params.append(vendor)
    if d_from:
        sql += " AND invoice_date >= ?"
        params.append(d_from)
    if d_to:
        sql += " AND invoice_date <= ?"
        params.append(d_to)
    invoices = db.query(sql + " ORDER BY id DESC LIMIT 100", params)
    ask = (request.args.get("ask") or "").strip()[:300]
    asked = None
    if ask:                                    # "Ask in plain English": read-only search, filters shown as chips
        asked = assistant.plain_english_search(ask)
        ids = [c["id"] for c in asked["invoices"]]
        invoices = db.query(f"SELECT * FROM invoices WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id DESC",
                            ids) if ids else []
        if ids and (asked["filters"].get("overdue") or asked["filters"].get("due_within_days") is not None):
            order = {i: n for n, i in enumerate(ids)}
            invoices = sorted(invoices, key=lambda r: order[r["id"]])
        db.log_audit(None, g.user["username"], g.user["role"], "plain_english_search", "info",
                     f"“{ask[:120]}” → {asked['count']} invoice(s) ({asked['how']})")
    ledger = {r["invoice_id"]: r["status"] for r in db.query("SELECT invoice_id, status FROM ledger")}
    due = {i["id"]: insights.due_info(i, ledger.get(i["id"])) for i in invoices}
    drafts = db.query("SELECT * FROM emails WHERE status='draft' ORDER BY id DESC LIMIT 3")
    role = g.user["role"]
    attention = db.query("""SELECT * FROM invoices WHERE status = ? ORDER BY id DESC LIMIT 6""",
                         ("Pending" if role == "manager" else "Flagged",))
    decisions = db.query("""SELECT i.*, u.full_name AS decider FROM invoices i JOIN users u ON u.id = i.decided_by
                            WHERE i.status IN ('Approved', 'Rejected') ORDER BY i.decided_at DESC LIMIT 6""")
    vendors = [r["vendor_name"] for r in db.query(
        "SELECT DISTINCT vendor_name FROM invoices WHERE vendor_name IS NOT NULL ORDER BY vendor_name")]
    open_dl = insights.open_deadlines()
    deadline_counts = {"overdue": sum(1 for d in open_dl if d["due"]["state"] == "overdue"),
                       "due_soon": sum(1 for d in open_dl if d["due"]["state"] == "due_soon")}
    return render_template("dashboard.html", invoices=invoices, s=stats(), status=status, q=q, drafts=drafts,
                           vendor=vendor, d_from=d_from, d_to=d_to, vendors=vendors, attention=attention,
                           decisions=decisions, unread_ids=notifications.unread_invoice_ids(g.user["id"]),
                           ask=ask, asked=asked, due=due, deadline_counts=deadline_counts)


@app.route("/api/feed")
@security.login_required
def api_feed():
    rows = db.query("""SELECT a.ts, a.step, a.result, a.reason, a.invoice_id, i.invoice_number, i.file_name
                       FROM audit_log a LEFT JOIN invoices i ON i.id = a.invoice_id
                       WHERE a.actor_type IN ('agent','system') AND a.invoice_id IS NOT NULL
                       ORDER BY a.id DESC LIMIT 8""")
    return jsonify([{"t": ist_hm(r["ts"]), "step": r["step"], "result": r["result"], "reason": r["reason"],
                     "invoice": r["invoice_number"] or r["file_name"], "id": r["invoice_id"]} for r in rows])


@app.route("/api/palette")
@security.login_required
def api_palette():
    """Recent invoices for the Ctrl+K quick-search palette (same rows the dashboard lists)."""
    rows = db.query("""SELECT id, invoice_number, file_name, vendor_name, po_number, total, status
                       FROM invoices ORDER BY id DESC LIMIT 200""")
    return jsonify([{"id": r["id"], "n": r["invoice_number"] or r["file_name"], "v": r["vendor_name"] or "",
                     "po": r["po_number"] or "", "amt": inr(r["total"]) if r["total"] is not None else "",
                     "st": status_label(r["status"]), "s": r["status"] or ""} for r in rows])


# ==========================================================================
# Invoice report
# ==========================================================================
def get_invoice(iid):
    inv = db.query("SELECT * FROM invoices WHERE id = ?", (iid,), one=True)
    if not inv:
        abort(404)
    return inv


@app.route("/invoice/<int:iid>")
@security.login_required
def invoice(iid):
    inv = get_invoice(iid)
    issues = json.loads(inv["issues_json"] or "[]")
    hl = json.loads(inv["highlights_json"] or "{}")
    text_view = None
    if inv["file_type"] == "txt" and os.path.exists(inv["stored_path"]):
        doc = tools.read_document(inv["stored_path"], "txt")
        text_view = mark_text(doc["text"], hl.get("text_marks", []))
    steps = db.query("SELECT * FROM audit_log WHERE invoice_id=? ORDER BY id", (iid,))
    emails = db.query("SELECT * FROM emails WHERE invoice_id=? ORDER BY id DESC", (iid,))
    ledger = db.query("SELECT * FROM ledger WHERE invoice_id=?", (iid,), one=True)
    uploader = db.query("SELECT full_name FROM users WHERE id=?", (inv["uploaded_by"],), one=True) if inv["uploaded_by"] else None
    decider = db.query("SELECT full_name FROM users WHERE id=?", (inv["decided_by"],), one=True) if inv["decided_by"] else None
    s = db.get_settings()
    cost = ((inv["prompt_tokens"] or 0) / 1e6 * s["groq_input_usd_per_m"] +
            (inv["completion_tokens"] or 0) / 1e6 * s["groq_output_usd_per_m"])
    # The AP user has now seen this invoice: clear its notifications, and record that the AP team
    # saw a manager's decision (once per person), so the timeline shows nobody missed it.
    notifications.mark_invoice_read(g.user["id"], iid)
    if g.user["role"] in ("ap", "admin") and inv["decided_by"] and inv["decided_by"] != g.user["id"] and \
            not db.query("SELECT 1 FROM audit_log WHERE invoice_id=? AND step='ap_seen' AND actor=?",
                         (iid, g.user["username"]), one=True):
        db.log_audit(iid, g.user["username"], g.user["role"], "ap_seen", "info",
                     f"{g.user['full_name']} opened the invoice after the manager's decision")
        steps = db.query("SELECT * FROM audit_log WHERE invoice_id=? ORDER BY id", (iid,))
    inv_row = db.query("SELECT * FROM investigations WHERE invoice_id=? ORDER BY id DESC", (iid,), one=True)
    investigation = None
    if inv_row:
        investigation = dict(inv_row)
        investigation.update(findings=json.loads(inv_row["findings_json"] or "[]"),
                             steps=json.loads(inv_row["steps_json"] or "[]"),
                             similar=json.loads(inv_row["similar_json"] or "[]"),
                             stale=bool(inv["processed_at"] and inv_row["invoice_processed_at"] != inv["processed_at"]),
                             email=db.query("SELECT * FROM emails WHERE id=?", (inv_row["email_id"],), one=True)
                             if inv_row["email_id"] else None)
    risk = insights.vendor_risk(inv["vendor_id"]) if inv["vendor_id"] else None
    return render_template("invoice.html", inv=inv, issues=issues, ex=json.loads(inv["extracted_json"] or "null"),
                           cmp=json.loads(inv["comparison_json"] or "{}"), hl=hl, text_view=text_view, steps=steps,
                           emails=emails, ledger=ledger, uploader=uploader, decider=decider, cost=cost,
                           facts=invoice_facts(inv, ledger, steps), timeline=workflow_timeline(steps),
                           investigation=investigation, investigating=investigator.is_running(iid), risk=risk,
                           rec_labels=investigator.LABELS)


def invoice_facts(inv, ledger, steps):
    """The few things an AP user needs first, in plain words."""
    deadline = insights.due_info(inv, ledger["status"] if ledger else None)
    due = datetime.fromisoformat(deadline["due_date"]).strftime("%d %b %Y") if deadline["due_date"] else None
    if inv["status"] == "Approved":
        approval = "Approved by manager" if inv["decided_by"] else "Approved automatically"
    else:
        approval = {"Pending": "Waiting for a manager", "Flagged": "Not sent for approval yet",
                    "Rejected": "Rejected", "Processing": "Being checked"}.get(inv["status"], inv["status"])
    payment = status_label(ledger["status"]) if ledger else "Not in the ledger"
    owner = {"Pending": "Manager", "Flagged": "AP team", "Processing": "ClearPay agent"}.get(inv["status"], "No action needed")
    return {"due": due, "deadline": deadline, "terms": inv["payment_terms_days"], "approval": approval,
            "payment": payment, "owner": owner, "updated": steps[-1]["ts"] if steps else inv["created_at"]}


_TIMELINE = {  # audit step -> (how to say it, tone)
    "upload": ("Uploaded by {who}", "person"), "inbox_pickup": ("Picked up from the inbox folder", ""),
    "email_pickup": ("{reason}", ""), "investigation": ("{reason}", ""),
    "payment_recorded": ("{reason}", "ok"),
    "extract_invoice_data": ("Invoice read by the agent", ""), "decision": ("Checks finished: {reason}", "auto"),
    "send_to_manager": ("{who} sent it to a manager", "person"), "reprocess": ("{who} asked to process it again", "person"),
    "manager_approve": ("Approved by {who}", "ok"), "manager_reject": ("Rejected by {who}", "bad"),
    "ap_notified": ("{reason}", ""), "ap_seen": ("Seen by {who}", "person"),
    "email_sent": ("Email sent: {reason}", ""), "email_failed": ("Email failed: {reason}", "bad"),
    "segregation_of_duties": ("Blocked: {who} can't decide on an invoice they uploaded", "bad"),
}


def workflow_timeline(steps):
    users = {u["username"]: u["full_name"] for u in db.query("SELECT username, full_name FROM users")}
    out = []
    for r in steps:
        if r["step"] not in _TIMELINE:
            continue
        text, tone = _TIMELINE[r["step"]]
        if r["step"] == "decision":
            first = (r["reason"] or "").split(":")[0]
            tone = {"Approved": "ok", "Pending": "warn", "Flagged": "bad", "Rejected": "bad"}.get(first, "")
            reason = status_label(first) + ((" — " + r["reason"].split(":", 1)[1].strip()) if ":" in (r["reason"] or "") else "")
        else:
            reason = r["reason"] or ""
        out.append({"ts": r["ts"], "tone": tone,
                    "text": text.format(who=users.get(r["actor"], r["actor"]), reason=reason)})
    return out


@app.route("/invoice/<int:iid>/status")
@security.login_required
def invoice_status(iid):
    inv = get_invoice(iid)
    steps = db.query("SELECT ts, step, result, reason FROM audit_log WHERE invoice_id=? AND actor_type IN "
                     "('agent','system') ORDER BY id", (iid,))
    return jsonify({"id": iid, "status": inv["status"], "status_label": status_label(inv["status"]),
                    "reason": inv["decision_reason"], "file": inv["file_name"], "ms": inv["processing_ms"],
                    "steps": [{"t": ist_hm(r["ts"]), "step": r["step"], "result": r["result"], "reason": r["reason"]}
                              for r in steps]})


@app.route("/invoice/<int:iid>/file")
@security.login_required
def invoice_file(iid):
    inv = get_invoice(iid)
    return send_file(inv["stored_path"], as_attachment=True, download_name=inv["file_name"])


@app.route("/invoice/<int:iid>/page/<int:n>.png")
@security.login_required
def invoice_page(iid, n):
    inv = get_invoice(iid)
    hl = json.loads(inv["highlights_json"] or "{}")
    pages = hl.get("pages", [])
    if not 1 <= n <= len(pages):
        abort(404)
    path = os.path.join(os.path.dirname(inv["stored_path"]), os.path.basename(pages[n - 1]))
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="image/png")


def _decide(iid, new_status):
    """Shared approve / reject logic for managers (and AP for flagged items)."""
    inv = get_invoice(iid)
    action = "approve" if new_status == "Approved" else "reject"
    if not can(action, inv):
        if inv["uploaded_by"] == g.user["id"]:
            db.log_audit(iid, g.user["username"], g.user["role"], "segregation_of_duties", "blocked",
                         "Tried to decide on an invoice they uploaded")
            flash("You uploaded this invoice, so someone else has to decide on it.", "error")
            return redirect(request.referrer or url_for("approvals"))
        abort(403)
    comment = (request.form.get("comment") or "").strip()[:500]
    now = db.now_iso()
    db.execute("UPDATE invoices SET status=?, decided_by=?, decided_at=?, manager_comment=? WHERE id=?",
               (new_status, g.user["id"], now, comment, iid))
    led = db.query("SELECT * FROM ledger WHERE invoice_id=?", (iid,), one=True)
    if new_status == "Approved":
        if led:
            db.execute("UPDATE ledger SET status='ready_to_pay', updated_at=? WHERE invoice_id=?", (now, iid))
        else:
            db.execute("""INSERT INTO ledger(invoice_id, vendor_id, po_number, amount, status, created_at, updated_at, note)
                          VALUES (?,?,?,?, 'ready_to_pay', ?, ?, ?)""",
                       (iid, inv["vendor_id"], inv["po_number"], inv["total"] or 0, now, now,
                        f"Approved by {g.user['username']}. No payment made."))
    elif led:
        db.execute("UPDATE ledger SET status='cancelled', updated_at=? WHERE invoice_id=?", (now, iid))
    rec = investigator.record_human_decision(iid, new_status.lower())
    db.log_audit(iid, g.user["username"], g.user["role"], "manager_" + action, "ok",
                 f"{new_status} by {g.user['full_name']}" + (f": {comment}" if comment else ""),
                 {"investigator_recommended": rec} if rec else None)
    purpose = "approved_notice" if new_status == "Approved" else "rejected_notice"
    to, subject, body = tools.build_email(iid, "ap_team", purpose, extra={
        "by": g.user["full_name"], "at": dt(now), "comment": comment})
    tools.save_email(iid, "ap_team", purpose, to, subject, body, g.user["username"])
    db.log_audit(iid, "agent", "agent", "draft_email", "ok", f"Drafted '{purpose}' email to the AP team ({to})")
    try:
        n = notifications.on_manager_decision(dict(inv), dict(g.user), new_status == "Approved", comment, now)
    except Exception:
        n = 0
    flash(f"{inv['invoice_number'] or inv['file_name']} {new_status.lower()}. "
          f"{n} AP team member{'s were' if n != 1 else ' was'} notified and the AP email is drafted.", "ok")
    return redirect(request.referrer or url_for("approvals"))


@app.route("/invoice/<int:iid>/approve", methods=["POST"])
@security.role_required("manager", "admin")
def approve(iid):
    return _decide(iid, "Approved")


@app.route("/invoice/<int:iid>/reject", methods=["POST"])
@security.login_required
def reject(iid):
    return _decide(iid, "Rejected")


@app.route("/invoice/<int:iid>/send-to-manager", methods=["POST"])
@security.role_required("ap", "admin")
def send_to_manager(iid):
    inv = get_invoice(iid)
    if not can("send_to_manager", inv):
        abort(403)
    note = (request.form.get("comment") or "").strip()[:300]
    db.execute("UPDATE invoices SET status='Pending', decision_reason=? WHERE id=?",
               (f"Sent to manager by AP: {note or inv['decision_reason']}", iid))
    if not db.query("SELECT 1 FROM ledger WHERE invoice_id=?", (iid,), one=True):
        now = db.now_iso()
        db.execute("""INSERT INTO ledger(invoice_id, vendor_id, po_number, amount, status, created_at, updated_at, note)
                      VALUES (?,?,?,?, 'pending_approval', ?, ?, 'Sent to manager by AP team')""",
                   (iid, inv["vendor_id"], inv["po_number"], inv["total"] or 0, now, now))
    investigator.record_human_decision(iid, "sent_to_manager")
    db.log_audit(iid, g.user["username"], g.user["role"], "send_to_manager", "info",
                 f"AP reviewed the flag and sent it for approval" + (f": {note}" if note else ""))
    to, subject, body = tools.build_email(iid, "manager", "approval_request")
    tools.save_email(iid, "manager", "approval_request", to, subject, body, g.user["username"])
    notifications.on_sent_to_manager(dict(get_invoice(iid)), dict(g.user), note)
    flash("Sent to the manager for approval. Managers were notified.", "ok")
    return redirect(url_for("invoice", iid=iid))


@app.route("/invoice/<int:iid>/reprocess", methods=["POST"])
@security.role_required("ap", "admin")
def reprocess(iid):
    inv = get_invoice(iid)
    if not can("reprocess", inv):
        abort(403)
    db.execute("DELETE FROM ledger WHERE invoice_id=? AND status != 'ready_to_pay'", (iid,))
    db.execute("UPDATE invoices SET status='Processing' WHERE id=?", (iid,))
    db.log_audit(iid, g.user["username"], g.user["role"], "reprocess", "info", "Asked the agent to process again")
    agent.submit(iid)
    return redirect(url_for("upload", ids=iid))


@app.route("/invoice/<int:iid>/email", methods=["POST"])
@security.login_required
def compose_email(iid):
    get_invoice(iid)
    recipient = request.form.get("recipient", "")
    purpose = request.form.get("purpose", "report")
    to_email = (request.form.get("to_email") or "").strip()
    if recipient not in tools.RECIPIENTS or purpose not in tools.PURPOSES:
        abort(400)
    if g.user["role"] == "manager" and (recipient == "manager" or purpose == "approval_request"):
        flash("Managers can't send an approval request to themselves.", "error")
        return redirect(url_for("invoice", iid=iid) + "#email")
    if recipient == "custom" and not security.valid_email(to_email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("invoice", iid=iid) + "#email")
    to, subject, body = tools.build_email(iid, recipient, purpose, to_email)
    eid = tools.save_email(iid, recipient, purpose, to, subject, body, g.user["username"])
    if eid is None:
        existing = db.query("SELECT id FROM emails WHERE invoice_id=? AND purpose=? AND to_addr=? AND recipient_type=?",
                            (iid, purpose, to or "(vendor email unknown — fill in)", recipient), one=True)
        eid = existing["id"] if existing else None
    db.log_audit(iid, g.user["username"], g.user["role"], "draft_email", "ok", f"Drafted '{purpose}' email to {to}")
    return redirect(url_for("email_detail", eid=eid)) if eid else redirect(url_for("emails"))


@app.route("/invoice/<int:iid>/investigate", methods=["POST"])
@security.role_required("ap", "manager", "admin")
def investigate(iid):
    """Ask the Investigator to look at this invoice again. It only recommends; nothing about the invoice changes."""
    inv = get_invoice(iid)
    if not can("investigate", inv):
        abort(403)
    if security.rate_limited("investigate", 6, 60):
        flash("Too many investigations in a minute. Wait a moment (this protects the AI service's limits).", "error")
        return redirect(url_for("invoice", iid=iid))
    investigator.submit(iid, requested_by=g.user["username"])
    db.log_audit(iid, g.user["username"], g.user["role"], "investigation_requested", "info", "Asked the Investigator to look at it")
    flash("The Investigator is looking at this invoice. Refresh in a few seconds.", "ok")
    return redirect(url_for("invoice", iid=iid) + "#investigation")


@app.route("/invoice/<int:iid>/record-payment", methods=["POST"])
@security.role_required("ap", "admin")
def record_payment(iid):
    """A PERSON records that the payment was made outside ClearPay. ClearPay itself never pays."""
    inv = get_invoice(iid)
    led = db.query("SELECT * FROM ledger WHERE invoice_id=?", (iid,), one=True)
    if not can("record_payment", inv) or not led or led["status"] != "ready_to_pay":
        abort(403)
    ref = " ".join((request.form.get("payment_ref") or "").split())[:80]
    now = db.now_iso()
    db.execute("UPDATE ledger SET status='paid', paid_at=?, paid_by=?, updated_at=?, note=? WHERE invoice_id=?",
               (now, g.user["id"], now, f"Payment recorded by {g.user['username']}" + (f" (ref {ref})" if ref else "")
                + ". Paid outside ClearPay.", iid))
    db.log_audit(iid, g.user["username"], g.user["role"], "payment_recorded", "ok",
                 f"Payment recorded by {g.user['full_name']}" + (f", reference {ref}" if ref else ""))
    flash(f"Payment recorded for {inv['invoice_number'] or inv['file_name']}. It has left the deadline list.", "ok")
    return redirect(request.referrer or url_for("invoice", iid=iid))


# ==========================================================================
# Ask ClearPay (chat over read-only tools) and the Help guide
# ==========================================================================
ASK_EXAMPLES = ["Why was {inv} flagged?", "Show flagged invoices over ₹50,000 from {vendor}",
                "Show me the previous issues with {vendor}", "Which invoices are overdue?",
                "How much money has ClearPay saved?"]


@app.route("/ask")
@security.login_required
def ask_page():
    ctx_inv = None
    if (request.args.get("invoice") or "").isdigit():
        ctx_inv = db.query("SELECT id, invoice_number, file_name FROM invoices WHERE id=?", (int(request.args["invoice"]),),
                           one=True)
    flagged = db.query("SELECT invoice_number FROM invoices WHERE status='Flagged' AND invoice_number IS NOT NULL "
                       "ORDER BY id DESC", one=True)
    top_vendor = db.query("SELECT name FROM vendors ORDER BY id", one=True)
    fill = {"inv": flagged["invoice_number"] if flagged else "BET-3002",
            "vendor": (top_vendor["name"].split()[0] if top_vendor else "Alpha")}
    examples = [e.format(**fill) for e in ASK_EXAMPLES]
    if ctx_inv:
        examples = ["What happened with this invoice?", "Why did this invoice fail matching?",
                    "What did managers decide in similar cases?"] + examples[3:4]
    return render_template("ask.html", examples=examples, ctx_inv=ctx_inv)


@app.route("/api/ask", methods=["POST"])
@security.login_required
def api_ask():
    if security.rate_limited("ask", 20, 60):
        return jsonify({"answer": "Too many questions in a minute. Wait a moment and ask again.", "sources": [],
                        "mode": "rules"}), 429
    question = (request.form.get("q") or "").strip()[:500]
    try:
        history = json.loads(request.form.get("history") or "[]")
    except ValueError:
        history = []
    ctx = request.form.get("invoice") or None
    res = assistant.ask(question, history if isinstance(history, list) else [], ctx if ctx and ctx.isdigit() else None)
    db.log_audit(int(ctx) if ctx and ctx.isdigit() else None, g.user["username"], g.user["role"], "ask_clearpay", "info",
                 f"Asked: “{question[:160]}” ({res['mode']}, tools: {', '.join(res.get('tools', [])) or 'none'})")
    return jsonify(res)


@app.route("/api/help", methods=["POST"])
@security.login_required
def api_help():
    if security.rate_limited("help", 30, 60):
        return jsonify({"answer": "Too many questions in a minute. Wait a moment.", "links": []}), 429
    res = helpbot.answer(request.form.get("q") or "", g.user["role"])
    links = []
    for ln in res["links"]:
        endpoint, _, qs = ln["endpoint"].partition("?")
        try:
            links.append({"label": ln["label"], "url": url_for(endpoint) + ("?" + qs if qs else "")})
        except Exception:
            continue
    return jsonify({"answer": res["answer"], "links": links, "mode": res["mode"]})


# ==========================================================================
# Payment deadlines, vendors (risk), analytics
# ==========================================================================
@app.route("/deadlines")
@security.login_required
def deadlines():
    show = request.args.get("show", "open")
    rows = insights.open_deadlines()
    counts = {k: sum(1 for r in rows if r["due"]["state"] == k) for k in ("overdue", "due_soon", "ok", "unknown")}
    amounts = {k: sum(r["total"] or 0 for r in rows if r["due"]["state"] == k) for k in ("overdue", "due_soon")}
    if show in ("overdue", "due_soon"):
        rows = [r for r in rows if r["due"]["state"] == show]
    return render_template("deadlines.html", rows=rows, counts=counts, amounts=amounts, show=show,
                           s=db.get_settings())


@app.route("/deadlines/payment-file.csv")
@security.role_required("ap", "admin")
def payment_file_csv():
    """Approved invoices ready for payment, as a file for the bank or the finance team. ClearPay still never pays:
    a person uploads this to the bank, then records each payment here."""
    rows = [r for r in insights.open_deadlines() if r["ledger_status"] == "ready_to_pay"]
    vendors = {v["id"]: dict(v) for v in db.query("SELECT id, name, gstin, bank_account, ifsc, msme FROM vendors")}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["invoice_id", "invoice_number", "vendor", "gstin", "bank_account", "ifsc", "msme", "po_number",
                "amount_inr", "invoice_date", "due_date", "deadline"])
    safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v   # CSV-injection guard
    for r in rows:
        v = vendors.get(r["vendor_id"]) or {}
        w.writerow([safe(x) for x in (r["id"], r["invoice_number"] or r["file_name"], v.get("name") or r["vendor_name"],
                                       v.get("gstin"), v.get("bank_account"), v.get("ifsc"),
                                       "yes" if v.get("msme") else "no", r["po_number"], f"{(r['total'] or 0):.2f}",
                                       r["invoice_date"], r["due"]["due_date"], r["due"]["label"])])
    db.log_audit(None, g.user["username"], g.user["role"], "payment_file_export", "info",
                 f"Exported {len(rows)} invoices ready for payment")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=payment_file.csv"})


@app.route("/vendors")
@security.login_required
def vendors_page():
    rows = insights.vendor_overview()
    sel_id = request.args.get("v", "")
    sel = next((r for r in rows if str(r["id"]) == sel_id), rows[0] if rows else None)
    profile = retrieval.vendor_profile(sel["name"]) if sel else None
    return render_template("vendors.html", rows=rows, sel=sel, profile=profile)


@app.route("/analytics")
@security.login_required
def analytics():
    a = insights.analytics()
    total = a["spend_total"] or 0
    # Spend by vendor as columns: the top 4, the rest as "Other vendors"
    vendors = a["spend_by_vendor"]
    cols = vendors[:4] + ([{"label": "Other vendors", "amount": sum(v["amount"] for v in vendors[4:])}] if len(vendors) > 4 else [])
    top = max((c["amount"] for c in cols), default=0)
    breakdown = [{"label": c["label"], "amount": c["amount"], "pct": round(100 * c["amount"] / total) if total else 0,
                  "h": max(30, round(100 * c["amount"] / top)) if top else 30} for c in cols]
    # Spend by month as bars on a "nice" axis (0, then 4 even steps up to a round number)
    peak = max((m["amount"] for m in a["spend_by_month"]), default=0)
    axis_max = 4 * _nice_ceiling(peak / 4)
    months = [{"label": datetime.strptime(m["label"], "%Y-%m").strftime("%b %y"), "amount": m["amount"],
               "h": round(100 * m["amount"] / axis_max, 1) if axis_max else 0} for m in a["spend_by_month"]]
    ticks = [axis_max * i / 4 for i in range(4, -1, -1)] if axis_max else []
    return render_template("analytics.html", a=a, breakdown=breakdown, months=months, ticks=ticks,
                           notes=_analytics_insights(a, breakdown))


def _nice_ceiling(x):
    """The smallest 1, 2, 2.5 or 5 × 10^k that is at least x (for chart axes)."""
    if x <= 0:
        return 0
    k = 10 ** math.floor(math.log10(x))
    return next(f * k for f in (1, 2, 2.5, 5, 10) if f * k >= x)


def _analytics_insights(a, breakdown):
    """Plain-language findings worked out from the same numbers (no AI involved)."""
    notes = []
    if breakdown and a["spend_total"]:
        big = breakdown[0]
        notes.append({"tone": "info", "title": f"{big['label']} is your largest supplier",
                      "body": f"{big['pct']}% of approved spend ({tools.fmt_inr(big['amount'])}) goes to {big['label']}. "
                              "A large share with one supplier is worth a price review.", "link": url_for("vendors_page")})
    if a["fraud_count"]:
        kind = a["fraud_by_type"][0]["type"].lower() if a["fraud_by_type"] else "fraud signs"
        notes.append({"tone": "bad", "title": f"{a['fraud_count']} risky invoice{'s' if a['fraud_count'] != 1 else ''} stopped before payment",
                      "body": f"{tools.fmt_inr(a['fraud_amount'])} was held back. The most common reason: {kind}.",
                      "link": url_for("dashboard", status="Flagged")})
    if a["overdue"]:
        soon = f", and {a['due_soon']} more fall due within 7 days" if a["due_soon"] else ""
        notes.append({"tone": "warn", "title": f"{a['overdue']} invoice{'s are' if a['overdue'] != 1 else ' is'} overdue",
                      "body": f"{tools.fmt_inr(a['overdue_amount'])} is past its due date{soon}. "
                              "MSME suppliers must be paid within 45 days.", "link": url_for("deadlines", show="overdue")})
    if a["on_hold"]:
        notes.append({"tone": "warn", "title": f"{tools.fmt_inr(a['on_hold'])} is waiting for a review",
                      "body": "Flagged invoices and invoices waiting for a manager. Nothing is paid until a person decides.",
                      "link": url_for("dashboard")})
    if a["ai"]["agreement_pct"] is not None:
        notes.append({"tone": "ok", "title": f"People agreed with the AI {a['ai']['agreement_pct']}% of the time",
                      "body": f"{a['ai']['agreed']} of {a['ai']['decided']} decisions matched the Investigator's recommendation. "
                              "The AI recommends; people decide.", "link": url_for("compare")})
    return notes


# ==========================================================================
# Approvals, upload, POs, emails, audit
# ==========================================================================
@app.route("/approvals")
@security.role_required("manager", "admin")
def approvals():
    sort = request.args.get("sort", "oldest")
    items = db.query("SELECT * FROM invoices WHERE status='Pending' ORDER BY total DESC" if sort == "amount"
                     else "SELECT * FROM invoices WHERE status='Pending' ORDER BY id ASC")
    recent = db.query("""SELECT i.*, u.full_name AS decider FROM invoices i JOIN users u ON u.id = i.decided_by
                         ORDER BY i.decided_at DESC LIMIT 6""")
    return render_template("approvals.html", items=items, recent=recent, sort=sort,
                           issues={i["id"]: json.loads(i["issues_json"] or "[]") for i in items})


@app.route("/upload", methods=["GET", "POST"])
@security.role_required("ap", "admin")
def upload():
    if request.method == "POST":
        if security.rate_limited("upload", 20, 60):
            flash("Too many uploads in a minute. Wait a moment and try again.", "error")
            return redirect(url_for("upload"))
        po_hint = (request.form.get("po_hint") or "").strip()[:30] or None
        report_email = (request.form.get("report_email") or "").strip() or None
        if report_email and not security.valid_email(report_email):
            flash("The report email address isn't valid.", "error")
            return redirect(url_for("upload"))
        if po_hint and not tools.normalize_po(po_hint):
            flash("The PO number should look like PO-2013.", "error")
            return redirect(url_for("upload"))
        ids, errors = [], []
        for f in request.files.getlist("files")[:10]:
            if not f or not f.filename:
                continue
            data = f.read(security.MAX_UPLOAD_BYTES + 1)
            ok, problem = security.check_upload(f.filename, data)
            if not ok:
                errors.append(f"{f.filename[:60]}: {problem}")
                db.log_audit(None, g.user["username"], g.user["role"], "upload_rejected", "blocked",
                             f"'{f.filename[:60]}': {problem}")
                continue
            stored, display, ext = security.store_file(f.filename, data)
            iid = agent.create_invoice(stored, display, ext, "upload", g.user["id"], po_hint, report_email)
            db.log_audit(iid, g.user["username"], g.user["role"], "upload", "info", f"Uploaded '{display}'")
            agent.submit(iid)
            ids.append(iid)
        for e in errors:
            flash(e, "error")
        if not ids and not errors:
            flash("Choose at least one file.", "error")
        return redirect(url_for("upload", ids=",".join(map(str, ids))) if ids else url_for("upload"))
    ids = [int(x) for x in (request.args.get("ids") or "").split(",") if x.isdigit()][:10]
    recent = db.query("SELECT * FROM invoices WHERE uploaded_by=? ORDER BY id DESC LIMIT 8", (g.user["id"],))
    return render_template("upload.html", ids=ids, recent=recent)


PO_PAGE_SIZE = 100


@app.route("/pos")
@security.login_required
def pos():
    """Purchase orders are master data loaded separately (load_po_data.py); this page only reads them."""
    q = (request.args.get("q") or "").strip().lower()[:40]
    like = f"%{q}%"
    total = db.query("""SELECT COUNT(*) n FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id
                        WHERE (? = '' OR LOWER(p.po_number) LIKE ? OR LOWER(v.name) LIKE ?)""", (q, like, like), one=True)["n"]
    rows = db.query("""SELECT p.*, v.name AS vendor_name,
        COALESCE((SELECT SUM(total) FROM invoices i WHERE i.po_number=p.po_number AND i.status='Approved'),0) AS billed,
        COALESCE((SELECT SUM(total) FROM invoices i WHERE i.po_number=p.po_number AND i.status='Pending'),0) AS pending,
        (SELECT COUNT(*) FROM invoices i WHERE i.po_number=p.po_number AND i.status='Flagged') AS flagged
        FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id
        WHERE (? = '' OR LOWER(p.po_number) LIKE ? OR LOWER(v.name) LIKE ?)
        ORDER BY p.po_number LIMIT ?""", (q, like, like, PO_PAGE_SIZE))
    sel = request.args.get("po") or (rows[0]["po_number"] if rows else None)
    po = db.po_with_lines(sel) if sel else None
    invs = db.query("SELECT * FROM invoices WHERE po_number=? ORDER BY id DESC", (sel,)) if po else []
    return render_template("pos.html", rows=rows, po=po, invs=invs, q=q, total=total)


PO_LINE_ROWS = 8


def _po_form_data():
    """Read and check the PO form. Totals are worked out here, in code. Returns (data, error)."""
    f = request.form
    vid = f.get("vendor_id", "")
    vendor = db.query("SELECT * FROM vendors WHERE id=? AND active=1", (int(vid),), one=True) if vid.isdigit() else None
    if not vendor:
        return None, "Choose a vendor."
    issue_date = (f.get("issue_date") or "")[:10]
    try:
        datetime.strptime(issue_date, "%Y-%m-%d")
    except ValueError:
        return None, "Enter the issue date."
    try:
        tax_rate = float(f.get("tax_pct") or "18") / 100
    except ValueError:
        return None, "The GST rate must be a number."
    if not 0 <= tax_rate <= 0.5:
        return None, "The GST rate must be between 0 and 50%."
    lines = []
    for i in range(PO_LINE_ROWS):
        desc = " ".join((f.get(f"desc_{i}") or "").split())[:120]
        qty, price = f.get(f"qty_{i}", "").strip(), f.get(f"price_{i}", "").strip()
        if not (desc or qty or price):
            continue
        try:
            q, p = float(qty), float(price.replace(",", ""))
        except ValueError:
            return None, f"Line {i + 1}: quantity and unit price must be numbers."
        if not desc or q <= 0 or p < 0 or q > 1e7 or p > 1e9:
            return None, f"Line {i + 1}: enter a description, a quantity above 0 and a price of 0 or more."
        lines.append((desc, q, p))
    if not lines:
        return None, "Add at least one line."
    subtotal = db.money(sum(db.money(q * p) for _, q, p in lines))
    tax = db.money(subtotal * tax_rate)
    status = f.get("status") if f.get("status") in ("open", "closed") else "open"
    return {"vendor": vendor, "issue_date": issue_date, "tax_rate": tax_rate, "lines": lines, "subtotal": subtotal,
            "tax": tax, "total": db.money(subtotal + tax), "status": status}, None


def _next_po_number():
    nums = [int(r["po_number"].split("-")[1]) for r in db.query("SELECT po_number FROM purchase_orders")
            if r["po_number"].split("-")[-1].isdigit()]
    return f"PO-{max(nums or [2000]) + 1}"


@app.route("/pos/new", methods=["GET", "POST"])
@security.role_required("admin")
def po_new():
    """Purchase orders are controlled business records: only an admin creates them (no AI involved)."""
    form = {"po_number": _next_po_number(), "issue_date": ist_now().date().isoformat(), "tax_pct": 18,
            "vendor_id": None, "lines": [], "status": "open"}
    if request.method == "POST":
        po_number = tools.normalize_po(request.form.get("po_number"))
        data, err = _po_form_data()
        if not po_number:
            err = "The PO number should look like PO-2050."
        elif db.query("SELECT 1 FROM purchase_orders WHERE po_number=?", (po_number,), one=True):
            err = f"{po_number} already exists."
        if err:
            flash(err, "error")
            form.update(request.form.to_dict())
            return render_template("po_form.html", form=form, vendors=_active_vendors(), rows=PO_LINE_ROWS, edit=False)
        po_id = db.execute("""INSERT INTO purchase_orders(po_number, vendor_id, issue_date, subtotal, tax_rate, tax, total,
                              status) VALUES (?,?,?,?,?,?,?,?)""",
                           (po_number, data["vendor"]["id"], data["issue_date"], data["subtotal"], data["tax_rate"],
                            data["tax"], data["total"], data["status"]))
        for desc, q, p in data["lines"]:
            db.execute("INSERT INTO po_lines(po_id, description, quantity, unit_price) VALUES (?,?,?,?)", (po_id, desc, q, p))
        db.log_audit(None, g.user["username"], "admin", "po_created", "info",
                     f"Created {po_number} for {data['vendor']['name']}: {len(data['lines'])} line(s), "
                     f"{tools.fmt_inr(data['total'])}")
        flash(f"{po_number} created.", "ok")
        return redirect(url_for("pos", po=po_number))
    return render_template("po_form.html", form=form, vendors=_active_vendors(), rows=PO_LINE_ROWS, edit=False)


@app.route("/pos/<po_number>/edit", methods=["GET", "POST"])
@security.role_required("admin")
def po_edit(po_number):
    po = db.po_with_lines(tools.normalize_po(po_number) or "")
    if not po:
        abort(404)
    form = {"po_number": po["po_number"], "issue_date": po["issue_date"], "tax_pct": round(po["tax_rate"] * 100, 2),
            "vendor_id": po["vendor_id"], "status": po["status"],
            "lines": [(ln["description"], ln["quantity"], ln["unit_price"]) for ln in po["lines"]]}
    if request.method == "POST":
        data, err = _po_form_data()
        if err:
            flash(err, "error")
            return redirect(url_for("po_edit", po_number=po["po_number"]))
        before = f"{tools.fmt_inr(po['total'])}, {len(po['lines'])} line(s), {po['status']}"
        db.execute("""UPDATE purchase_orders SET vendor_id=?, issue_date=?, subtotal=?, tax_rate=?, tax=?, total=?, status=?
                      WHERE id=?""", (data["vendor"]["id"], data["issue_date"], data["subtotal"], data["tax_rate"],
                                      data["tax"], data["total"], data["status"], po["id"]))
        db.execute("DELETE FROM po_lines WHERE po_id=?", (po["id"],))
        for desc, q, p in data["lines"]:
            db.execute("INSERT INTO po_lines(po_id, description, quantity, unit_price) VALUES (?,?,?,?)",
                       (po["id"], desc, q, p))
        db.log_audit(None, g.user["username"], "admin", "po_edited", "info",
                     f"Edited {po['po_number']}: {before} → {tools.fmt_inr(data['total'])}, {len(data['lines'])} "
                     f"line(s), {data['status']}")
        flash(f"{po['po_number']} saved. Invoices checked from now on use the new lines.", "ok")
        return redirect(url_for("pos", po=po["po_number"]))
    return render_template("po_form.html", form=form, vendors=_active_vendors(), rows=PO_LINE_ROWS, edit=True)


def _active_vendors():
    return db.query("SELECT id, name FROM vendors WHERE active=1 ORDER BY name")


@app.route("/emails")
@security.login_required
def emails():
    kind = request.args.get("kind", "")
    sql, params = "SELECT e.*, i.invoice_number FROM emails e LEFT JOIN invoices i ON i.id=e.invoice_id", []
    if kind in tools.RECIPIENTS:
        sql += " WHERE e.recipient_type=?"
        params.append(kind)
    rows = db.query(sql + " ORDER BY e.id DESC LIMIT 100", params)
    return render_template("emails.html", rows=rows, kind=kind, sel=None)


@app.route("/emails/<int:eid>", methods=["GET", "POST"])
@security.login_required
def email_detail(eid):
    e = db.query("SELECT * FROM emails WHERE id=?", (eid,), one=True)
    if not e:
        abort(404)
    if request.method == "POST":
        to = (request.form.get("to_addr") or "").strip()
        subject = " ".join((request.form.get("subject") or "").split())[:200]   # one line: no header injection
        body = (request.form.get("body") or "")[:8000]
        if not security.valid_email(to):
            flash("Enter a valid email address before saving.", "error")
            return redirect(url_for("email_detail", eid=eid))
        action = request.form.get("action")
        if action == "regenerate" and e["invoice_id"]:
            to2, subject, body = tools.build_email(e["invoice_id"], e["recipient_type"], e["purpose"],
                                                   to if e["recipient_type"] == "custom" else None)
            to = to2 or to
        if action == "send":
            if security.rate_limited("send_email", 30, 60):
                flash("Too many emails sent in a minute. Wait a moment.", "error")
                return redirect(url_for("email_detail", eid=eid))
            ok, delivered_to, err = mailer.send(to, subject, body)
            if ok:
                db.execute("UPDATE emails SET to_addr=?, subject=?, body=?, status='sent', sent_at=?, last_error=NULL "
                           "WHERE id=?", (to, subject, body, db.now_iso(), eid))
                where = (f"delivered to {delivered_to}" + (f" (test mode, meant for {to})" if delivered_to != to else "")
                         if delivered_to else "marked as sent (demo mode: nothing left the app)")
                db.log_audit(e["invoice_id"], g.user["username"], g.user["role"], "email_sent", "ok",
                             f"'{subject[:80]}' {where}")
                if e["purpose"] == "investigation" and e["invoice_id"]:
                    inv_row = db.query("SELECT human_decision FROM investigations WHERE invoice_id=? ORDER BY id DESC",
                                       (e["invoice_id"],), one=True)
                    if inv_row and not inv_row["human_decision"]:
                        investigator.record_human_decision(e["invoice_id"], "asked_vendor")
                flash(f"Email {where}.", "mail")
            else:
                db.execute("UPDATE emails SET to_addr=?, subject=?, body=?, status='failed', last_error=? WHERE id=?",
                           (to, subject, body, err, eid))
                db.log_audit(e["invoice_id"], g.user["username"], g.user["role"], "email_failed", "problem",
                             f"'{subject[:80]}' to {to}: {err}")
                flash(f"Not sent: {err}", "error")
        else:
            db.execute("UPDATE emails SET to_addr=?, subject=?, body=? WHERE id=?", (to, subject, body, eid))
            db.log_audit(e["invoice_id"], g.user["username"], g.user["role"], "email_" + (action or "save"), "info",
                         f"Updated email '{subject[:80]}' to {to}")
            flash("Draft saved.", "ok")
        return redirect(url_for("email_detail", eid=eid))
    rows = db.query("SELECT e.*, i.invoice_number FROM emails e LEFT JOIN invoices i ON i.id=e.invoice_id "
                    "ORDER BY e.id DESC LIMIT 100")
    return render_template("emails.html", rows=rows, kind="", sel=e)


def _audit_rows(limit=300):
    f = {k: (request.args.get(k) or "").strip()[:40] for k in ("invoice", "who", "step", "date")}
    sql, params = """SELECT a.*, i.invoice_number, i.file_name FROM audit_log a
                     LEFT JOIN invoices i ON i.id = a.invoice_id WHERE 1=1""", []
    if f["invoice"]:
        sql += " AND (CAST(a.invoice_id AS TEXT) = ? OR i.invoice_number LIKE ?)"
        params += [f["invoice"], f"%{f['invoice']}%"]
    if f["who"] in ("agent", "system", "ap", "manager", "admin"):
        sql += " AND a.actor_type = ?"
        params.append(f["who"])
    if f["step"]:
        sql += " AND a.step LIKE ?"
        params.append(f"%{f['step']}%")
    if f["date"]:
        sql += " AND substr(a.ts,1,10) = ?"
        params.append(f["date"])
    return db.query(sql + " ORDER BY a.id DESC LIMIT ?", params + [int(limit)]), f


@app.route("/audit")
@security.login_required
def audit():
    rows, f = _audit_rows()
    ok, broken = db.verify_audit_chain()
    sel = None
    if request.args.get("entry", "").isdigit():
        sel = db.query("SELECT * FROM audit_log WHERE id=?", (int(request.args["entry"]),), one=True)
    steps = [r["step"] for r in db.query("SELECT DISTINCT step FROM audit_log ORDER BY step")]
    return render_template("audit.html", rows=rows, f=f, chain_ok=ok, broken=broken, sel=sel, steps=steps)


@app.route("/audit.csv")
@security.login_required
def audit_csv():
    rows, _ = _audit_rows(limit=5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time", "invoice_id", "invoice_number", "actor", "actor_type", "step", "result", "reason", "hash"])
    safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v   # CSV-injection guard
    for r in rows:
        w.writerow([safe(x) for x in (r["id"], r["ts"], r["invoice_id"], r["invoice_number"], r["actor"],
                                       r["actor_type"], r["step"], r["result"], r["reason"], r["hash"])])
    db.log_audit(None, g.user["username"], g.user["role"], "audit_export", "info", f"Exported {len(rows)} audit rows")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit_log.csv"})


# ==========================================================================
# Trust & testing
# ==========================================================================
_eval_state = {"running": False, "done": 0, "total": 0, "set": "", "error": None}


def _run_eval_bg(mode):
    try:
        def prog(s, i, n):
            _eval_state.update(set=s, done=i, total=n)
        evaluate.run_all(mode, progress=prog)
    except Exception as e:
        _eval_state["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        _eval_state["running"] = False


@app.route("/evaluation", methods=["GET", "POST"])
@security.login_required
def evaluation():
    if request.method == "POST":
        if not can("run_eval"):
            abort(403)
        if not _eval_state["running"]:
            mode = "offline" if request.form.get("mode") == "offline" else None
            _eval_state.update(running=True, done=0, total=0, error=None)
            db.log_audit(None, g.user["username"], g.user["role"], "run_evaluation", "info",
                         f"Started evaluation ({mode or 'auto'} mode)")
            threading.Thread(target=_run_eval_bg, args=(mode,), daemon=True).start()
        return redirect(url_for("evaluation"))
    return render_template("evaluation.html", r=evaluate.load("test"), state=_eval_state)


@app.route("/api/eval/status")
@security.login_required
def eval_status():
    return jsonify(_eval_state)


@app.route("/redteam")
@security.login_required
def redteam():
    r = evaluate.load("redteam")
    sel, trail, text = None, [], None
    if r:
        name = request.args.get("attack") or next((a["file"] for a in r["attacks"]), None)
        sel = next((a for a in r["attacks"] if a["file"] == name), None)
        if sel:
            path = os.path.join(db.REDTEAM_DIR, sel["file"])
            if os.path.exists(path):
                text = tools.read_document(path, sel["file"].rsplit(".", 1)[-1])["text"]
            dbfile = os.path.join(db.DATA_DIR, "eval_redteam.db")
            if db.database_exists(dbfile):
                with db.using(dbfile):
                    inv = db.query("SELECT id FROM invoices WHERE file_name=?", (sel["file"],), one=True)
                    if inv:
                        trail = [dict(x) for x in db.query("SELECT * FROM audit_log WHERE invoice_id=? ORDER BY id",
                                                           (inv["id"],))]
    return render_template("redteam.html", r=r, sel=sel, trail=trail, text=text, state=_eval_state)


def _replay(source, new_settings):
    """Re-run the decision rules on stored check results. No LLM calls, nothing is written."""
    cur_settings = db.get_settings()
    expected = {}
    if source == "test":
        path = os.path.join(db.DATA_DIR, "eval_test.db")
        if not db.database_exists(path):
            return None
        with open(os.path.join(db.SAMPLE_DIR, "expected_outcomes.json")) as f:
            expected = {c["file"]: c["expected_status"] for c in json.load(f)}
        with db.using(path):
            rows = [dict(r) for r in db.query("SELECT * FROM invoices WHERE checks_json IS NOT NULL ORDER BY id")]
    else:
        rows = [dict(r) for r in db.query("SELECT * FROM invoices WHERE checks_json IS NOT NULL ORDER BY id")]
    out = {"rows": [], "cur": {}, "new": {}, "changed": 0, "n": len(rows), "labelled": bool(expected)}
    for r in rows:
        checks = json.loads(r["checks_json"])
        ex = json.loads(r["extracted_json"] or "null")
        a, _, _ = tools.decide(checks, cur_settings, ex)
        b, reason_b, _ = tools.decide(checks, new_settings, ex)
        out["cur"][a] = out["cur"].get(a, 0) + 1
        out["new"][b] = out["new"].get(b, 0) + 1
        exp = expected.get(r["file_name"])
        row = {"id": r["id"], "label": r["invoice_number"] or r["file_name"], "total": r["total"], "cur": a, "new": b,
               "reason": reason_b, "expected": exp}
        if a != b:
            out["changed"] += 1
            out["rows"].append(row)
    if expected:
        tally = lambda key: sum(1 for r in rows if tools.decide(json.loads(r["checks_json"]),
                                                                  cur_settings if key == "cur" else new_settings,
                                                                  json.loads(r["extracted_json"] or "null"))[0]
                                == expected.get(r["file_name"]))
        out["acc_cur"], out["acc_new"] = tally("cur"), tally("new")
    approve_risky = lambda key: sum(1 for r in rows if tools.decide(
        json.loads(r["checks_json"]), cur_settings if key == "cur" else new_settings,
        json.loads(r["extracted_json"] or "null"))[0] == "Approved" and expected.get(r["file_name"], "Approved") != "Approved")
    out["risky_cur"], out["risky_new"] = approve_risky("cur"), approve_risky("new")
    return out


@app.route("/whatif", methods=["GET", "POST"])
@security.login_required
def whatif():
    cur = db.get_settings()
    form = {"auto_approve_limit": cur["auto_approve_limit"], "variance_tolerance_pct": cur["variance_tolerance_pct"],
            "min_confidence": cur["min_confidence"], "source": "live"}
    result = None
    if request.method == "POST":
        try:
            form = {"auto_approve_limit": max(0.0, float(request.form["auto_approve_limit"])),
                    "variance_tolerance_pct": min(100.0, max(0.0, float(request.form["variance_tolerance_pct"]))),
                    "min_confidence": min(1.0, max(0.0, float(request.form["min_confidence"]))),
                    "source": "test" if request.form.get("source") == "test" else "live"}
        except (KeyError, ValueError):
            abort(400)
        result = _replay(form["source"], {**cur, **{k: v for k, v in form.items() if k != "source"}})
        if result is None:
            flash("Run the evaluation once first, so there is a labelled test set to replay.", "error")
        if request.form.get("propose") and result is not None:
            to = cur["manager_email"]
            body = (f"Hi,\n\n{g.user['full_name']} proposes new invoice rules:\n\n"
                    f"  Auto-approve limit: {tools.fmt_inr(cur['auto_approve_limit'])} → {tools.fmt_inr(form['auto_approve_limit'])}\n"
                    f"  Variance tolerance: {cur['variance_tolerance_pct']:g}% → {form['variance_tolerance_pct']:g}%\n"
                    f"  Min. confidence: {cur['min_confidence']:g} → {form['min_confidence']:g}\n\n"
                    f"Replaying {result['n']} invoices, {result['changed']} decisions would change.\n"
                    f"Review: {tools.APP_URL}/whatif\n\nAn admin applies the change in Settings.\n\n— ClearPay")
            tools.save_email(None, "manager", "report", to, "Proposed rule change for invoice approvals", body,
                             g.user["username"])
            db.log_audit(None, g.user["username"], g.user["role"], "rule_change_proposed", "info",
                         f"Proposed limit {form['auto_approve_limit']}, tolerance {form['variance_tolerance_pct']}%, "
                         f"confidence {form['min_confidence']}")
            flash("Proposal drafted as an email to the manager.", "ok")
    return render_template("whatif.html", cur=cur, form=form, res=result)


_compare_state = {"running": False, "done": 0, "total": 0, "error": None}


def _run_compare_bg():
    import compare_ai
    try:
        compare_ai.run(progress=lambda i, n: _compare_state.update(done=i, total=n))
    except Exception as e:
        _compare_state["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        _compare_state["running"] = False


@app.route("/compare", methods=["GET", "POST"])
@security.login_required
def compare():
    """Rules alone vs rules + AI on tricky invoices. Running it uses Groq tokens, so only an admin starts it."""
    import compare_ai
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        if not tools.llm_enabled():
            flash("The comparison needs the AI: set GROQ_API_KEY in .env (and LLM_MODE isn't offline).", "error")
        elif not _compare_state["running"]:
            _compare_state.update(running=True, done=0, total=0, error=None)
            db.log_audit(None, g.user["username"], g.user["role"], "run_comparison", "info", "Started rules vs AI comparison")
            threading.Thread(target=_run_compare_bg, daemon=True).start()
        return redirect(url_for("compare"))
    return render_template("compare.html", r=compare_ai.load(), state=_compare_state, cases=compare_ai.CASES)


@app.route("/api/compare/status")
@security.login_required
def compare_status():
    return jsonify(_compare_state)


@app.route("/regression", methods=["GET", "POST"])
@security.login_required
def regression():
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        t = evaluate.load("test")
        if t:
            evaluate.save_baseline(t["rows"], t["meta"])
            with open(evaluate.LATEST_REGRESSION, "w") as f:
                json.dump(evaluate.regression_report(t["rows"], t["meta"]), f, default=str)
            db.log_audit(None, g.user["username"], g.user["role"], "baseline_saved", "info",
                         f"Saved evaluation from {t['meta']['run_at']} as regression baseline")
            flash("Current evaluation saved as the new baseline.", "ok")
        return redirect(url_for("regression"))
    return render_template("regression.html", reg=evaluate.load("regression"), t=evaluate.load("test"))


# ==========================================================================
# Settings (admin)
# ==========================================================================
@app.route("/settings", methods=["GET", "POST"])
@security.role_required("admin")
def settings():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "rules":
            changes = []
            try:
                for key, lo, hi in (("auto_approve_limit", 0, 1e9), ("variance_tolerance_pct", 0, 100),
                                    ("min_confidence", 0, 1), ("split_window_days", 1, 365),
                                    ("cost_saved_per_invoice_inr", 0, 100000), ("default_payment_terms_days", 0, 365),
                                    ("msme_max_days", 1, 365), ("smart_match_min_confidence", 0, 1)):
                    val = float(request.form[key])
                    if not lo <= val <= hi:
                        raise ValueError(key)
                    whole = key in ("split_window_days", "default_payment_terms_days", "msme_max_days")
                    db.set_setting(key, int(val) if whole else val)
                    changes.append(f"{key}={val:g}")
                auto = "1" if request.form.get("auto_investigate") == "1" else "0"
                db.set_setting("auto_investigate", auto)
                changes.append(f"auto_investigate={auto}")
                for key in ("ap_team_email", "manager_email"):
                    if not security.valid_email(request.form[key]):
                        raise ValueError(key)
                    db.set_setting(key, request.form[key].strip())
            except (KeyError, ValueError) as e:
                flash(f"Check the value for {e}.", "error")
                return redirect(url_for("settings"))
            db.log_audit(None, g.user["username"], "admin", "settings_changed", "info", ", ".join(changes))
            flash("Rules saved. New invoices use them right away.", "ok")
        elif act == "add_user":
            username = (request.form.get("username") or "").strip()[:40]
            role = request.form.get("role")
            pw = request.form.get("password") or ""
            problem = security.password_problem(pw)
            if not username.replace("_", "").isalnum() or role not in security.ROLE_LABELS or problem:
                flash(problem or "Use letters, numbers or _ for the username, and pick a role.", "error")
            elif db.query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
                flash("That username is taken.", "error")
            else:
                db.execute("INSERT INTO users(username, full_name, email, role, password_hash) VALUES (?,?,?,?,?)",
                           (username, (request.form.get("full_name") or username).strip()[:80],
                            (request.form.get("email") or "").strip()[:120], role, generate_password_hash(pw)))
                db.log_audit(None, g.user["username"], "admin", "user_added", "info", f"Added {username} as {role}")
                flash(f"Added {username}.", "ok")
        elif act == "vendor_email" and (request.form.get("vid") or "").isdigit():
            addr = (request.form.get("email") or "").strip()
            if not security.valid_email(addr):
                flash("Enter a valid vendor email address.", "error")
            else:
                v = db.query("SELECT * FROM vendors WHERE id=?", (int(request.form["vid"]),), one=True)
                db.execute("UPDATE vendors SET email=? WHERE id=?", (addr, v["id"]))
                db.log_audit(None, g.user["username"], "admin", "vendor_email_changed", "info",
                             f"{v['name']}: {v['email']} → {addr}")
                flash(f"Saved the email for {v['name']}. New vendor drafts will use it.", "ok")
        elif act == "vendor_msme" and (request.form.get("vid") or "").isdigit():
            v = db.query("SELECT * FROM vendors WHERE id=?", (int(request.form["vid"]),), one=True)
            if v:
                db.execute("UPDATE vendors SET msme=? WHERE id=?", (0 if v["msme"] else 1, v["id"]))
                db.log_audit(None, g.user["username"], "admin", "vendor_msme_changed", "info",
                             f"{v['name']}: MSME {'off' if v['msme'] else 'on'}")
                flash(f"{v['name']} is {'no longer' if v['msme'] else 'now'} marked as an MSME supplier. "
                      "New invoices use it for the due date.", "ok")
        elif act == "test_email":
            addr = (request.form.get("to") or "").strip()
            if not security.valid_email(addr):
                flash("Enter a valid address for the test email.", "error")
            elif not mailer.is_real():
                flash(mailer.status()["label"], "error")
            else:
                ok, delivered_to, err = mailer.send(addr, "ClearPay test email",
                                                    "If you can read this, real email sending works.\n\n— ClearPay")
                db.log_audit(None, g.user["username"], "admin", "email_test", "ok" if ok else "problem",
                             f"Test email to {delivered_to or addr}" + ("" if ok else f" failed: {err}"))
                flash(f"Test email sent to {delivered_to}. Check the inbox (and spam folder)." if ok else f"Not sent: {err}",
                      "ok" if ok else "error")
        elif act == "reset_password" and (request.form.get("uid") or "").isdigit():
            uid = int(request.form["uid"])
            pw = request.form.get("new_password") or ""
            problem = security.password_problem(pw)
            u = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
            if not u or problem:
                flash(problem or "User not found.", "error")
            else:
                db.execute("""UPDATE users SET password_hash=?, password_changed_at=?, failed_attempts=0, locked_until=NULL,
                              has_password=1 WHERE id=?""", (generate_password_hash(pw), db.now_iso(), uid))
                ended = security.end_other_sessions(uid, None, "password reset by admin")
                db.log_audit(None, g.user["username"], "admin", "password_reset", "info",
                             f"Reset the password for {u['username']} (signed out {ended} session(s))")
                notifications.security_event(uid, "Your password was reset",
                                             f"{g.user['full_name']} reset your ClearPay password at {ist_now():%H:%M IST}. "
                                             "Sign in with the new password they give you.")
                flash(f"Password reset for {u['username']}. Their open sessions were signed out.", "ok")
        elif act == "reset_mfa" and (request.form.get("uid") or "").isdigit():
            u = db.query("SELECT * FROM users WHERE id=?", (int(request.form["uid"]),), one=True)
            if u and u["mfa_enabled"]:
                db.execute("""UPDATE users SET mfa_enabled=0, mfa_secret=NULL, mfa_last_step=NULL, mfa_recovery=NULL,
                              mfa_enabled_at=NULL WHERE id=?""", (u["id"],))
                ended = security.end_other_sessions(u["id"], session.get("sid") if u["id"] == g.user["id"] else None,
                                                    "two-factor reset by admin")
                db.log_audit(None, u["username"], "admin", "mfa_reset", "info",
                             f"{g.user['username']} reset two-factor authentication (signed out {ended} session(s))")
                notifications.security_event(u["id"], "Two-factor authentication was reset",
                                             f"{g.user['full_name']} reset your two-factor authentication at {ist_now():%H:%M IST}. "
                                             "Set up your authenticator app again in Account → Security.")
                flash(f"Two-factor authentication reset for {u['username']}. They can set it up again after signing in.", "ok")
        elif act == "reject_request" and (request.form.get("uid") or "").isdigit():
            u = db.query("SELECT * FROM users WHERE id=?", (int(request.form["uid"]),), one=True)
            if u and not u["active"] and not u["has_password"] and not u["last_login_at"]:
                if not db.query("SELECT 1 FROM access_requests WHERE user_id=? AND decision IS NULL", (u["id"],), one=True):
                    access_requests.create(u["id"])                  # requests made before email approval existed
                access_requests.decide(u["id"], "rejected", g.user["full_name"])
                flash(f"Rejected the access request from {u['full_name']}.", "ok")
        elif act in ("unlock", "toggle") and (request.form.get("uid") or "").isdigit():
            uid = int(request.form["uid"])
            if uid == g.user["id"] and act == "toggle":
                flash("You can't deactivate your own account.", "error")
            else:
                u = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
                if act == "unlock":
                    db.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
                elif not u["active"] and access_requests.latest_decision(uid) is None and \
                        db.query("SELECT 1 FROM access_requests WHERE user_id=?", (uid,), one=True):
                    access_requests.decide(uid, "approved", g.user["full_name"])     # activating = approving the request
                else:
                    db.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, uid))
                    if u["active"]:
                        security.end_other_sessions(uid, None, "account deactivated")
                db.log_audit(None, g.user["username"], "admin", "user_" + act, "info", f"{act} {u['username']}")
                flash("User updated.", "ok")
        return redirect(url_for("settings"))
    decisions = {r["user_id"]: r["decision"] for r in db.query("SELECT user_id, decision FROM access_requests ORDER BY id")}
    return render_template("settings.html", s=db.get_settings(), users=db.query("SELECT * FROM users ORDER BY id"),
                           decisions=decisions,
                           vendors=db.query("SELECT * FROM vendors ORDER BY name"))


# ==========================================================================
# Account: profile, appearance, security, sign-in activity (every signed-in user)
# ==========================================================================
ACCOUNT_TABS = ("profile", "appearance", "security", "activity")


@app.route("/account", methods=["GET", "POST"])
@security.login_required
def account():
    tab = request.args.get("tab", "profile")
    tab = tab if tab in ACCOUNT_TABS else "profile"
    me = g.user
    if request.method == "POST":
        act = request.form.get("action")
        wants_json = request.headers.get("Accept", "").startswith("application/json")
        if act == "theme":
            theme = request.form.get("theme")
            if theme not in ("light", "dark", "system"):
                abort(400)
            db.execute("UPDATE users SET theme=? WHERE id=?", (theme, me["id"]))
            if wants_json:
                return jsonify({"ok": True, "theme": theme})
            flash("Appearance saved.", "ok")
        elif act == "profile":
            name = " ".join((request.form.get("full_name") or "").split())[:80]
            email = (request.form.get("email") or "").strip()[:254]
            dept = " ".join((request.form.get("department") or "").split())[:80]
            if not name:
                flash("Enter your name.", "error")
            elif not security.valid_email(email):
                flash("Enter a valid email address.", "error")
            else:
                db.execute("UPDATE users SET full_name=?, email=?, department=? WHERE id=?", (name, email, dept or None, me["id"]))
                db.log_audit(None, me["username"], me["role"], "profile_updated", "info", "Updated name, email or department")
                flash("Profile saved.", "ok")
        elif act == "password":
            cur, new, confirm = (request.form.get(k) or "" for k in ("current", "new", "confirm"))
            problem = security.password_problem(new)
            if not check_password_hash(me["password_hash"], cur):
                flash("Your current password is wrong.", "error")
            elif new != confirm:
                flash("The two new passwords don't match.", "error")
            elif problem:
                flash(problem, "error")
            elif check_password_hash(me["password_hash"], new):
                flash("Choose a password that is different from your current one.", "error")
            else:
                db.execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
                           (generate_password_hash(new), db.now_iso(), me["id"]))
                ended = security.end_other_sessions(me["id"], session.get("sid"), "password changed")
                db.log_audit(None, me["username"], me["role"], "password_changed", "info",
                             f"Changed their password (signed out {ended} other session(s))")
                notifications.security_event(me["id"], "Password changed",
                                             f"Your ClearPay password was changed at {ist_now():%H:%M IST}. "
                                             "If this wasn't you, contact your administrator now.")
                flash(f"Password changed. {ended} other session{'s were' if ended != 1 else ' was'} signed out.", "ok")
        elif act == "end_session":
            sid = request.form.get("sid") or ""
            row = db.query("SELECT * FROM user_sessions WHERE id=? AND user_id=?", (sid, me["id"]), one=True)
            if row and sid != session.get("sid"):
                security.end_session(sid, "signed out from another session")
                db.log_audit(None, me["username"], me["role"], "session_ended", "info", "Signed out one of their other sessions")
                flash("That session was signed out.", "ok")
        elif act == "end_others":
            ended = security.end_other_sessions(me["id"], session.get("sid"), "signed out from another session")
            db.log_audit(None, me["username"], me["role"], "session_ended", "info", f"Signed out {ended} other session(s)")
            flash(f"Signed out of {ended} other session{'s' if ended != 1 else ''}.", "ok")
        elif act == "mfa_start" and not me["mfa_enabled"]:
            # the secret is stored now but only switched on once a code from the app proves it was scanned
            db.execute("UPDATE users SET mfa_secret=?, mfa_last_step=NULL WHERE id=?", (mfa.seal(mfa.new_secret()), me["id"]))
            return redirect(url_for("account", tab="security", setup=1))
        elif act == "mfa_confirm" and not me["mfa_enabled"] and me["mfa_secret"]:
            step = mfa.verify(mfa.unseal(me["mfa_secret"]), request.form.get("code", ""))
            if step is None:
                flash("That code didn't match. Check the time on your phone is set automatically, then use the newest code.", "error")
                return redirect(url_for("account", tab="security", setup=1))
            codes, stored = mfa.new_recovery_codes()
            db.execute("""UPDATE users SET mfa_enabled=1, mfa_last_step=?, mfa_recovery=?, mfa_enabled_at=? WHERE id=?""",
                       (step, stored, db.now_iso(), me["id"]))
            ended = security.end_other_sessions(me["id"], session.get("sid"), "two-factor authentication turned on")
            db.log_audit(None, me["username"], me["role"], "mfa_enabled", "info",
                         f"Turned on two-factor authentication (signed out {ended} other session(s))")
            notifications.security_event(me["id"], "Two-factor authentication turned on",
                                         f"From now on, signing in needs a code from your authenticator app ({ist_now():%H:%M IST}).")
            flash("Two-factor authentication is on. Save your recovery codes now.", "ok")
            return account_page("security", recovery_codes=codes)
        elif act in ("mfa_disable", "mfa_new_codes") and me["mfa_enabled"]:
            if act == "mfa_disable" and mfa.required_for(me["role"]):
                flash("Your role must use two-factor authentication, so it can't be turned off.", "error")
            elif not check_second_factor(me, request.form.get("code", "")):
                db.log_audit(None, me["username"], me["role"], "mfa_failed", "blocked", "Wrong code while changing two-factor settings")
                flash("That code isn't right. Use the current code from your authenticator app or a recovery code.", "error")
            elif act == "mfa_disable":
                db.execute("""UPDATE users SET mfa_enabled=0, mfa_secret=NULL, mfa_last_step=NULL, mfa_recovery=NULL,
                              mfa_enabled_at=NULL WHERE id=?""", (me["id"],))
                db.log_audit(None, me["username"], me["role"], "mfa_disabled", "info", "Turned off two-factor authentication")
                notifications.security_event(me["id"], "Two-factor authentication turned off",
                                             f"Signing in no longer needs an authenticator code ({ist_now():%H:%M IST}). "
                                             "If this wasn't you, change your password and tell your administrator.")
                flash("Two-factor authentication is off.", "ok")
            else:
                codes, stored = mfa.new_recovery_codes()
                db.execute("UPDATE users SET mfa_recovery=? WHERE id=?", (stored, me["id"]))
                db.log_audit(None, me["username"], me["role"], "mfa_new_codes", "info", "Made new recovery codes (old ones stop working)")
                flash("New recovery codes made. The old ones no longer work.", "ok")
                return account_page("security", recovery_codes=codes)
        elif act == "google_unlink" and me["google_sub"]:
            if not me["has_password"]:
                flash("You sign in only with Google, so it can't be disconnected. Ask an admin to set a password first.", "error")
            else:
                db.execute("UPDATE users SET google_sub=NULL, google_email=NULL WHERE id=?", (me["id"],))
                db.log_audit(None, me["username"], me["role"], "google_unlinked", "info", f"Disconnected Google account {me['google_email']}")
                notifications.security_event(me["id"], "Google account disconnected",
                                             f"{me['google_email']} can no longer be used to sign in to ClearPay.")
                flash("Google account disconnected.", "ok")
        return redirect(url_for("account", tab=tab))
    return account_page(tab)


def account_page(tab, recovery_codes=None):
    me = db.query("SELECT * FROM users WHERE id=?", (g.user["id"],), one=True)
    setup = None
    if tab == "security" and request.args.get("setup") and not me["mfa_enabled"] and me["mfa_secret"]:
        secret = mfa.unseal(me["mfa_secret"])
        qr = mfa.qr_svg(mfa.provisioning_uri(secret, me["email"] or me["username"]))
        setup = {"qr": Markup(qr),  # nosec B704 - SVG made by the qrcode library from our own secret, no user input
                 "key": mfa.pretty(secret)}

    sessions = [dict(r) for r in db.query("SELECT * FROM user_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                                          (me["id"],))]
    for r in sessions:
        r["live"] = security.session_is_live(r)
        r["current"] = r["id"] == session.get("sid")
    previous = next((r for r in sessions if not r["current"]), None)
    activity = []
    for r in sessions:
        activity.append({"ts": r["created_at"], "event": "Signed in", "detail": agent_str(r["user_agent"]), "ip": r["ip"], "ok": True})
        if r["ended_at"]:
            activity.append({"ts": r["ended_at"], "event": "Signed out" if r["end_reason"] == "signed out" else
                             "Session ended", "detail": r["end_reason"], "ip": "", "ok": True})
    labels = {"login_failed": "Failed sign-in", "account_locked": "Account locked", "password_changed": "Password changed",
              "password_reset": "Password reset by admin", "mfa_failed": "Wrong authenticator code",
              "mfa_enabled": "Two-factor turned on", "mfa_disabled": "Two-factor turned off",
              "mfa_reset": "Two-factor reset by admin", "mfa_new_codes": "New recovery codes",
              "recovery_code_used": "Recovery code used", "google_linked": "Google connected",
              "google_unlinked": "Google disconnected"}
    marks = ",".join("?" * len(labels))
    for r in db.query(f"SELECT * FROM audit_log WHERE actor = ? AND step IN ({marks}) ORDER BY id DESC LIMIT 50",  # nosec B608 - placeholders only
                      (me["username"], *labels)):
        activity.append({"ts": r["ts"], "event": labels[r["step"]], "detail": r["reason"], "ip": "",
                         "ok": r["step"] not in ("login_failed", "account_locked", "mfa_failed")})
    activity.sort(key=lambda a: a["ts"] or "", reverse=True)
    alerts = [n for n in notifications.latest(me["id"], 50) if n["kind"] == "security"][:8]
    return render_template("account.html", tab=tab, me=me, sessions=sessions, previous=previous,
                           activity=activity[:60], alerts=alerts, current_sid=session.get("sid"), setup=setup,
                           recovery_codes=recovery_codes, codes_left=mfa.recovery_left(me["mfa_recovery"]),
                           mfa_required=mfa.required_for(me["role"]))


# ==========================================================================
# Notifications
# ==========================================================================
def _note_json(n):
    return {"id": n["id"], "kind": n["kind"], "title": n["title"], "body": n["body"], "invoice_id": n["invoice_id"],
            "icon": notifications.ICONS.get(n["kind"], "bell"), "read": bool(n["read_at"]),
            "time": dt(n["created_at"]), "hm": ist_time(n["created_at"]),
            "url": url_for("open_notification", nid=n["id"])}


@app.route("/notifications")
@security.login_required
def notifications_page():
    show = "unread" if request.args.get("show") == "unread" else "all"
    items = notifications.latest(g.user["id"], 200, only_unread=show == "unread")
    return render_template("notifications.html", items=items, show=show)


@app.route("/notifications/<int:nid>/open")
@security.login_required
def open_notification(nid):
    row = notifications.mark_read(g.user["id"], nid)
    if row and row["invoice_id"]:
        return redirect(url_for("invoice", iid=row["invoice_id"]))
    return redirect(url_for("account", tab="security") if row and row["kind"] == "security" else url_for("notifications_page"))


@app.route("/api/notifications")
@security.login_required
def api_notifications():
    after = request.args.get("after", "0")
    after = int(after) if after.isdigit() else 0
    uid = g.user["id"]
    return jsonify({"unread": notifications.unread_count(uid), "last_id": notifications.last_id(uid),
                    "new": [_note_json(n) for n in reversed(notifications.latest(uid, 10, after_id=after))] if after else [],
                    "recent": [_note_json(n) for n in notifications.latest(uid, 8)]})


@app.route("/api/notifications/read", methods=["POST"])
@security.login_required
def api_notifications_read():
    nid = request.form.get("id", "")
    if nid == "all":
        notifications.mark_all_read(g.user["id"])
    elif nid.isdigit():
        notifications.mark_read(g.user["id"], int(nid))
    else:
        abort(400)
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"unread": notifications.unread_count(g.user["id"])})
    return redirect(request.referrer or url_for("notifications_page"))


# ==========================================================================
# Errors
# ==========================================================================
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(500)
def error(e):
    code = getattr(e, "code", 500)
    msg = {400: getattr(e, "description", "The request wasn't valid."),
           403: "Your role doesn't allow this action.", 404: "That page or invoice doesn't exist.",
           413: "The upload is too large. Each file must be 10 MB or smaller.",
           500: "Something went wrong on our side. The error was logged."}.get(code, "Error")
    if code == 400 and "CSRF" in str(msg):
        msg = "Your form expired. Reload the page and try again."
    return render_template("error.html", code=code, msg=msg), code


# ==========================================================================
# Startup
# ==========================================================================
def startup():
    db.init_db()
    if not db.query("SELECT 1 FROM users LIMIT 1", one=True):
        if BEHIND_PROXY and os.getenv("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD) == DEFAULT_DEMO_PASSWORD:
            raise RuntimeError("Set DEMO_PASSWORD in .env to your own strong password before the first start: "
                               "BEHIND_PROXY=1 refuses to create accounts with the built-in demo password.")
        db.seed()
    if BEHIND_PROXY:
        weak = [u["username"] for u in db.query("SELECT username, password_hash FROM users WHERE active = 1")
                if check_password_hash(u["password_hash"], DEFAULT_DEMO_PASSWORD)]
        if weak:
            print(f"WARNING: these accounts still use the built-in demo password: {', '.join(weak)}. "
                  "Change them now (Settings & users > Reset a password).", flush=True)
    # Anything left half-processed by a previous run goes back in the queue.
    for r in db.query("SELECT id FROM invoices WHERE status='Processing'"):
        agent.submit(r["id"])
    if os.getenv("DISABLE_WATCHER") != "1":
        watcher.start()
        email_inbox.start()                  # only if INBOX_IMAP_* is set in .env


if __name__ == "__main__":
    startup()
    print(f"ClearPay on http://127.0.0.1:5000  "
          f"({'Groq ' + tools.MODEL if tools.llm_enabled() else 'offline rule-based mode — add GROQ_API_KEY to .env'})")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 5000)), debug=False, use_reloader=False)

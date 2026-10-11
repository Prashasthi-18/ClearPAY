"""
security.py - every security control in one place so it is easy to review.

  * password check with account lockout
  * login_required / role_required decorators (server-side, every request)
  * CSRF tokens for every POST form
  * simple in-memory rate limiting
  * security headers (CSP, no framing, no sniffing)
  * upload validation (extension, real file signature, size) + random file names
  * prompt-injection scanner for invoice text
  * helpers: email validation, bank-account masking
"""
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf", "txt", "png", "jpg", "jpeg"}

_DUMMY_HASH = generate_password_hash("timing-equaliser")

ROLE_LABELS = {"ap": "AP team", "manager": "Manager", "admin": "Admin"}


# --------------------------------------------------------------------------
# Login with lockout
# --------------------------------------------------------------------------
def authenticate(username, password):
    """Return (user_row, error_message). `username` may also be the account's email address.
    Locks the account after repeated failures."""
    username = (username or "").strip()
    user = db.query("SELECT * FROM users WHERE username = ? AND active = 1", (username,), one=True)
    if user is None and "@" in username:            # an email only counts when exactly one active account has it
        same = db.query("SELECT * FROM users WHERE LOWER(email) = ? AND active = 1", (username.lower(),))
        user = same[0] if len(same) == 1 else None
    # Same message for unknown user and wrong password, so usernames can't be probed.
    generic = "Wrong username or password."
    if not user:
        check_password_hash(_DUMMY_HASH, password or "")  # equalise timing
        return None, generic
    locked = locked_message(user)
    if locked:
        return None, locked
    if check_password_hash(user["password_hash"], password or ""):
        db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user["id"],))
        return user, None
    return None, record_failure(user, "wrong passwords") or generic


def locked_message(user):
    """The lockout message if the account is locked right now, else None."""
    if user["locked_until"]:
        until = datetime.fromisoformat(user["locked_until"])
        if until > datetime.now(timezone.utc):
            mins = int((until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            return f"Account locked after too many attempts. Try again in {mins} min."
    return None


def record_failure(user, what):
    """Count a failed sign-in step (password or authenticator code). Returns the lockout message once it locks."""
    attempts = user["failed_attempts"] + 1
    locked = None
    if attempts >= MAX_FAILED_LOGINS:
        locked = (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        attempts = 0
    db.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
               (attempts, locked, user["id"]))
    if locked:
        db.log_audit(None, user["username"], "system", "account_locked", "problem",
                     f"Locked for {LOCKOUT_MINUTES} min after {MAX_FAILED_LOGINS} failed logins")
        try:
            import notifications
            notifications.security_event(user["id"], "Account locked after failed sign-ins",
                                         f"{user['full_name']}'s account was locked for {LOCKOUT_MINUTES} minutes after "
                                         f"{MAX_FAILED_LOGINS} {what} from {request.remote_addr}.", also_admins=True)
        except Exception:  # a notification must never block the lockout itself
            pass
        return f"Account locked after too many attempts. Try again in {LOCKOUT_MINUTES} min."
    return None


SESSION_IDLE_MINUTES = 30


def load_current_user():
    """Called before every request: puts the logged-in user on flask.g.
    The signed cookie must point at a sign-in session that is still open on the server, so
    'sign out of other sessions' and the 30-minute idle timeout are enforced server-side too."""
    g.user = None
    g.session_row = None
    uid, sid = session.get("uid"), session.get("sid")
    if not uid:
        return
    row = db.query("SELECT * FROM user_sessions WHERE id = ? AND user_id = ? AND ended_at IS NULL", (sid or "", uid), one=True)
    if row is None:
        session.clear()
        return
    last = datetime.fromisoformat(row["last_seen"])
    now = datetime.now(timezone.utc)
    if now - last > timedelta(minutes=SESSION_IDLE_MINUTES):
        end_session(sid, "timed out")
        session.clear()
        return
    g.user = db.query("SELECT * FROM users WHERE id = ? AND active = 1", (uid,), one=True)
    if g.user is None:
        end_session(sid, "account deactivated")
        session.clear()
        return
    g.session_row = row
    if now - last > timedelta(seconds=60):          # keep "last active" fresh without a write on every request
        db.execute("UPDATE user_sessions SET last_seen = ? WHERE id = ?", (db.now_iso(), sid))


def start_session(user_id):
    """Record a new sign-in. Returns its id, which goes into the signed session cookie."""
    sid = secrets.token_urlsafe(24)
    now = db.now_iso()
    db.execute("""INSERT INTO user_sessions(id, user_id, user_agent, ip, created_at, last_seen) VALUES (?,?,?,?,?,?)""",
               (sid, user_id, (request.headers.get("User-Agent") or "")[:300], request.remote_addr, now, now))
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
    return sid


def end_session(sid, reason):
    db.execute("UPDATE user_sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
               (db.now_iso(), reason, sid))


def end_other_sessions(user_id, keep_sid, reason):
    rows = db.query("SELECT id FROM user_sessions WHERE user_id = ? AND ended_at IS NULL AND id != ?", (user_id, keep_sid or ""))
    for r in rows:
        end_session(r["id"], reason)
    return len(rows)


def session_is_live(row):
    if row["ended_at"]:
        return False
    return datetime.now(timezone.utc) - datetime.fromisoformat(row["last_seen"]) <= timedelta(minutes=SESSION_IDLE_MINUTES)


def describe_agent(ua):
    """'Chrome', 'Windows', 'Desktop' from a User-Agent string (good enough for a sign-in list)."""
    ua = ua or ""
    browser = ("Edge" if "Edg/" in ua else "Opera" if "OPR/" in ua else "Chrome" if "Chrome/" in ua
               else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua else "Unknown browser")
    os_name = ("Windows" if "Windows" in ua else "Android" if "Android" in ua else "iOS" if ("iPhone" in ua or "iPad" in ua)
               else "macOS" if "Mac OS X" in ua else "Linux" if "Linux" in ua else "Unknown system")
    device = "Phone" if ("Mobile" in ua or "iPhone" in ua) else "Tablet" if "iPad" in ua else "Computer"
    return browser, os_name, device


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Server-side role check. Hiding a button is not security; this is."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.get("user") is None:
                return redirect(url_for("login", next=request.path))
            if g.user["role"] not in roles:
                db.log_audit(None, g.user["username"], g.user["role"], "access_denied", "blocked",
                             f"Tried to open {request.path} without permission")
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    """Reject any state-changing request without the matching token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, description="Your form expired. Reload the page and try again.")


# --------------------------------------------------------------------------
# Rate limiting (in memory, per IP + bucket). Fine for a single-process demo.
# --------------------------------------------------------------------------
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(bucket, limit, per_seconds):
    key = (bucket, request.remote_addr)
    now = time.time()
    with _hits_lock:
        q = _hits[key]
        while q and q[0] < now - per_seconds:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


def reset_rate_limits():
    with _hits_lock:
        _hits.clear()


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------
CSP = ("default-src 'self'; "
       "script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
       "img-src 'self' data:; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def add_security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:                       # tell browsers to use https only (180 days)
        resp.headers["Strict-Transport-Security"] = "max-age=15552000"
    if request.path.startswith(("/invoice", "/api")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------
def file_extension(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def signature_matches(ext, head):
    """Check the file's real content, not just its name."""
    if ext == "pdf":
        return head.startswith(b"%PDF")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "txt":
        if b"\x00" in head:
            return False
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


def check_upload(filename, data):
    """Return (ok, problem). 'quarantine' means: right type, unreadable content."""
    ext = file_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Only PDF, TXT, PNG and JPG files are accepted."
    if len(data) == 0:
        return False, "The file is empty."
    if len(data) > MAX_UPLOAD_BYTES:
        return False, "Files must be 10 MB or smaller."
    if not signature_matches(ext, data[:2048]):
        return True, "quarantine"
    return True, None


def store_file(original_name, data):
    """Save under a random name in the private upload folder (blocks ../ path tricks)."""
    ext = file_extension(original_name)
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(db.UPLOAD_DIR, stored)
    with open(path, "wb") as f:
        f.write(data)
    safe_display = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(original_name))[:120]
    return path, safe_display, ext


# --------------------------------------------------------------------------
# Prompt-injection scanner (runs in code on the raw invoice text)
# --------------------------------------------------------------------------
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompts?)",
    r"\b(system|assistant|developer)\s*(note|message|prompt|instruction)s?\s*:",
    r"\bpre-?approved\b",
    r"\b(skip|bypass|disable|ignore)\s+(the\s+)?(po|purchase\s+order|matching|checks?|validation|verification)",
    r"\b(write_ledger_entry|escalate_to_human|draft_email|lookup_po|check_duplicate|check_vendor|validate_math)\b",
    r"\b(call|invoke|run|use)\s+(the\s+)?(tool|function)\b",
    r"\b(mark|set|change|update)\s+(this\s+|the\s+)?(invoice|status)\s+(as|to)\s+approved",
    r"\byou\s+are\s+(an?\s+|the\s+)?(ai|assistant|model|agent|llm)\b",
    r"\b(ai|llm)\s+(reviewer|agent|assistant|model)s?\b",
    r"\bnew\s+instructions?\b",
]


def scan_for_injection(text):
    """Return the list of suspicious phrases found (empty list = clean)."""
    found = []
    for pat in INJECTION_PATTERNS:
        for m in re.finditer(pat, text or "", flags=re.IGNORECASE):
            found.append(m.group(0)[:80])
    return sorted(set(found))


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(addr):
    return bool(addr) and len(addr) <= 254 and bool(EMAIL_RE.match(addr))


def mask_account(acct):
    """Show only the last 4 digits of a bank account."""
    if not acct:
        return ""
    digits = re.sub(r"\D", "", str(acct))
    return "••••" + digits[-4:] if len(digits) >= 4 else "••••"


def password_problem(pw):
    if len(pw or "") < 10:
        return "Use at least 10 characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Use letters and numbers."
    return None

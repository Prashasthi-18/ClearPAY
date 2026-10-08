"""
email_inbox.py - read invoices straight from a real email inbox (IMAP), so nobody has to upload them.

  new email -> find the invoice (PDF / image / text attachment, or an invoice typed in the body)
            -> same file checks as an upload -> invoice record -> the normal agent checks and rules
            -> flagged / pending / approved like any other invoice -> people review it

Nothing is approved, rejected or paid because an email arrived: an emailed invoice goes through exactly the
same code checks as an uploaded one. The sender's address is only recorded; vendor emails ClearPay drafts
still go to the address on file.

Settings (.env):
  INBOX_IMAP_HOST=imap.gmail.com   INBOX_IMAP_PORT=993   INBOX_IMAP_USER=invoices@yourcompany.com
  INBOX_IMAP_PASSWORD=<app password>   INBOX_IMAP_FOLDER=INBOX   INBOX_POLL_SECONDS=60
Use a mailbox that only receives invoices: every unread email in it is read and then marked as read.
"""
import email
import email.policy
import hashlib
import imaplib
import os
import re
import threading
import time
from email.utils import parseaddr

import agent
import db
import security

INVOICE_WORDS = re.compile(r"\b(invoice|tax invoice|bill)\b", re.I)
MONEY_WORDS = re.compile(r"\b(total|amount due|grand total|subtotal)\b", re.I)
MIN_IMAGE_BYTES = 15 * 1024          # smaller images are logos and signatures, not scanned invoices
MAX_PER_POLL = 20
_state = {"running": False, "last_check": None, "last_error": None, "picked_up": 0, "emails_read": 0}


def configured():
    return all(os.getenv(k, "").strip() for k in ("INBOX_IMAP_HOST", "INBOX_IMAP_USER", "INBOX_IMAP_PASSWORD"))


def status():
    s = dict(_state)
    s["configured"] = configured()
    s["mailbox"] = os.getenv("INBOX_IMAP_USER", "") if configured() else ""
    return s


def _decode_text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def find_invoices(msg):
    """Return [(file name, bytes)] for the invoices in one email. Pure function (tested without a mailbox)."""
    found = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        ctype = part.get_content_type()
        if name:
            ext = security.file_extension(name)
            data = part.get_payload(decode=True) or b""
            if ext not in security.ALLOWED_EXTENSIONS or not data:
                continue
            if ext in ("png", "jpg", "jpeg") and len(data) < MIN_IMAGE_BYTES and part.get("Content-ID"):
                continue                                  # an inline logo
            if ext == "txt" and not INVOICE_WORDS.search(data[:4000].decode("utf-8", "replace")):
                continue
            found.append((name, data))
        elif ctype == "application/pdf":
            found.append(("invoice.pdf", part.get_payload(decode=True) or b""))
    if not found:                                         # no attachment: an invoice written in the email itself?
        body = msg.get_body(preferencelist=("plain",)) if hasattr(msg, "get_body") else None
        text = _decode_text(body) if body is not None else ""
        if INVOICE_WORDS.search(text) and MONEY_WORDS.search(text) and re.search(r"\d", text):
            subject = re.sub(r"[^A-Za-z0-9._ -]", "_", str(msg.get("Subject") or "email invoice"))[:60]
            found.append((f"{subject.strip() or 'email invoice'}.txt", text.encode("utf-8")))
    return found


def handle_message(raw_bytes):
    """Turn one raw email into invoices (queued for the agent). Returns (result text, invoice ids)."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    mid = str(msg.get("Message-ID") or "").strip()[:250] or "no-id-" + hashlib.sha256(raw_bytes).hexdigest()[:32]
    if db.query("SELECT 1 FROM inbox_messages WHERE message_id = ?", (mid,), one=True):
        return "already read", []
    sender = parseaddr(str(msg.get("From") or ""))[1][:200]
    subject = " ".join(str(msg.get("Subject") or "").split())[:200]
    ids, problems = [], []
    for name, data in find_invoices(msg)[:10]:
        ok, problem = security.check_upload(name, data)
        if not ok:
            problems.append(f"{name[:60]}: {problem}")
            db.log_audit(None, "mailbox", "system", "email_rejected", "blocked", f"'{name[:60]}' from {sender}: {problem}")
            continue
        stored, display, ext = security.store_file(name, data)
        iid = agent.create_invoice(stored, display, ext, "email")
        db.execute("UPDATE invoices SET email_from=?, email_subject=? WHERE id=?", (sender, subject, iid))
        db.log_audit(iid, "mailbox", "system", "email_pickup", "info",
                     f"Picked up '{display}' from an email by {sender} (“{subject[:80]}”)")
        agent.submit(iid)
        ids.append(iid)
    result = (f"{len(ids)} invoice(s) picked up" if ids else "no invoice found") + \
        (f"; skipped {len(problems)} file(s)" if problems else "")
    db.execute("""INSERT INTO inbox_messages(message_id, from_addr, subject, result, invoice_ids, received_at)
                  VALUES (?,?,?,?,?,?)""", (mid, sender, subject, result, ",".join(map(str, ids)), db.now_iso()))
    _state["emails_read"] += 1
    _state["picked_up"] += len(ids)
    return result, ids


def check_once():
    """Read every unread email once. Returns how many emails were handled."""
    host = os.getenv("INBOX_IMAP_HOST", "").strip()
    port = int(os.getenv("INBOX_IMAP_PORT", "993"))
    box = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        # Google shows App Passwords with spaces; they aren't part of the password
        box.login(os.getenv("INBOX_IMAP_USER", "").strip(), "".join(os.getenv("INBOX_IMAP_PASSWORD", "").split()))
        box.select(os.getenv("INBOX_IMAP_FOLDER", "INBOX"))
        typ, data = box.search(None, "UNSEEN")
        nums = (data[0] or b"").split()[:MAX_PER_POLL] if typ == "OK" else []
        for num in nums:
            typ, parts = box.fetch(num, "(BODY.PEEK[])")       # PEEK: don't mark read until it's handled
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if typ != "OK" or raw is None:
                continue
            handle_message(raw)
            box.store(num, "+FLAGS", "\\Seen")
        return len(nums)
    finally:
        try:
            box.logout()
        except Exception:
            pass


def _loop():
    every = max(15, int(os.getenv("INBOX_POLL_SECONDS", "60")))
    while True:
        try:
            check_once()
            _state["last_error"] = None
        except Exception as e:      # wrong password, network down... keep trying, show it on the dashboard
            msg = f"{type(e).__name__}: {str(e)[:160]}"
            if msg != _state["last_error"]:
                db.log_audit(None, "mailbox", "system", "mailbox_error", "problem", msg)
            _state["last_error"] = msg
        _state["last_check"] = db.now_iso()
        time.sleep(every)


def start():
    if _state["running"] or not configured():
        return False
    _state["running"] = True
    threading.Thread(target=_loop, name="mailbox", daemon=True).start()
    return True

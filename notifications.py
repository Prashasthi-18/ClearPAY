"""
notifications.py - in-app notifications, so the AP team never misses an invoice event.

Who is told what
  manager approves / rejects an invoice  -> every AP user + whoever uploaded it   (kind: approved / rejected)
  invoice needs a manager's approval     -> managers                              (kind: needs_approval)
  invoice needs AP review (flagged)      -> every AP user + whoever uploaded it   (kind: attention)
  invoice rejected automatically         -> whoever uploaded it                   (kind: rejected)
  processing failed                      -> every AP user                         (kind: failed)
  security events on an account          -> that user (+ admins for lockouts)     (kind: security)

One row per recipient, so read / unread is personal. Rows are only ever created by the
existing workflow (approve, reject, send to manager, agent finished); nothing here changes it.
"""
from datetime import datetime, timedelta, timezone

import db

ICONS = {"approved": "circle-check", "rejected": "circle-x", "needs_approval": "clock", "attention": "triangle-alert",
         "failed": "circle-alert", "security": "shield-alert"}
IST = timezone(timedelta(hours=5, minutes=30))


def _active_users():
    return [dict(r) for r in db.query("SELECT id, role, full_name, username FROM users WHERE active = 1")]


def ids_with_role(*roles):
    return [u["id"] for u in _active_users() if u["role"] in roles]


def notify(user_ids, kind, title, body, invoice_id=None, actor=None):
    """Create one notification per recipient (duplicates and empty ids dropped). Returns how many."""
    now = db.now_iso()
    n = 0
    for uid in dict.fromkeys(u for u in user_ids if u):
        db.execute("""INSERT INTO notifications(user_id, kind, title, body, invoice_id, actor, created_at)
                      VALUES (?,?,?,?,?,?,?)""", (uid, kind, title[:160], body[:600], invoice_id, actor, now))
        n += 1
    return n


def _label(inv):
    return inv["invoice_number"] or inv["file_name"]


def _amount(inv):
    from tools import fmt_inr                      # local import keeps module start-up light
    return fmt_inr(inv["total"]) if inv["total"] is not None else "amount unknown"


def _hhmm(iso):
    try:
        t = datetime.fromisoformat(iso)
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return t.astimezone(IST).strftime("%H:%M IST")
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------
# Workflow events
# --------------------------------------------------------------------------
def on_manager_decision(inv, decider, approved, comment, when_iso):
    """A manager approved or rejected an invoice: tell the AP team (and the uploader)."""
    recipients = [u for u in ids_with_role("ap") + [inv["uploaded_by"]] if u and u != decider["id"]]
    verb = "approved" if approved else "rejected"
    vendor = inv["vendor_name"] or "Unknown vendor"
    next_step = ("The ledger entry is now ready for payment." if approved
                 else "The ledger entry was cancelled; contact the vendor if needed.")
    body = f"{vendor}, {_amount(inv)}. {verb.capitalize()} by {decider['full_name']} at {_hhmm(when_iso)}. {next_step}"
    if comment:
        body += f' Comment: "{comment}"'
    n = notify(recipients, "approved" if approved else "rejected", f"Invoice {_label(inv)} {verb}", body,
               inv["id"], decider["username"])
    db.log_audit(inv["id"], "system", "system", "ap_notified", "info",
                 f"AP team notified ({n} {'person' if n == 1 else 'people'}) that {decider['full_name']} {verb} the invoice")
    return n


def on_sent_to_manager(inv, by_user, note):
    managers = ids_with_role("manager") or ids_with_role("admin")
    body = f"{inv['vendor_name'] or 'Unknown vendor'}, {_amount(inv)}. {by_user['full_name']} reviewed it and asked for approval."
    if note:
        body += f' Note: "{note}"'
    return notify(managers, "needs_approval", f"Approval needed: {_label(inv)}", body, inv["id"], by_user["username"])


def on_processed(invoice_id, status):
    """Called when the agent finishes an invoice. Auto-approved invoices need nobody, so they create nothing."""
    inv = db.query("SELECT * FROM invoices WHERE id = ?", (invoice_id,), one=True)
    if not inv or inv["source"] == "eval":
        return 0
    reason = inv["decision_reason"] or ""
    vendor = inv["vendor_name"] or "Unknown vendor"
    if status == "Pending":
        managers = ids_with_role("manager") or ids_with_role("admin")
        return notify(managers, "needs_approval", f"Approval needed: {_label(inv)}",
                      f"{vendor}, {_amount(inv)}. {reason}.", invoice_id, "agent")
    if status == "Flagged":
        failed = reason.startswith("Processing error")
        return notify(ids_with_role("ap") + [inv["uploaded_by"]], "failed" if failed else "attention",
                      f"Processing failed: {_label(inv)}" if failed else f"{_label(inv)} needs AP review",
                      f"{vendor}, {_amount(inv)}. {reason}.", invoice_id, "agent")
    if status == "Rejected" and inv["uploaded_by"]:
        return notify([inv["uploaded_by"]], "rejected", f"{_label(inv)} was rejected automatically",
                      f"{vendor}, {_amount(inv)}. {reason}.", invoice_id, "agent")
    return 0


def security_event(user_id, title, body, also_admins=False):
    recipients = [user_id] + (ids_with_role("admin") if also_admins else [])
    return notify(recipients, "security", title, body, None, "system")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def unread_count(user_id):
    return db.query("SELECT COUNT(*) n FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,), one=True)["n"]


def latest(user_id, limit=20, only_unread=False, after_id=0):
    sql = "SELECT * FROM notifications WHERE user_id = ? AND id > ?"
    if only_unread:
        sql += " AND read_at IS NULL"
    return [dict(r) for r in db.query(sql + " ORDER BY id DESC LIMIT ?", (user_id, after_id, int(limit)))]


def last_id(user_id):
    r = db.query("SELECT MAX(id) m FROM notifications WHERE user_id = ?", (user_id,), one=True)
    return r["m"] or 0


def mark_read(user_id, note_id):
    """Mark one as read. Returns the notification (or None if it isn't this user's)."""
    row = db.query("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (note_id, user_id), one=True)
    if row and not row["read_at"]:
        db.execute("UPDATE notifications SET read_at = ? WHERE id = ?", (db.now_iso(), note_id))
    return row


def mark_all_read(user_id):
    db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (db.now_iso(), user_id))


def mark_invoice_read(user_id, invoice_id):
    """Opening an invoice marks its notifications as read. Returns how many were unread."""
    n = db.query("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND invoice_id=? AND read_at IS NULL",
                 (user_id, invoice_id), one=True)["n"]
    if n:
        db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND invoice_id = ? AND read_at IS NULL",
                   (db.now_iso(), user_id, invoice_id))
    return n


def unread_invoice_ids(user_id):
    return {r["invoice_id"] for r in db.query(
        "SELECT DISTINCT invoice_id FROM notifications WHERE user_id = ? AND read_at IS NULL AND invoice_id IS NOT NULL",
        (user_id,))}

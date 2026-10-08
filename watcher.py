"""
watcher.py - background thread that watches invoices_inbox/.

Every few seconds it looks for new files. Each new file is validated, copied
into the private upload folder under a random name, recorded as an invoice
and queued for the agent. The original is moved to invoices_inbox/processed/
(or invoices_inbox/rejected/ if it isn't an allowed file type).
"""
import os
import shutil
import threading
import time

import agent
import db
import security

POLL_SECONDS = 2
_state = {"running": False, "last_pickup": None, "picked_up": 0}


def _settled(path):
    """Skip files that are still being copied in (size still changing)."""
    try:
        s1 = os.path.getsize(path)
        time.sleep(0.3)
        return s1 == os.path.getsize(path)
    except OSError:
        return False


def scan_once():
    processed = os.path.join(db.INBOX_DIR, "processed")
    rejected = os.path.join(db.INBOX_DIR, "rejected")
    os.makedirs(processed, exist_ok=True)
    os.makedirs(rejected, exist_ok=True)
    for name in sorted(os.listdir(db.INBOX_DIR)):
        path = os.path.join(db.INBOX_DIR, name)
        if not os.path.isfile(path) or name.startswith(".") or not _settled(path):
            continue
        with open(path, "rb") as f:
            data = f.read(security.MAX_UPLOAD_BYTES + 1)
        ok, problem = security.check_upload(name, data)
        if not ok:
            shutil.move(path, os.path.join(rejected, f"{int(time.time())}_{name}"))
            db.log_audit(None, "watcher", "system", "inbox_rejected", "blocked", f"'{name[:80]}': {problem}")
            continue
        stored, display, ext = security.store_file(name, data)
        iid = agent.create_invoice(stored, display, ext, "inbox")
        db.log_audit(iid, "watcher", "system", "inbox_pickup", "info", f"Picked up '{display}' from invoices_inbox")
        shutil.move(path, os.path.join(processed, f"{int(time.time())}_{name}"))
        agent.submit(iid)
        _state["last_pickup"] = db.now_iso()
        _state["picked_up"] += 1


def _loop():
    while True:
        try:
            scan_once()
        except Exception as e:     # keep watching even if one file causes trouble
            db.log_audit(None, "watcher", "system", "watcher_error", "problem", f"{type(e).__name__}: {e}"[:200])
        time.sleep(POLL_SECONDS)


def start():
    if _state["running"]:
        return
    _state["running"] = True
    threading.Thread(target=_loop, name="inbox-watcher", daemon=True).start()


def status():
    return dict(_state)

"""
mfa.py - two-factor authentication with an authenticator app (TOTP, RFC 6238) plus one-time recovery codes.

  * Works with Google Authenticator, Microsoft Authenticator, Authy, 1Password, ...: 6 digits, 30 seconds, SHA-1.
  * A code is accepted for the current 30-second step and one step either side (clock drift), and never twice
    (the last used step is stored, so a code seen over someone's shoulder can't be replayed).
  * Recovery codes are stored hashed and each works once.
  * If MFA_ENCRYPTION_KEY is set (a Fernet key), the secrets are encrypted in the database.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import struct
import time
from urllib.parse import quote, urlencode

ISSUER = "ClearPay"
DIGITS = 6
PERIOD = 30
DRIFT_STEPS = 1
RECOVERY_CODES = 10


# ---------------------------------------------------------------- TOTP
def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret, step, digits=DIGITS):
    mac = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(n).zfill(digits)


def current_step(now=None):
    return int((time.time() if now is None else now) // PERIOD)


def verify(secret, code, last_step=None, now=None):
    """Return the matching time step if `code` is valid and newer than `last_step`, else None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    now_step = current_step(now)
    for step in range(now_step - DRIFT_STEPS, now_step + DRIFT_STEPS + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret, account):
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": PERIOD})


def qr_svg(text):
    """The QR code as an inline SVG string (no image files, nothing sent anywhere)."""
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.index("<svg"):]                    # drop the XML declaration


def pretty(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ---------------------------------------------------------------- recovery codes
def _hash_code(code):
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes():
    """Returns (codes to show once, JSON of their hashes to store)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"          # no 0/o, 1/l/i
    codes = ["".join(secrets.choice(alphabet) for _ in range(5)) + "-" + "".join(secrets.choice(alphabet) for _ in range(5))
             for _ in range(RECOVERY_CODES)]
    return codes, json.dumps([_hash_code(c) for c in codes])


def use_recovery_code(stored_json, code):
    """Returns (ok, updated JSON). A used code is removed."""
    hashes = json.loads(stored_json or "[]")
    h = _hash_code(code or "")
    for i, stored in enumerate(hashes):
        if hmac.compare_digest(stored, h):
            del hashes[i]
            return True, json.dumps(hashes)
    return False, stored_json


def recovery_left(stored_json):
    return len(json.loads(stored_json or "[]"))


def looks_like_recovery_code(code):
    c = (code or "").replace("-", "").replace(" ", "")
    return len(c) == 10 and c.isalnum() and not c.isdigit()


# ---------------------------------------------------------------- secrets at rest
def _fernet():
    key = os.getenv("MFA_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def seal(secret):
    f = _fernet()
    return "enc:" + f.encrypt(secret.encode()).decode() if f else secret


def unseal(stored):
    if not stored:
        return None
    if stored.startswith("enc:"):
        f = _fernet()
        if f is None:
            raise RuntimeError("MFA secrets are encrypted but MFA_ENCRYPTION_KEY is not set in .env")
        return f.decrypt(stored[4:].encode()).decode()
    return stored


def required_for(role):
    """Roles that must use MFA (MFA_REQUIRED_ROLES=admin,manager). Empty = optional for everyone."""
    roles = {r.strip() for r in os.getenv("MFA_REQUIRED_ROLES", "").split(",") if r.strip()}
    return role in roles

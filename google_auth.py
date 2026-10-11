"""
google_auth.py - "Continue with Google" (OpenID Connect, authorization-code flow with PKCE).

  * state  : random, kept in the signed session, checked on return (stops login CSRF).
  * nonce  : random, must come back inside the ID token (stops token replay).
  * PKCE   : the code can only be exchanged by the browser session that started the sign-in.
  * The ID token comes straight from Google's token endpoint over HTTPS with our client secret, so it is
    trusted on TLS (OpenID Connect Core 3.1.3.7); its issuer, audience, expiry, nonce and email_verified are checked.

Settings (.env): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, optional GOOGLE_REDIRECT_URI,
GOOGLE_ALLOWED_DOMAINS (e.g. "yourcompany.com"), GOOGLE_SIGNUP (request | off).
"""
import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105 - a URL, not a password
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleError(Exception):
    """Shown to the person on the sign-in page, so keep messages plain."""


def client_id():
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def enabled():
    return bool(client_id() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip())


def signup_mode():
    return "off" if os.getenv("GOOGLE_SIGNUP", "request").strip().lower() == "off" else "request"


def allowed_domains():
    return {d.strip().lower().lstrip("@") for d in os.getenv("GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()}


def domain_allowed(email):
    domains = allowed_domains()
    return not domains or email.rsplit("@", 1)[-1].lower() in domains


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def start(redirect_uri):
    """Returns (url to send the browser to, values to keep in the session until Google sends them back)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {"client_id": client_id(), "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
              "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
    domains = allowed_domains()
    if len(domains) == 1:
        params["hd"] = next(iter(domains))       # a hint for Google's account picker; still checked on return
    return AUTH_URL + "?" + urlencode(params), {"state": state, "nonce": nonce, "verifier": verifier,
                                                "redirect_uri": redirect_uri, "at": int(time.time())}


def exchange_code(code, verifier, redirect_uri):
    """Swap the one-time code for tokens at Google. Separate so tests can replace it."""
    import httpx
    r = httpx.post(TOKEN_URL, data={"code": code, "client_id": client_id(),
                                    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
                                    "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                                    "code_verifier": verifier}, timeout=15)
    if r.status_code != 200:
        raise GoogleError("Google didn't accept the sign-in. Try again.")
    return r.json()


def _claims(id_token):
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise GoogleError("Google sent an unreadable sign-in token. Try again.")


def finish(args, pending):
    """Check what Google sent back. Returns {'sub', 'email', 'name'} or raises GoogleError."""
    if args.get("error"):
        raise GoogleError("Google sign-in was cancelled." if args["error"] == "access_denied" else
                          "Google sign-in didn't complete. Try again.")
    if not pending or not args.get("state") or not secrets.compare_digest(args["state"], pending.get("state", "")):
        raise GoogleError("That Google sign-in link expired. Start again.")
    if int(time.time()) - pending.get("at", 0) > 600:
        raise GoogleError("The Google sign-in took too long. Start again.")
    if not args.get("code"):
        raise GoogleError("Google sign-in didn't complete. Try again.")
    tokens = exchange_code(args["code"], pending["verifier"], pending["redirect_uri"])
    c = _claims(tokens.get("id_token", ""))
    if c.get("iss") not in ISSUERS or c.get("aud") != client_id():
        raise GoogleError("That sign-in token wasn't meant for ClearPay.")
    if int(c.get("exp", 0)) < time.time() or not secrets.compare_digest(str(c.get("nonce", "")), pending["nonce"]):
        raise GoogleError("That Google sign-in expired. Start again.")
    email = (c.get("email") or "").strip().lower()
    if not email or c.get("email_verified") not in (True, "true"):
        raise GoogleError("Your Google account's email address isn't verified.")
    return {"sub": str(c["sub"]), "email": email, "name": (c.get("name") or email.split("@")[0]).strip()[:80]}

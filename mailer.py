"""
mailer.py - sends emails for real. Two ways, chosen by EMAIL_MODE in .env:

  EMAIL_MODE=mock     nothing leaves the app (default)
  EMAIL_MODE=smtp     any SMTP server (Gmail, Outlook, a company mail server...):
                        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
                        SMTP_SECURITY=starttls | ssl | none     (none only for a local test server)
  EMAIL_MODE=brevo    Brevo's email API over HTTPS (port 443). Use this where outgoing SMTP ports are
                      blocked, for example Render's free plan:
                        BREVO_API_KEY, and MAIL_FROM = a sender address you verified in Brevo
  Both real modes also use:
  MAIL_FROM, MAIL_FROM_NAME
  EMAIL_REDIRECT_TO            test mode: every email goes to this address instead,
                               with a note saying who it was meant for

Emails are never sent automatically: a person presses "Send" on each draft.
"""
import http.client
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import security


def mode():
    return os.getenv("EMAIL_MODE", "mock").strip().lower()


def missing_settings():
    if mode() == "brevo":
        return [k for k in ("BREVO_API_KEY", "MAIL_FROM") if not os.getenv(k, "").strip()]
    return [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not os.getenv(k)]


def is_real():
    return mode() in ("smtp", "brevo") and not missing_settings()


def _via():
    return "Brevo (HTTPS)" if mode() == "brevo" else os.getenv("SMTP_HOST")


def redirect_to():
    return os.getenv("EMAIL_REDIRECT_TO", "").strip()


def status():
    """Shown on the Settings and Email drafts pages."""
    if mode() not in ("smtp", "brevo"):
        return {"real": False, "label": "Demo mode: emails are only marked as sent (EMAIL_MODE=mock)."}
    if missing_settings():
        return {"real": False, "label": f"EMAIL_MODE={mode()}, but these are missing in the settings: " + ", ".join(missing_settings())}
    r = redirect_to()
    return {"real": True, "redirect": r,
            "label": (f"Real sending ON via {_via()}. TEST MODE: every email goes to {r}."
                      if r else f"Real sending ON via {_via()}. Emails go to the real recipients.")}


def _one_line(text, limit=250):
    """Header values must be one line (blocks email header injection)."""
    return " ".join(str(text or "").split())[:limit]


def send(to, subject, body, html=None):
    """Send one email (plain text, plus an optional HTML version). Returns (ok, delivered_to, error_message)."""
    if not is_real():
        return True, None, None                       # mock mode: caller just marks it sent
    target = redirect_to() or to
    if not security.valid_email(target):
        return False, None, f"'{target}' isn't a valid email address."
    if target.lower().endswith((".example", ".test", ".invalid", ".localhost")):
        return False, None, (f"'{target}' is a placeholder demo address and can't receive mail. Put a real address "
                             f"in Settings (manager, AP team or vendor), or set EMAIL_REDIRECT_TO in .env for testing.")

    if redirect_to():
        body = (f"[TEST MODE] This email was meant for: {to}\n"
                f"It was redirected to you because EMAIL_REDIRECT_TO is set in the settings.\n"
                f"{'-' * 60}\n\n{body}")
        if html:
            from html import escape
            html = (f'<p style="font:13px sans-serif;color:#8C1E15">[TEST MODE] This email was meant for: {escape(to)}</p>' + html)
    subject = ("[TEST] " if redirect_to() else "") + _one_line(subject)
    if mode() == "brevo":
        return _send_brevo(target, subject, body, html)
    return _send_smtp(target, subject, body, html)


def _send_brevo(target, subject, body, html=None):
    """Brevo transactional email API: one HTTPS POST. The sender must be verified in the Brevo account."""
    payload = {"sender": {"name": _one_line(os.getenv("MAIL_FROM_NAME", "ClearPay"), 80), "email": os.getenv("MAIL_FROM", "").strip()},
               "to": [{"email": target}], "subject": subject, "textContent": body}
    if html:
        payload["htmlContent"] = html
    headers = {"accept": "application/json", "content-type": "application/json", "api-key": os.getenv("BREVO_API_KEY", "").strip()}
    try:
        conn = http.client.HTTPSConnection("api.brevo.com", timeout=20, context=ssl.create_default_context())
        try:
            conn.request("POST", "/v3/smtp/email", body=json.dumps(payload), headers=headers)
            resp = conn.getresponse()
            raw = resp.read(4000).decode("utf-8", "replace")
        finally:
            conn.close()
    except (TimeoutError, ConnectionError, OSError) as e:
        return False, None, (f"Couldn't reach api.brevo.com ({type(e).__name__}). Check the server's internet connection.")
    if 200 <= resp.status < 300:
        return True, target, None
    try:
        detail = json.loads(raw).get("message", "")
    except ValueError:
        detail = raw[:150]
    if resp.status in (401, 403):
        return False, None, "Brevo refused the API key. Create a new key in Brevo (Settings, SMTP & API, API keys) and put it in BREVO_API_KEY."
    if resp.status == 400:
        return False, None, (f"Brevo rejected the email: {detail or 'bad request'}. If it mentions the sender, verify "
                             f"{os.getenv('MAIL_FROM')} as a sender in Brevo (Senders & IP, Senders).")
    if resp.status == 429:
        return False, None, "Brevo says too many emails were sent just now (or the daily limit is used up). Try again later."
    return False, None, f"Brevo error {resp.status}: {detail or 'no details'}"


def _send_smtp(target, subject, body, html=None):
    from_addr = os.getenv("MAIL_FROM") or os.getenv("SMTP_USER")
    msg = EmailMessage()
    msg["From"] = f"{_one_line(os.getenv('MAIL_FROM_NAME', 'ClearPay'), 80)} <{from_addr}>"
    msg["To"] = target
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1])
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    security_mode = os.getenv("SMTP_SECURITY", "ssl" if port == 465 else "starttls").lower()
    tls = ssl.create_default_context()
    try:
        if security_mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, context=tls, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if security_mode == "starttls":
                server.starttls(context=tls)
            if security_mode != "none":            # "none" is only for a local test server without login
                # Google shows App Passwords as "abcd efgh ijkl mnop": spaces are not part of the password
                server.login(os.getenv("SMTP_USER", "").strip(), "".join(os.getenv("SMTP_PASSWORD", "").split()))
            server.send_message(msg)
        return True, target, None
    except smtplib.SMTPAuthenticationError:
        return False, None, ("The mail server refused the login. Check SMTP_USER, and for Gmail use a 16-character "
                             "App Password, not your normal password.")
    except smtplib.SMTPServerDisconnected:
        # Must come before OSError: smtplib's errors are a kind of OSError in Python.
        return False, None, ("The mail server hung up during login. For Gmail this almost always means the App Password "
                             "isn't accepted: create a new one at myaccount.google.com/apppasswords while signed in to "
                             f"{os.getenv('SMTP_USER')}, put the 16 letters (no spaces) in SMTP_PASSWORD, and restart. "
                             "After several failed tries Google may also block logins for 15-30 minutes.")
    except smtplib.SMTPRecipientsRefused:
        return False, None, f"The mail server refused the recipient '{target}'."
    except smtplib.SMTPException as e:
        return False, None, f"Mail server error: {str(e)[:200]}"
    except (TimeoutError, ConnectionError, OSError) as e:
        return False, None, (f"Couldn't reach {host}:{port} ({type(e).__name__}). Check SMTP_HOST/SMTP_PORT, your "
                             f"internet, or whether this network blocks outgoing mail ports (common on college/office Wi-Fi).")

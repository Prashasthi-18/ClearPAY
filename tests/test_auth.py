"""Two-factor authentication (TOTP + recovery codes) and Google sign-in / sign-up / linking."""
import base64
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest

import db
import google_auth
import mfa
import security
from conftest import PASSWORD


@pytest.fixture()
def app(fresh_db, monkeypatch):
    for k in ("MFA_REQUIRED_ROLES", "MFA_ENCRYPTION_KEY", "GOOGLE_ALLOWED_DOMAINS", "GOOGLE_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_SIGNUP", "request")
    import app as appmod
    appmod.app.config["TESTING"] = True
    security.reset_rate_limits()
    return appmod.app


def token(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def password_login(c, username="prashasthi", password=PASSWORD):
    return c.post("/login", data={"username": username, "password": password, "csrf_token": token(c.get("/login").text)})


def user(username):
    return db.query("SELECT * FROM users WHERE username=?", (username,), one=True)


def turn_on_mfa(username="prashasthi"):
    secret = mfa.new_secret()
    codes, stored = mfa.new_recovery_codes()
    db.execute("UPDATE users SET mfa_enabled=1, mfa_secret=?, mfa_recovery=? WHERE username=?", (mfa.seal(secret), stored, username))
    return secret, codes


# ---------------------------------------------------------------- TOTP maths
def test_totp_matches_the_rfc_6238_test_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert mfa.code_at(secret, 59 // 30, digits=8) == "94287082"
    assert mfa.code_at(secret, 1111111109 // 30, digits=8) == "07081804"


def test_codes_allow_one_step_of_drift_and_never_replay():
    s, now = mfa.new_secret(), 1_700_000_000
    step = mfa.current_step(now)
    assert mfa.verify(s, mfa.code_at(s, step), now=now) == step
    assert mfa.verify(s, mfa.code_at(s, step - 1), now=now) == step - 1
    assert mfa.verify(s, mfa.code_at(s, step - 3), now=now) is None
    assert mfa.verify(s, mfa.code_at(s, step), last_step=step, now=now) is None       # already used
    assert mfa.verify(s, "12345", now=now) is None


def test_recovery_codes_work_once_and_are_stored_hashed():
    codes, stored = mfa.new_recovery_codes()
    assert len(codes) == 10 and codes[0] not in stored
    ok, rest = mfa.use_recovery_code(stored, codes[3].upper())
    assert ok and mfa.recovery_left(rest) == 9
    assert mfa.use_recovery_code(rest, codes[3])[0] is False


def test_secrets_are_encrypted_when_a_key_is_set(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    sealed = mfa.seal("JBSWY3DPEHPK3PXP")
    assert sealed.startswith("enc:") and "JBSWY3DP" not in sealed and mfa.unseal(sealed) == "JBSWY3DPEHPK3PXP"


# ---------------------------------------------------------------- sign-in with MFA
def test_password_alone_is_not_enough_when_mfa_is_on(app):
    secret, _ = turn_on_mfa()
    c = app.test_client()
    r = password_login(c)
    assert r.status_code == 302 and r.headers["Location"].endswith("/login/mfa")
    assert c.get("/").status_code == 302                                  # not signed in yet
    page = c.get("/login/mfa").text
    assert "Two-step verification" in page
    r = c.post("/login/mfa", data={"code": mfa.code_at(secret, mfa.current_step()), "csrf_token": token(page)})
    assert r.status_code == 302 and c.get("/").status_code == 200
    assert "authenticator code" in db.query("SELECT reason FROM audit_log WHERE step='user_login' ORDER BY id DESC", one=True)["reason"]


def test_wrong_codes_count_towards_the_lockout(app):
    turn_on_mfa()
    c = app.test_client()
    password_login(c)
    for _ in range(security.MAX_FAILED_LOGINS):
        r = c.post("/login/mfa", data={"code": "000000", "csrf_token": token(c.get("/login/mfa").text)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    assert user("prashasthi")["locked_until"] is not None
    assert "locked" in c.get("/login").text


def test_a_recovery_code_signs_in_once(app):
    _, codes = turn_on_mfa()
    c = app.test_client()
    password_login(c)
    c.post("/login/mfa", data={"code": codes[0], "csrf_token": token(c.get("/login/mfa").text)})
    assert c.get("/").status_code == 200
    c2 = app.test_client()
    password_login(c2)
    r = c2.post("/login/mfa", data={"code": codes[0], "csrf_token": token(c2.get("/login/mfa").text)})
    assert r.status_code == 200 and "That code isn" in r.text and c2.get("/").status_code == 302


def test_the_mfa_step_expires(app):
    turn_on_mfa()
    c = app.test_client()
    password_login(c)
    with c.session_transaction() as s:
        s["mfa_pending"] = dict(s["mfa_pending"], at=int(time.time()) - 3600)
    assert c.get("/login/mfa").headers["Location"].endswith("/login")


def test_setting_up_mfa_from_the_account_page(app):
    c = app.test_client()
    password_login(c)
    t = token(c.get("/account?tab=security").text)
    c.post("/account?tab=security", data={"action": "mfa_start", "csrf_token": t})
    page = c.get("/account?tab=security&setup=1").text
    assert "<svg" in page and "Setup key" in page
    assert not user("prashasthi")["mfa_enabled"]                            # not on until a code proves the scan
    secret = mfa.unseal(user("prashasthi")["mfa_secret"])
    r = c.post("/account?tab=security", data={"action": "mfa_confirm", "code": mfa.code_at(secret, mfa.current_step()),
                                              "csrf_token": t})
    assert "Your recovery codes" in r.text and user("prashasthi")["mfa_enabled"] == 1
    assert len(re.findall(r"<li>[a-z2-9]{5}-[a-z2-9]{5}</li>", r.text)) == 10


def test_required_roles_must_set_up_mfa_first(app, monkeypatch):
    monkeypatch.setenv("MFA_REQUIRED_ROLES", "admin")
    c = app.test_client()
    password_login(c, "admin")
    r = c.get("/settings")
    assert r.status_code == 302 and "tab=security" in r.headers["Location"]
    assert c.get("/account?tab=security").status_code == 200


def test_admin_can_reset_someones_mfa(app):
    turn_on_mfa("prashasthi")
    c = app.test_client()
    password_login(c, "admin")
    c.post("/settings", data={"action": "reset_mfa", "uid": user("prashasthi")["id"], "csrf_token": token(c.get("/settings").text)})
    u = user("prashasthi")
    assert u["mfa_enabled"] == 0 and u["mfa_secret"] is None


# ---------------------------------------------------------------- Google
def id_token(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def google_round_trip(c, monkeypatch, email="new.person@example.com", sub="g-123", **over):
    r = c.get("/auth/google")
    assert r.status_code == 302 and r.headers["Location"].startswith(google_auth.AUTH_URL)
    q = parse_qs(urlparse(r.headers["Location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    claims = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "sub": sub, "email": email,
              "email_verified": True, "name": "New Person", "exp": time.time() + 600, "nonce": q["nonce"][0]}
    claims.update(over)
    monkeypatch.setattr(google_auth, "exchange_code", lambda code, verifier, uri: {"id_token": id_token(claims)})
    return c.get("/auth/google/callback", query_string={"state": over.get("state", q["state"][0]), "code": "abc"})


def test_google_sign_up_creates_an_inactive_account_for_an_admin_to_approve(app, monkeypatch):
    c = app.test_client()
    r = google_round_trip(c, monkeypatch)
    assert r.headers["Location"].endswith("/login")
    assert "administrator has to approve" in c.get("/login").text
    u = db.query("SELECT * FROM users WHERE google_sub='g-123'", one=True)
    assert u["active"] == 0 and u["role"] == "ap" and u["has_password"] == 0 and u["username"] == "newperson"
    r = google_round_trip(app.test_client(), monkeypatch)                  # trying again before approval
    db.execute("UPDATE users SET active=1 WHERE id=?", (u["id"],))
    c = app.test_client()
    google_round_trip(c, monkeypatch)
    assert c.get("/").status_code == 200


def test_google_links_to_an_existing_account_by_verified_email(app, monkeypatch):
    db.execute("UPDATE users SET email='ap@example.com' WHERE username='prashasthi'")
    c = app.test_client()
    google_round_trip(c, monkeypatch, email="ap@example.com", sub="g-ap")
    assert c.get("/").status_code == 200 and user("prashasthi")["google_sub"] == "g-ap"


def test_google_sign_in_still_asks_for_the_mfa_code(app, monkeypatch):
    db.execute("UPDATE users SET google_sub='g-ap' WHERE username='prashasthi'")
    turn_on_mfa()
    c = app.test_client()
    r = google_round_trip(c, monkeypatch, email="ap@example.com", sub="g-ap")
    assert r.headers["Location"].endswith("/login/mfa") and c.get("/").status_code == 302


@pytest.mark.parametrize("bad", [{"state": "forged"}, {"aud": "someone-else"}, {"email_verified": False},
                                 {"exp": 1}, {"nonce": "replayed"}, {"iss": "https://evil.example"}])
def test_google_responses_that_fail_a_check_are_refused(app, monkeypatch, bad):
    c = app.test_client()
    r = google_round_trip(c, monkeypatch, **bad)
    assert r.headers["Location"].endswith("/login")
    assert db.query("SELECT COUNT(*) n FROM users WHERE google_sub='g-123'", one=True)["n"] == 0
    assert c.get("/").status_code == 302


def test_allowed_domains_and_signup_off(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "mycompany.com")
    c = app.test_client()
    google_round_trip(c, monkeypatch, email="someone@gmail.com")
    assert "Use your work Google account" in c.get("/login").text
    monkeypatch.setenv("GOOGLE_SIGNUP", "off")
    c = app.test_client()
    google_round_trip(c, monkeypatch, email="new@mycompany.com", sub="g-9")
    assert "Ask your administrator to add you" in c.get("/login").text
    assert db.query("SELECT COUNT(*) n FROM users WHERE google_sub IS NOT NULL", one=True)["n"] == 0


def test_google_button_is_hidden_until_configured(app, monkeypatch):
    assert "Continue with Google" in app.test_client().get("/login").text
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    assert "Continue with Google" not in app.test_client().get("/login").text
    assert app.test_client().get("/auth/google").status_code == 404


# ---------------------------------------------------------------- username or email
def test_sign_in_with_email_instead_of_username(app):
    db.execute("UPDATE users SET email='Priya@Example.com' WHERE username='prashasthi'")
    c = app.test_client()
    password_login(c, "priya@example.com")
    assert c.get("/").status_code == 200
    db.execute("UPDATE users SET email='shared@example.com' WHERE username IN ('prashasthi', 'ap_user2')")
    c = app.test_client()
    r = password_login(c, "shared@example.com")                 # two accounts share it: refused, like a wrong password
    assert r.status_code == 200 and c.get("/").status_code == 302


# ---------------------------------------------------------------- access requests approved from the admin's email
@pytest.fixture()
def mails(monkeypatch):
    import mailer
    sent = []
    monkeypatch.setenv("ACCESS_EMAILS_SYNC", "1")
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, html=None: sent.append((to, subject, body, html)) or (True, to, None))
    db.execute("UPDATE users SET email='boss@realcompany.com' WHERE username='admin'")
    return sent


def request_link(mails):
    to, subject, body, html = next(m for m in mails if m[1].startswith("ClearPay access request"))
    return to, re.search(r"(/access-request/[\w-]+)\?d=approve", body).group(1), html


def test_new_google_account_emails_admins_approve_and_reject_links(app, monkeypatch, mails):
    google_round_trip(app.test_client(), monkeypatch)
    to, link, html = request_link(mails)
    assert to == "boss@realcompany.com" and "Approve" in html and "Reject" in html
    assert db.query("SELECT token_hash FROM access_requests", one=True)["token_hash"] not in link   # only the hash is stored
    c = app.test_client()
    page = c.get(link + "?d=approve").text                               # opening the link changes nothing
    assert "Access request" in page and user("newperson")["active"] == 0
    c.post(link, data={"decision": "approve", "csrf_token": token(page)})
    assert user("newperson")["active"] == 1
    assert any(m[0] == "new.person@example.com" and "approved" in m[1] for m in mails)
    assert "already approved" in c.get(link).text                        # one-time
    c = app.test_client()
    google_round_trip(c, monkeypatch)
    assert c.get("/").status_code == 200


def test_rejected_request_tells_the_person_and_blocks_sign_in(app, monkeypatch, mails):
    google_round_trip(app.test_client(), monkeypatch)
    _, link, _ = request_link(mails)
    c = app.test_client()
    c.post(link, data={"decision": "reject", "csrf_token": token(c.get(link).text)})
    assert user("newperson")["active"] == 0
    c = app.test_client()
    google_round_trip(c, monkeypatch)
    assert "was declined" in c.get("/login").text


def test_expired_or_unknown_links_do_nothing(app, monkeypatch, mails):
    google_round_trip(app.test_client(), monkeypatch)
    _, link, _ = request_link(mails)
    db.execute("UPDATE access_requests SET expires_at='2000-01-01T00:00:00+00:00'")
    c = app.test_client()
    page = c.get(link).text
    assert "expired" in page
    c.post(link, data={"decision": "approve", "csrf_token": token(page) if 'csrf_token' in page else ""})
    assert user("newperson")["active"] == 0
    assert app.test_client().get("/access-request/not-a-real-token").status_code == 404


def test_admin_can_approve_or_reject_in_settings_too(app, monkeypatch, mails):
    google_round_trip(app.test_client(), monkeypatch)
    c = app.test_client()
    password_login(c, "admin")
    c.post("/settings", data={"action": "reject_request", "uid": user("newperson")["id"], "csrf_token": token(c.get("/settings").text)})
    assert "Declined" in c.get("/settings").text and user("newperson")["active"] == 0


def test_waiting_person_trying_again_resends_only_when_no_live_request(app, monkeypatch, mails):
    google_round_trip(app.test_client(), monkeypatch)
    count = lambda: sum(1 for m in mails if m[1].startswith("ClearPay access request"))
    assert count() == 1
    google_round_trip(app.test_client(), monkeypatch)                   # request still open: no second email
    assert count() == 1
    db.execute("DELETE FROM access_requests")                            # like a request made before emails existed
    google_round_trip(app.test_client(), monkeypatch)
    assert count() == 2


# ---------------------------------------------------------------- sign-in without a reload (login.js)
ASYNC = {"X-ClearPay-Async": "1"}


def test_background_sign_in_answers_in_json(app):
    c = app.test_client()
    t = token(c.get("/login").text)
    bad = c.post("/login", data={"username": "prashasthi", "password": "wrong", "csrf_token": t}, headers=ASYNC).get_json()
    assert bad["ok"] is False and bad["error"]
    assert c.get("/").status_code == 302                                  # still signed out
    good = c.post("/login", data={"username": "prashasthi", "password": PASSWORD, "csrf_token": t}, headers=ASYNC).get_json()
    assert good["ok"] is True and good["next"].endswith("/") and good["first"] == user("prashasthi")["full_name"].split()[0]
    assert good["initials"] and good["color"]
    assert c.get("/").status_code == 200                                  # the session is real


def test_background_sign_in_hands_over_to_the_code_step(app):
    secret, _ = turn_on_mfa()
    c = app.test_client()
    t = token(c.get("/login").text)
    r = c.post("/login", data={"username": "prashasthi", "password": PASSWORD, "csrf_token": t}, headers=ASYNC).get_json()
    assert r["ok"] is True and r.get("mfa") and r["next"].endswith("/login/mfa") and "name" not in r
    page = c.get("/login/mfa").text
    wrong = c.post("/login/mfa", data={"code": "000000", "csrf_token": token(page)}, headers=ASYNC).get_json()
    assert wrong["ok"] is False and "code" in wrong["error"]
    right = c.post("/login/mfa", data={"code": mfa.code_at(secret, mfa.current_step()), "csrf_token": token(page)}, headers=ASYNC).get_json()
    assert right["ok"] is True and right["name"] and c.get("/").status_code == 200

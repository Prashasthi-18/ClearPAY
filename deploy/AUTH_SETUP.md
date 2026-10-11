# Google sign-in and two-factor authentication: setup

ClearPay supports three ways to sign in, and they can be combined:

| | What the person does | Needs setup? |
|---|---|---|
| Username + password | Types their ClearPay password | No |
| **Continue with Google** | Picks their Google account | Yes: a Google OAuth client (part A) |
| **Two-factor (MFA)** | Types a 6-digit code from an authenticator app after either of the above | No setup by the admin; each person turns it on (part B) |

Google sign-in and two-factor are independent. Someone with two-factor turned on is asked for their code after Google too.

---

## Part A: Google sign-in

Allow about 10 minutes. You need a Google account. To limit sign-in to your company, it should be a Google Workspace account for that company.

### A1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in.
2. At the top, open the project picker, choose **New project**, name it `ClearPay` and press **Create**.
3. Make sure the new project is selected in the picker.

### A2. Set up the consent screen ("Google Auth Platform")

1. In the menu go to **APIs & Services → OAuth consent screen**. Newer consoles call this **Google Auth Platform**; press **Get started** if you see it.
2. **App information:** App name `ClearPay`, User support email = your email. **Next**.
3. **Audience:**
   - **Internal** if everyone signs in with your company's Google Workspace. Only your organisation's accounts can use it, and no Google review is needed. This is the best option for a company.
   - **External** for personal Gmail accounts, or if you don't have Workspace.
4. **Contact information:** your email. Agree to the policy and press **Create**.
5. If you picked **External**, the app starts in **Testing** mode, where only listed test users can sign in. Either:
   - go to **Audience → Test users → Add users** and add every Gmail address that will sign in (up to 100; fine for a hackathon or demo), or
   - press **Publish app** on the Audience page. ClearPay only asks for `openid`, `email` and `profile`, which are non-sensitive scopes, so publishing doesn't need Google's security review.
6. Optional: under **Data access → Add or remove scopes**, tick `openid`, `.../auth/userinfo.email` and `.../auth/userinfo.profile`. ClearPay requests these anyway; listing them just shows them on the consent screen.

### A3. Create the OAuth client

1. Go to **APIs & Services → Credentials**, or **Google Auth Platform → Clients**.
2. Press **Create credentials → OAuth client ID**, or **Create client**.
3. **Application type: Web application.** Name: `ClearPay web`.
4. **Authorised redirect URIs:** add the address of ClearPay followed by `/auth/google/callback`. It must match **exactly**: http vs https, `localhost` vs `127.0.0.1`, the port, and no trailing slash.

   | Where ClearPay runs | Redirect URI to add |
   |---|---|
   | On your laptop (`python app.py`) | `http://127.0.0.1:5000/auth/google/callback` |
   | Also add, if you open it as localhost | `http://localhost:5000/auth/google/callback` |
   | On your server | `https://clearpay.yourcompany.com/auth/google/callback` |

   Google allows plain `http` only for `localhost` and `127.0.0.1`. Everything else must be `https`.
5. Leave **Authorised JavaScript origins** empty; ClearPay doesn't use them.
6. Press **Create**. Copy the **Client ID** and **Client secret**, or press **Download JSON**. Newer consoles only show the secret once.

### A4. Put the values in `.env`

```ini
GOOGLE_CLIENT_ID=1234567890-abc123.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx

# Only these email domains may sign in with Google (recommended for a company). Empty = any Google account.
GOOGLE_ALLOWED_DOMAINS=yourcompany.com

# request = someone without an account can ask for one (created inactive; an admin approves it)
# off     = admins add every user themselves; unknown Google accounts are refused
GOOGLE_SIGNUP=request

# On a server: set this to the exact redirect URI you registered (avoids http/https mix-ups behind a proxy)
GOOGLE_REDIRECT_URI=
```

Keep the client secret out of git. `.env` is already in `.gitignore`.

### A5. Restart and test

1. Stop ClearPay (Ctrl+C) and run `python app.py` again.
2. Open <http://127.0.0.1:5000>. The sign-in page now shows **Continue with Google**, plus **Request access with Google** when sign-up is on.
3. Test each case:
   - **Existing user:** set a user's email to their Google address in **Account → Profile**, or have an admin do it. The first Google sign-in links the accounts automatically and sends that user a security notification. After that, linking uses Google's account id, not the email.
   - **Link from inside ClearPay:** sign in with a password, then go to **Account → Security → Connect Google account**.
   - **New person (sign-up):** press **Continue with Google** with an account ClearPay doesn't know. The person sees "Request sent". Admins get a **New account request** notification. In **Settings & users** the account shows **Awaiting approval**; press **Activate** to let them in with the AP team role. They then sign in with Google again.

### How it is secured

- Standard OpenID Connect authorization-code flow with **PKCE**, a random **state** (stops login CSRF) and a **nonce** (stops token replay).
- The ID token is fetched directly from Google's token endpoint over HTTPS with the client secret. Its issuer, audience (your client ID), expiry, nonce and `email_verified` are all checked.
- Accounts are linked by Google's permanent account id (`sub`). Email is used only for the first automatic link, and only if exactly one active ClearPay account has that verified address.
- Sign-ups are created **inactive**. Nobody gets access until an admin activates them.
- The account lockout, deactivation, 30-minute idle timeout, new-device alerts and audit log apply exactly as for passwords.

### Troubleshooting

| What you see | Fix |
|---|---|
| Google says **Error 400: redirect_uri_mismatch** | The redirect URI in A3 doesn't match the address in the browser. Compare them character by character (127.0.0.1 vs localhost, port, http/https), or set `GOOGLE_REDIRECT_URI`. Changes in Google can take a few minutes to apply. |
| **Access blocked: ClearPay has not completed the Google verification process** | The app is External + Testing and this account isn't a test user. Add it (A2 step 5) or publish the app. |
| **Error 403: org_internal** | The app is Internal and this isn't an account from your Workspace organisation. |
| ClearPay: "... can't sign in to ClearPay. Use your work Google account." | The email's domain isn't in `GOOGLE_ALLOWED_DOMAINS`. |
| ClearPay: "There's no ClearPay account for ..." | `GOOGLE_SIGNUP=off` and no user has this Google account or email. Add the user in Settings & users with that email. |
| ClearPay: "Your account request is waiting for an administrator" | An admin must press **Activate** in Settings & users. |
| ClearPay: "That Google sign-in link expired" | The sign-in took over 10 minutes, the browser blocked cookies, or the page was opened in two tabs. Start again. |
| No Google button at all | `GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_SECRET` is empty, or ClearPay wasn't restarted. |

---

## Part B: Two-factor authentication (authenticator app)

Nothing to set up with Google or anyone else. It uses the standard TOTP method (RFC 6238): 6 digits that change every 30 seconds.

### B1. Recommended settings in `.env` (admin, once)

```ini
# Encrypt the authenticator secrets stored in the database. Generate a key ONCE:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MFA_ENCRYPTION_KEY=paste-the-generated-key-here

# Make two-factor compulsory for some roles (comma-separated: admin, manager, ap). Empty = optional for everyone.
MFA_REQUIRED_ROLES=admin,manager
```

- Set `MFA_ENCRYPTION_KEY` **before anyone turns on two-factor**, and back it up with your other secrets. If it is lost or changed, everyone's two-factor stops working and an admin must reset it for each person. Without the key, the secrets are stored unencrypted, which is acceptable for a local demo.
- With `MFA_REQUIRED_ROLES`, someone in those roles who hasn't set up two-factor can sign in but is sent straight to **Account → Security** and can't open anything else until it's done.
- Restart ClearPay after changing `.env`.

### B2. Turning it on (each person)

1. Install an authenticator app on your phone: **Google Authenticator**, **Microsoft Authenticator**, **Authy**, or a password manager with one built in (1Password, Bitwarden).
2. In ClearPay open the avatar menu → **Account → Security → Set up authenticator app**.
3. In the app press **+** and **Scan a QR code**, then scan the code on screen. If you can't scan, choose **Enter a setup key** and type the key shown under the QR code, as time-based with 6 digits.
4. Type the 6-digit code the app shows and press **Turn on**.
5. **Save the 10 recovery codes** shown next. Use **Copy codes** or **Print**. They are shown only once. Each one replaces a phone code once, for when you don't have your phone.

Turning it on signs out your other sessions, and you get a security notification.

### B3. Signing in with it

1. Sign in as usual (password or Google).
2. ClearPay asks for the **Authentication code**. Type the current 6-digit code from the app.
3. No phone? Type a recovery code instead (like `abcde-fghjk`). You'll get a notification saying how many are left.

You have 5 minutes to enter the code. Wrong codes count toward the same lockout as wrong passwords: 5 failures lock the account for 15 minutes and alert the admins. A code can't be used twice.

### B4. Managing it

| Task | Where |
|---|---|
| Make new recovery codes (old ones stop working) | Account → Security → enter a current code → **Make new recovery codes** |
| Turn it off (not allowed for required roles) | Account → Security → enter a current code → **Turn off** |
| Lost phone **and** recovery codes | An admin: **Settings & users → Reset 2FA** next to the user. Their sessions end; they sign in with password/Google and set it up again. |
| See what happened | Account → **Sign-in activity** shows two-factor events. Admins see everything in the **Audit log**. |

### B5. Troubleshooting

| Problem | Fix |
|---|---|
| "That code didn't match" when turning it on | Your phone's clock is off. Set date & time to **automatic** on the phone, then use the newest code. ClearPay accepts one 30-second step of drift either way. |
| Codes always rejected at sign-in | Same clock fix as above. Also make sure you're reading the **ClearPay** entry in the app if you have several. |
| "MFA secrets are encrypted but MFA_ENCRYPTION_KEY is not set" | Put the original key back in `.env` and restart. If it's lost, an admin must use **Reset 2FA** for each user. |
| Locked out after wrong codes | Wait 15 minutes, or an admin presses **Unlock** in Settings & users. |

---

## For the demo

1. Sign in as `admin` and turn on two-factor (B2) with your phone.
2. Sign out and sign in again: password, then the phone code. Then try a recovery code.
3. With Google configured: press **Continue with Google** with an unknown account to show the access request, then approve it as admin in **Settings & users**.

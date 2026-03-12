# 09 — Email OTP Authentication

## What This File Covers

The complete email OTP verification flow, from the user's perspective and the code's perspective. How to configure Gmail SMTP, the `EmailVerification` model design, all three OTP views, and how to test without sending real emails.

**Prerequisites:** File 08 (API Endpoints & Views) — you need to understand `/request-otp/`, `/verify-otp/`, and `/resend-otp/`.

---

## 1. Why Email OTP?

Email OTP (One-Time Password) solves a specific problem: how do you confirm a user is real and has a valid email address without making them create an account with a password?

**Benefits:**
- No password to forget, lose, or reuse
- No registration form — just name and email
- Verified email = real contact for follow-up
- Simple to implement — no OAuth apps, no password hashing

**How it works in 60 seconds:**
1. User enters name and email
2. Server generates a 6-digit code and emails it
3. User checks their email and enters the code
4. Server verifies the code → creates a session → user can now chat

The code expires in **1 minute** and can be resent **at most once**.

---

## 2. Gmail SMTP Setup

DocChat sends OTP emails using Gmail's SMTP server. To use Gmail for automated emails, you need an **App Password** — not your regular Google account password.

**Why App Passwords?**
Google blocks apps from logging in with your normal password for security reasons. App Passwords are 16-character passwords generated specifically for a single app. You can revoke them at any time without changing your main password.

### Step-by-Step: Create a Gmail App Password

**Prerequisites:** Two-Step Verification (2FA) must be enabled on your Google account.

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Click **Security** in the left sidebar
3. Under "How you sign in to Google", click **2-Step Verification**
4. Scroll down to **App passwords** and click it
5. In the "App name" field, type "DocChat" (or any name)
6. Click **Create**
7. Google shows a 16-character password like `abcd efgh ijkl mnop`
8. Copy it (remove spaces): `abcdefghijklmnop`

### Configure `.env`

```ini
EMAIL_HOST_USER=your-gmail@gmail.com
EMAIL_APP_PASSWORD=abcdefghijklmnop
```

### Django Settings (already configured in `dochat/settings.py`)

```python
EMAIL_BACKEND       = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST          = "smtp.gmail.com"
EMAIL_PORT          = 587
EMAIL_USE_TLS       = True
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
DEFAULT_FROM_EMAIL  = os.environ.get("EMAIL_HOST_USER", "noreply@docchat.local")
```

- Port 587 with TLS is the standard for Gmail SMTP
- `EMAIL_HOST_PASSWORD` maps to `EMAIL_APP_PASSWORD` from `.env` — note the different variable name

---

## 3. Testing Without Sending Real Emails

During development, you do not want to send real emails — it is slow, uses your Gmail quota, and requires a working Gmail account from the start.

Django has a built-in "console" email backend that prints emails to the terminal instead of sending them.

**To use it during development:**

In `dochat/settings.py`, temporarily change:

```python
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

Or better, make it conditional on `DEBUG`:

```python
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
```

Now when an OTP is requested, instead of sending an email, Django prints the full email content to your terminal:

```
Content-Type: text/plain; charset="utf-8"
Subject: Your Verification Code
From: your-gmail@gmail.com
To: user@example.com

Your verification code is: 847293
This code expires in 1 minute.
```

Copy the 6-digit code from the terminal output and enter it in the browser.

---

## 4. The `EmailVerification` Model

```python
class EmailVerification(models.Model):
    email        = models.EmailField(db_index=True)
    name         = models.CharField(max_length=200)
    mobile       = models.CharField(max_length=20, blank=True, default="")
    code         = models.CharField(max_length=6)
    created_at   = models.DateTimeField(auto_now_add=True)
    expires_at   = models.DateTimeField()
    is_verified  = models.BooleanField(default=False)
    resend_count = models.IntegerField(default=0)
```

**Security design decisions:**

1. **`code` is stored as plain text** — OTP codes are single-use and short-lived (1 minute). Hashing a 6-digit code provides minimal security benefit and complicates comparison. This is acceptable for OTP codes (unlike passwords, which must always be hashed).

2. **`secrets.randbelow(900_000) + 100_000`** — generates a cryptographically random 6-digit number. Python's built-in `random` module is predictable; `secrets` is not. Range: 100,000 to 999,999 (always exactly 6 digits).

3. **1-minute expiry** — `expires_at = timezone.now() + timedelta(minutes=1)`. Short enough to limit brute-force attempts.

4. **`resend_count` capped at 1** — prevents a malicious user from using the "resend" feature to spam email inboxes.

5. **`is_verified = True` after use** — the record is kept as an audit trail even after the code is used.

---

## 5. The Three OTP Views

### `request_otp_view` — Send the Code

```python
@api_view(["POST"])
def request_otp_view(request):
    name   = request.data.get("name", "").strip()
    email  = request.data.get("email", "").strip().lower()
    mobile = request.data.get("mobile", "").strip()

    if not email:
        return Response({"error": "Email is required."}, status=400)

    # 1. Delete stale (expired + unverified) records for this email
    EmailVerification.objects.filter(
        email=email, is_verified=False, expires_at__lt=timezone.now()
    ).delete()

    # 2. Check if a valid unexpired record already exists
    existing = EmailVerification.objects.filter(
        email=email, is_verified=False, expires_at__gt=timezone.now()
    ).first()

    if existing:
        verification = existing  # Reuse — do not generate a new code
    else:
        # 3. Create a fresh verification record
        verification = EmailVerification.objects.create(
            email=email,
            name=name,
            mobile=mobile,
            code=EmailVerification.generate_code(),
            expires_at=timezone.now() + timedelta(minutes=1),
        )

    # 4. Send the email
    _send_verification_email(email, name, verification.code)

    # 5. Return the verification ID and an obfuscated email hint
    parts = email.split("@")
    hint = parts[0][0] + "***" + parts[0][-1] + "@" + parts[1]

    return Response({
        "verification_id": verification.pk,
        "email_hint": hint,
    })
```

**Why reuse existing records?** If the user clicks "Request OTP" twice in quick succession, we do not want to send two emails with different codes. We reuse the existing valid code.

### `verify_otp_view` — Check the Code

```python
@api_view(["POST"])
def verify_otp_view(request):
    verification_id = request.data.get("verification_id")
    code = request.data.get("code", "").strip()

    try:
        verification = EmailVerification.objects.get(pk=verification_id)
    except EmailVerification.DoesNotExist:
        return Response({"error": "Invalid verification request."}, status=400)

    # Check expiry
    if verification.is_expired:
        return Response({"error": "Code expired. Please request a new code."}, status=400)

    # Check the code
    if verification.code != code:
        return Response({"error": "Invalid code. Please try again."}, status=400)

    # Mark as verified
    verification.is_verified = True
    verification.save(update_fields=["is_verified"])

    # Create the chat session
    active_doc = Document.get_active()
    session = ChatSession.objects.create(
        session_key=str(uuid.uuid4()),
        user_name=verification.name,
        user_email=verification.email,
        user_mobile=verification.mobile,
        document_name=active_doc.original_filename if active_doc else "",
    )

    return Response({"token": session.session_key})
```

### `resend_otp_view` — Regenerate and Resend

```python
@api_view(["POST"])
def resend_otp_view(request):
    verification_id = request.data.get("verification_id")

    try:
        verification = EmailVerification.objects.get(pk=verification_id, is_verified=False)
    except EmailVerification.DoesNotExist:
        return Response({"error": "Invalid verification request."}, status=400)

    # Check resend limit
    if verification.resend_count >= 1:
        return Response({"error": "Maximum resend limit reached."}, status=429)

    # Generate new code and reset expiry
    verification.refresh_code()      # generates new code + resets expires_at
    verification.resend_count += 1
    verification.save()

    # Send new email
    _send_verification_email(verification.email, verification.name, verification.code)

    parts = verification.email.split("@")
    hint = parts[0][0] + "***" + parts[0][-1] + "@" + parts[1]

    return Response({"status": "resent", "email_hint": hint})
```

---

## 6. Sending the Email

```python
def _send_verification_email(to_email: str, name: str, code: str):
    subject = "Your Verification Code"

    # Plain text version
    text_body = f"Hi {name},\n\nYour verification code is: {code}\n\nThis code expires in 1 minute."

    # HTML version (from template)
    html_body = render_to_string("emails/verification_code.html", {
        "name": name,
        "code": code,
    })

    # EmailMultiAlternatives sends both HTML and plain text
    # Email clients show HTML if supported, plain text otherwise
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
```

The HTML template (`templates/emails/verification_code.html`) displays the code in a large, readable format with styling.

---

## 7. Admin Controls

The four fields in `ChatSessionConfig` let the admin control the verification flow:

| Field | Default | Effect When False |
|-------|---------|------------------|
| `collect_name` | True | Name input hidden; `ChatSession.user_name` will be blank |
| `collect_email` | True | Email input hidden; no OTP possible |
| `collect_mobile` | False | Mobile input hidden |
| `verify_email` | True | No OTP sent; session created directly by `/start-session/` |

**Important dependency:** `verify_email` only applies when `collect_email` is also `True`. If `collect_email` is `False`, the OTP flow never triggers regardless of `verify_email`.

The frontend reads these settings from `GET /session-config/` on page load and adjusts its UI accordingly.

---

## 8. Security Notes

**Single-use codes:** After `verify_otp_view` marks `is_verified = True`, the code cannot be used again (the view checks `is_verified=False` when looking up verifications for resend, and checks `is_verified` status implicitly via the record lookup).

**Short expiry:** 1 minute gives an attacker at most 999,999 possible codes to try (6-digit codes). In 1 minute, even an automated attack can make only a few hundred requests — far too few to brute-force a 6-digit code.

**Rate limiting (production recommendation):** Consider adding Django's `ratelimit` package to `/request-otp/` to prevent automated OTP flooding. Without rate limiting, a bot could request thousands of OTPs to spam email addresses.

**Audit trail:** `EmailVerification` records with `is_verified=True` are kept indefinitely. This allows admins to investigate suspicious activity (multiple OTP requests from the same email in a short period).

---

## What to Do Next

Read [File 10 — ReAct Agent Loop](10_react_agent_loop.md) to understand the advanced multi-step reasoning mode that can search the document, retrieve specific pages, and remember users across sessions.

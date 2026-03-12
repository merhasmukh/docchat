# 08 — API Endpoints and Views

## What This File Covers

Every REST endpoint in DocChat, what it does, how Django REST Framework is used, the session token system, and a deep dive into how SSE (Server-Sent Events) streaming is implemented.

**Prerequisites:** File 07 (LLM Providers).

---

## 1. What is Django REST Framework?

Django REST Framework (DRF) is a toolkit that makes it easier to build REST APIs in Django. DocChat uses two features from DRF:

**`@api_view` decorator** — restricts a view to specific HTTP methods and returns proper JSON error responses for invalid requests:

```python
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["POST"])
def my_endpoint(request):
    data = request.data  # parsed JSON body
    return Response({"status": "ok"})
```

**Auto-generated OpenAPI docs** — visit `http://127.0.0.1:8000/api/docs/` to see interactive Swagger documentation for all endpoints.

---

## 2. The Session Token System

DocChat does not use Django's built-in session cookies for end-user authentication. Instead, it uses a custom UUID token:

**Why not cookies?**
Cookies are blocked in embedded `<iframe>` widgets (cross-origin restrictions). A UUID token sent in a custom header works everywhere.

**How it works:**

1. User completes OTP verification → server creates a `ChatSession` with a UUID `session_key`
2. The UUID is returned to the browser: `{"token": "f47ac10b-58cc-4372-a567..."}`
3. Browser stores it in `localStorage`: `localStorage.setItem("chatToken", token)`
4. Every subsequent request sends it as a header: `X-Chat-Token: f47ac10b-...`
5. Server looks up the session: `ChatSession.objects.get(session_key=token)`

**The `_get_chat_session()` helper:**

```python
def _get_chat_session(request):
    token = request.headers.get("X-Chat-Token", "").strip()
    if not token:
        return None
    try:
        return ChatSession.objects.get(session_key=token)
    except ChatSession.DoesNotExist:
        return None
```

---

## 3. Every Endpoint Explained

All endpoints are in `chat/views.py` and `chat/urls.py`.

---

### `GET /` — Main Chat UI

```python
@ensure_csrf_cookie
def index_view(request):
    return render(request, "index.html")
```

Serves the main `templates/index.html` page. `@ensure_csrf_cookie` forces Django to set the CSRF cookie even on a GET request — the frontend JavaScript needs it for POST requests.

---

### `GET /widget/` — Embeddable Widget

```python
@xframe_options_exempt
def widget_view(request):
    return render(request, "widget.html")
```

Same as `index_view` but with `@xframe_options_exempt` — this removes the `X-Frame-Options` HTTP header that would otherwise prevent the page from being embedded in an `<iframe>` on another site.

---

### `GET /status/` — Check Active Document and Session

**Purpose:** The frontend calls this on page load to know whether a document is ready and whether the current session token is still valid.

**Request:** No body. Optionally include `X-Chat-Token` header.

**Response:**
```json
{
    "document_loaded": true,
    "filename": "Prospectus_2024.pdf",
    "total_pages": 42,
    "session_active": true
}
```

`session_active` is `true` only if the `X-Chat-Token` matches a `ChatSession` in the database. If the token is invalid or missing, it returns `"session_active": false`.

**What the frontend does with this:** If `session_active` is `true`, it skips the login modal and goes directly to the chat. If `false`, it shows the name/email/OTP form.

---

### `GET /history/` — Retrieve Chat History

**Purpose:** Loads previous messages for the current session (shown when the user returns to the page).

**Request:** Requires `X-Chat-Token` header.

**Response:**
```json
[
    {"role": "user", "content": "What are the fees?"},
    {"role": "assistant", "content": "The annual fees for MCA are ₹45,000."}
]
```

Returns the last 50 messages ordered chronologically. This history is also passed to the LLM on the next question (so it has conversation context).

---

### `GET /session-config/` — Client Configuration

**Purpose:** Tells the frontend what user information to collect before allowing chat. Reads from `ChatSessionConfig`.

**Request:** No headers required.

**Response:**
```json
{
    "collect_name": true,
    "collect_email": true,
    "collect_mobile": false,
    "verify_email": true
}
```

The frontend uses this to decide which form fields to show. If `collect_email` is `false`, the email field is hidden. If `verify_email` is `false`, no OTP step is shown.

---

### `POST /start-session/` — Create Session Without OTP

**Purpose:** Creates a `ChatSession` directly when email verification is disabled (`verify_email=False`).

**Request body:**
```json
{
    "name": "Rahul Shah",
    "email": "rahul@example.com",
    "mobile": "9876543210"
}
```

Only `name`, `email`, and `mobile` fields that are enabled in `ChatSessionConfig` are required.

**Response:**
```json
{"token": "f47ac10b-58cc-4372-a567-0e02b2c3d479"}
```

The frontend stores this token in `localStorage`.

---

### `POST /request-otp/` — Send OTP Email

**Purpose:** Validates the user's name and email, creates an `EmailVerification` record, and sends the 6-digit code by email.

**Request body:**
```json
{"name": "Priya Patel", "email": "priya@example.com"}
```

**Response (success):**
```json
{
    "verification_id": 42,
    "email_hint": "p***a@example.com"
}
```

`email_hint` is an obfuscated version of the email shown on the OTP entry screen (e.g., "Your code was sent to p***a@example.com").

**What happens internally:**
1. Delete any existing unverified records for this email (cleanup)
2. If a valid, unexpired record exists → reuse it (do not send a new code)
3. Otherwise → generate code, set 1-minute expiry, save `EmailVerification`, send email

**Response (error):**
```json
{"error": "Invalid email address"}
```

---

### `POST /verify-otp/` — Verify Code and Create Session

**Purpose:** Checks the entered code, marks the verification complete, and creates a `ChatSession`.

**Request body:**
```json
{"verification_id": 42, "code": "847293"}
```

**Response (success):**
```json
{"token": "f47ac10b-58cc-4372-a567-0e02b2c3d479"}
```

**What happens internally:**
1. Look up `EmailVerification` by `verification_id`
2. Check `is_expired` → return 400 if expired
3. Check `code` matches → return 400 if wrong
4. Mark `is_verified = True`
5. Create `ChatSession` with a new UUID `session_key`
6. Return the UUID as `token`

**Response (errors):**
```json
{"error": "Code expired. Please request a new code."}
{"error": "Invalid code. Please try again."}
```

---

### `POST /resend-otp/` — Resend OTP

**Purpose:** Generates a new code and resends the email. Maximum 1 resend per verification record.

**Request body:**
```json
{"verification_id": 42}
```

**Response (success):**
```json
{"status": "resent", "email_hint": "p***a@example.com"}
```

**Response (error — already used resend):**
```json
{"error": "Maximum resend limit reached."}
```

---

### `POST /chat/` — The Main Streaming Chat Endpoint

This is the most important and complex endpoint. It receives a question and returns a Server-Sent Events stream of answer tokens.

**Request:**
```
POST /chat/
X-Chat-Token: f47ac10b-58cc-4372-a567-0e02b2c3d479
Content-Type: application/json

{"question": "What are the MCA admission requirements?"}
```

**Response:** A streaming `text/event-stream` response (see Section 4 below).

**Internal flow:**

```
1. Parse request body → validate "question" is present and non-empty

2. Look up active document → if none, return 503
   "No document is currently loaded. Please contact the administrator."

3. Look up ChatSession from X-Chat-Token → if invalid, return 401

4. Load conversation history from ChatMessage records

5. Resolve context:
   - If agent_mode → skip to agent loop (see File 10)
   - If context_mode == "full" → load full markdown text
   - If context_mode == "rag" → retrieve top-3 chunks via Qdrant
   - If provider == "sarvam" → truncate to 9,000 chars

6. Lazy Gemini cache creation:
   - If provider == "gemini" AND context_mode == "full"
   - AND use_gemini_cache == True
   - AND Document.gemini_cache_name is empty
   → create_gemini_cache(markdown_text, gemini_model)

7. Create generator function:
   def generate():
       try:
           for token in ask_streaming(question, history, context, cfg, usage_out):
               # Escape newlines inside tokens (SSE protocol)
               escaped = token.replace("\n", "\\n")
               yield f"data: {escaped}\n\n"
       except GeminiCacheExpiredError:
           # Transparently recreate cache and retry
           ...
       except Exception as e:
           yield f"data: [ERROR: {e}]\n\n"
       finally:
           yield "data: [DONE]\n\n"
           # Save ChatMessage and update ChatSession totals

8. Return StreamingHttpResponse(generate(), content_type="text/event-stream")
   with headers:
     Cache-Control: no-cache
     X-Accel-Buffering: no   ← tells nginx not to buffer this response
```

**After the stream completes (in the `finally` block):**

```python
ChatMessage.objects.create(
    session=session_obj,
    provider=cfg.provider,
    model_name=model_name,
    question=question,
    answer=full_response,
    input_tokens=usage["input"],
    output_tokens=usage["output"],
    total_cost=cost,
    ...
)

# Atomically update session totals using Django F() expressions
# F() avoids race conditions — it increments the DB value directly
ChatSession.objects.filter(pk=session_obj.pk).update(
    message_count=F("message_count") + 1,
    total_tokens=F("total_tokens") + usage["total"],
    total_cost=F("total_cost") + cost,
    avg_cost_per_message=F("total_cost") / F("message_count"),
    ...
)
```

`F("message_count") + 1` means "increment the database value by 1" — it does not read the value into Python first, avoiding race conditions if two requests update the session simultaneously.

---

### `POST /reset/` — Clear Session

**Purpose:** Called when the user clicks "New Chat". Logs the session end.

**Request:** Requires `X-Chat-Token` header. No body needed.

**Response:** `{"status": "reset"}`

**Note:** This does not delete the `ChatSession` from the database — it is kept for analytics. The frontend clears `localStorage` after receiving this response.

---

## 4. Server-Sent Events (SSE) — How Streaming Works

SSE is a standard browser technology for receiving a continuous stream of text from a server over a regular HTTP connection.

### The SSE Format

```
data: Hello\n\n
data:  world\n\n
data: !\n\n
data: [DONE]\n\n
```

Each event is:
- The literal text `data: `
- The event payload (one token)
- Two newlines (`\n\n`) to signal the end of the event

The browser's event stream parser reads events separated by double newlines.

### Newline Escaping

LLM responses often contain newlines (for bullet points, paragraph breaks, etc.). But a bare `\n` in an SSE stream would be misinterpreted as an event separator. So DocChat escapes newlines inside tokens:

```python
escaped = token.replace("\n", "\\n")   # actual newline → backslash-n
yield f"data: {escaped}\n\n"
```

The browser-side JavaScript reverses this:

```javascript
const unescaped = token.replace(/\\n/g, "\n");
```

### Sentinel Events

- `data: [DONE]\n\n` — signals the end of the response. JavaScript stops reading.
- `data: [ERROR: message here]\n\n` — signals an error. JavaScript shows an error message.

### Django's `StreamingHttpResponse`

```python
from django.http import StreamingHttpResponse

response = StreamingHttpResponse(
    generate(),                          # the generator function
    content_type="text/event-stream",    # tells browser this is SSE
)
response["Cache-Control"] = "no-cache"  # do not cache the stream
response["X-Accel-Buffering"] = "no"    # tell nginx not to buffer
return response
```

`generate()` is a Python generator — it `yield`s one string at a time. Django forwards each yielded string to the browser immediately as it is produced, creating the streaming effect.

**Why `X-Accel-Buffering: no`?** In production, nginx (the reverse proxy) might buffer the entire response before sending it to the browser. This would break streaming — the user would see nothing until the full answer is ready. This header tells nginx to disable buffering for this response.

---

## 5. OpenAPI Documentation

DRF-Spectacular auto-generates OpenAPI 3.0 documentation from your view decorators:

- **Swagger UI:** `http://127.0.0.1:8000/api/docs/`
- **ReDoc:** `http://127.0.0.1:8000/api/redoc/`
- **Raw schema:** `http://127.0.0.1:8000/api/schema/`

The `/chat/` SSE endpoint cannot be fully auto-documented (SSE is not standard in OpenAPI), so `chat/schema_hooks.py` manually injects its schema description.

---

## What to Do Next

Read [File 09 — Email OTP Authentication](09_email_otp_authentication.md) to understand the complete email verification flow in detail, including Gmail SMTP setup and testing without sending real emails.

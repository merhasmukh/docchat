# 12 — Frontend and Widget

## What This File Covers

The HTML/JavaScript frontend — how the page loads, the OTP flow in JavaScript, how SSE streaming is consumed, markdown rendering, and how to embed the chatbot as an `<iframe>` widget on any external website.

**Prerequisites:** File 08 (API Endpoints) — you need to understand the SSE format and the session token system.

---

## 1. Template Architecture

Django serves HTML from the `templates/` directory:

```
templates/
├── index.html                  ← Main chat UI (served at GET /)
├── widget.html                 ← Embeddable widget (served at GET /widget/)
├── emails/
│   └── verification_code.html ← OTP email HTML
└── admin/
    └── widget_script.html      ← Admin page that shows the embed <iframe> code
```

Django is configured to find these templates:

```python
# dochat/settings.py
TEMPLATES = [{
    "DIRS": [BASE_DIR / "templates"],
    ...
}]
```

### `index.html` vs `widget.html`

Both files contain the same chat UI. The only difference:
- `index.html` is served by `index_view` — a regular page with `X-Frame-Options: SAMEORIGIN` (cannot be embedded externally)
- `widget.html` is served by `widget_view` — decorated with `@xframe_options_exempt` so it can be embedded in an `<iframe>` from any domain

---

## 2. Static Files

Source files in `static/`:

```
static/
├── css/
│   ├── style.css      ← Custom theme (warm beige/brown, Bootstrap overrides)
│   └── widget.css     ← Widget-specific styles
└── js/
    ├── main.js        ← All frontend logic
    ├── widget.js      ← Widget initialisation
    └── widget_chat.js ← Widget chat interactions
```

**In templates**, static files are referenced like this:

```html
{% load static %}
<link rel="stylesheet" href="{% static 'css/style.css' %}">
<script src="{% static 'js/main.js' %}"></script>
```

`{% static 'css/style.css' %}` generates `/static/css/style.css` in development, and in production points to wherever `collectstatic` put the files.

---

## 3. External Libraries (loaded via CDN)

```html
<!-- Bootstrap 5 — responsive layout, buttons, modals, forms -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>

<!-- Font Awesome — icons -->
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">

<!-- marked.js — render markdown to HTML -->
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>

<!-- DOMPurify — sanitise HTML before inserting into DOM -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js"></script>
```

---

## 4. Page Load Flow

When the user opens the chat page, `main.js` runs this sequence:

```
1. Fetch GET /session-config/
   → { collect_name, collect_email, collect_mobile, verify_email }
   → Show/hide form fields in the info modal accordingly

2. Fetch GET /status/
   → { document_loaded, filename, total_pages, session_active }
   → If document_loaded is false:
       Show "No document loaded" message, disable chat input

3. Check localStorage for existing token:
   const token = localStorage.getItem("chatToken");

4. If token exists AND session_active is true:
   → Skip the info/OTP modal
   → Load chat history from GET /history/ (with X-Chat-Token header)
   → Show chat UI immediately (returning user)

5. If no valid token:
   → Show the user info modal (name, email, mobile fields)
```

---

## 5. The OTP Flow in JavaScript

### Step 1 — User Submits Name and Email

```javascript
async function submitUserInfo() {
    const name  = document.getElementById("nameInput").value.trim();
    const email = document.getElementById("emailInput").value.trim();

    const response = await fetch("/request-otp/", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),  // required for POST requests
        },
        body: JSON.stringify({ name, email }),
    });

    const data = await response.json();

    if (response.ok) {
        verificationId = data.verification_id;  // save for next step
        showOtpInput(data.email_hint);          // show "Enter code sent to p***a@..."
    } else {
        showError(data.error);
    }
}
```

### Step 2 — User Enters OTP Code

```javascript
async function submitOtp() {
    const code = document.getElementById("otpInput").value.trim();

    const response = await fetch("/verify-otp/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify({ verification_id: verificationId, code }),
    });

    const data = await response.json();

    if (response.ok) {
        localStorage.setItem("chatToken", data.token);  // store for all future requests
        closeModal();
        enableChat();
    } else {
        showError(data.error);
    }
}
```

### Getting the CSRF Token

Django requires a CSRF token on all POST requests:

```javascript
function getCsrfToken() {
    // Django sets a csrftoken cookie on the page load (via @ensure_csrf_cookie)
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
}
```

---

## 6. Sending a Question and Receiving the SSE Stream

This is the core of the frontend. When the user clicks Send:

### Why `fetch()` Instead of `EventSource`?

`EventSource` is the browser's native SSE API — but it only supports GET requests. The chat endpoint is a POST (we need to send a JSON body with the question). So we use `fetch()` with `ReadableStream` instead.

### The Complete Streaming Flow

```javascript
async function sendMessage(question) {
    const token = localStorage.getItem("chatToken");

    // Show user's message in the chat
    appendMessage("user", question);

    // Create a placeholder for the streaming answer
    const assistantDiv = appendMessage("assistant", "");

    // Make the POST request
    const response = await fetch("/chat/", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
            "X-Chat-Token": token,
        },
        body: JSON.stringify({ question }),
    });

    if (!response.ok) {
        assistantDiv.textContent = "Error: Could not get a response.";
        return;
    }

    // Set up the stream reader
    const reader   = response.body.getReader();
    const decoder  = new TextDecoder("utf-8");
    let   buffer   = "";
    let   fullText = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // Decode binary chunk → string
        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE events (separated by double newlines)
        const events = buffer.split("\n\n");
        buffer = events.pop();  // last incomplete event stays in buffer

        for (const event of events) {
            if (!event.startsWith("data: ")) continue;

            const payload = event.slice(6);  // remove "data: " prefix

            if (payload === "[DONE]") {
                // Stream finished — do final render
                renderMarkdown(assistantDiv, fullText);
                return;
            }

            if (payload.startsWith("[ERROR:")) {
                assistantDiv.textContent = payload;
                return;
            }

            // Unescape newlines (server sends "\\n", we need actual "\n")
            const token = payload.replace(/\\n/g, "\n");
            fullText += token;

            // Live re-render with markdown as tokens arrive
            renderMarkdown(assistantDiv, fullText);
        }
    }
}
```

### Live Markdown Rendering

```javascript
function renderMarkdown(element, text) {
    // marked.js converts markdown → HTML
    const rawHtml = marked.parse(text);
    // DOMPurify removes any potentially dangerous HTML (XSS prevention)
    const safeHtml = DOMPurify.sanitize(rawHtml);
    element.innerHTML = safeHtml;
    // Auto-scroll to the bottom
    element.scrollIntoView({ behavior: "smooth", block: "end" });
}
```

This is called on every token, so the user sees the formatted markdown text appearing and updating in real time — bullet points, bold text, code blocks all render live.

**Why DOMPurify?** The LLM response may contain HTML tags. Without sanitisation, a malicious document could cause the LLM to output `<script>alert("XSS")</script>`, which would execute in the user's browser. DOMPurify removes all dangerous tags while keeping formatting.

---

## 7. Copy to Clipboard

Each message has a copy button:

```javascript
function copyMessage(button) {
    const messageDiv = button.closest(".message").querySelector(".message-content");
    const text = messageDiv.innerText;  // plain text (no HTML tags)

    navigator.clipboard.writeText(text).then(() => {
        button.innerHTML = '<i class="fas fa-check"></i>';  // show checkmark
        setTimeout(() => {
            button.innerHTML = '<i class="fas fa-copy"></i>';  // restore after 2s
        }, 2000);
    });
}
```

---

## 8. New Chat (Session Reset)

When the user clicks "New Chat":

```javascript
async function resetChat() {
    const token = localStorage.getItem("chatToken");

    // Tell the server (for logging purposes)
    if (token) {
        await fetch("/reset/", {
            method: "POST",
            headers: { "X-CSRFToken": getCsrfToken(), "X-Chat-Token": token },
        });
    }

    // Clear the stored session token
    localStorage.removeItem("chatToken");

    // Reload the page — this triggers the page load flow
    // and shows the info/OTP modal again
    window.location.reload();
}
```

---

## 9. The Embeddable Widget

The widget makes it possible to add DocChat to any existing website using a single `<iframe>` tag — no installation, no backend changes needed on the external site.

### How it Works

1. The external site embeds `<iframe src="https://your-server.com/widget/">` in its HTML
2. The browser loads the widget page from your DocChat server
3. The widget's API calls (`/chat/`, `/session-config/`, etc.) all go to the same origin (your DocChat server) — no CORS issues
4. The widget is completely self-contained

### Why `@xframe_options_exempt`

By default, Django adds an `X-Frame-Options: SAMEORIGIN` header to all responses. This prevents the page from being embedded in an `<iframe>` on a different domain — a security feature to prevent "clickjacking" attacks.

`widget_view` has `@xframe_options_exempt`:

```python
from django.views.decorators.clickjacking import xframe_options_exempt

@xframe_options_exempt
def widget_view(request):
    return render(request, "widget.html")
```

This removes the restriction for `/widget/` only, allowing external sites to embed it.

### Getting the Embed Code

1. Go to `http://127.0.0.1:8000/admin/`
2. Click **LLM Configurations** → click the one row
3. At the top of the form, click **"Get embed script"**

The page shows an `<iframe>` snippet like:

```html
<iframe
  src="https://your-server.com/widget/"
  width="400"
  height="600"
  style="border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);"
  title="DocChat Assistant"
></iframe>
```

Copy this and paste it into any HTML page on your external website.

### Recommended iframe Dimensions

For a sidebar/floating chat widget:
- Width: 380px–420px
- Height: 580px–650px

For a full-width embedded experience:
- Width: 100%
- Height: 700px

---

## 10. Customising the Appearance

### Change the Colour Theme

The warm beige/brown theme uses CSS custom properties (variables). Edit `static/css/style.css`:

```css
:root {
    --primary-color: #8B5E3C;      /* main brown */
    --secondary-color: #D4A96A;    /* warm gold */
    --background-color: #F5F0E8;   /* beige background */
    --surface-color: #FFFFFF;      /* card/message backgrounds */
    --text-color: #2C1810;         /* dark brown text */
}
```

Change these values to match your brand colours.

### Change the Bot Name

In `templates/index.html`, find and replace "DocChat" with your preferred name:

```html
<title>DocChat — Document Q&A Assistant</title>
<h1 class="header-title">DocChat</h1>
```

### Add a Logo

In `templates/index.html`, find the header area and replace the text heading with an image:

```html
<header class="app-header">
    <img src="{% static 'img/your-logo.png' %}" alt="Your Organization" height="40">
    <!-- or keep the text alongside the logo -->
    <span class="header-title">Your Organization Chat</span>
</header>
```

Place your logo file in `static/img/` and run `python manage.py collectstatic`.

---

## What to Do Next

Read [File 13 — Deployment](13_deployment.md) to learn how to move from the development server to a production setup with gunicorn, nginx, MySQL, and SSL.

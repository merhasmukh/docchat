# 03 — Django Project Structure

## What This File Covers

How Django organises code, and exactly what every file in DocChat does. After reading this, you will know where to find any piece of functionality and understand how a user's request travels from their browser to a response.

**Prerequisites:** File 02 (Environment Setup) — the project must be cloned and runnable.

---

## 1. What is Django? (30-Second Explanation)

Django is a Python web framework. Its job is to receive HTTP requests from browsers, figure out what code to run, run it, and send back a response.

The simplest possible flow:

```
Browser sends:  GET http://127.0.0.1:8000/
                        ↓
Django checks:  Which URL pattern matches "/"?
                        ↓
Django runs:    The view function registered for "/"
                        ↓
Browser receives: HTML page
```

You write view functions (Python functions that receive a request and return a response), register them with URL patterns, and Django handles the rest — parsing requests, managing sessions, serving static files, etc.

---

## 2. Project vs App

Django distinguishes between a **project** and an **app**:

- A **project** is the entire website. It holds global settings, the root URL configuration, and glue code.
- An **app** is a self-contained module of functionality. A project can have many apps (e.g., `users`, `blog`, `shop`).

DocChat has one project and one app:

```
docchat/          ← the project (global config)
    settings.py
    urls.py
    wsgi.py

chat/             ← the app (all business logic)
    models.py
    views.py
    urls.py
    pipeline.py
    admin.py
    providers/
    agent/
```

The analogy: the project is the restaurant building, the app is the kitchen. Django is the building's infrastructure (plumbing, electricity), and your app is where the cooking happens.

---

## 3. Complete Directory Map

Here is every file and folder in the project, with a one-line description:

```
docchat/                        ← Django project config
├── settings.py                 ← All global settings (DB, email, logging, paths)
├── urls.py                     ← Root URL router (includes chat.urls + admin)
├── wsgi.py                     ← WSGI entry point (used by gunicorn in production)
└── __init__.py

chat/                           ← The main Django app
├── models.py                   ← Database table definitions (Document, Session, etc.)
├── views.py                    ← URL handler functions (chat, OTP, status, etc.)
├── urls.py                     ← URL patterns for the chat app
├── pipeline.py                 ← OCR, RAG retrieval, LLM dispatch
├── admin.py                    ← Admin panel registration and customisation
├── apps.py                     ← App config (name, signal registration)
├── schema_hooks.py             ← OpenAPI schema customisation for /chat/ SSE endpoint
├── migrations/                 ← Auto-generated DB migration files (do not edit manually)
│   ├── 0001_initial.py
│   └── ...
├── providers/                  ← One file per LLM provider
│   ├── gemini.py               ← Google Gemini streaming + context caching
│   ├── ollama.py               ← Ollama (local) streaming
│   ├── sarvam.py               ← Sarvam AI streaming
│   ├── utils.py                ← Shared prompts, conversational detection, rules
│   └── __init__.py
└── agent/                      ← ReAct agent loop (advanced feature)
    ├── loop.py                 ← Orchestrates multi-step reasoning
    ├── tools.py                ← Tool definitions (search, get_page, list_sections)
    ├── memory.py               ← Cross-session user memory (load + save)
    └── __init__.py

templates/                      ← HTML templates
├── index.html                  ← Main chat UI (served at /)
├── widget.html                 ← Embeddable widget (served at /widget/)
├── emails/
│   └── verification_code.html ← OTP email template
└── admin/
    └── widget_script.html      ← Admin page showing embed <iframe> code

static/                         ← Source static files (CSS, JS)
├── css/
│   ├── style.css               ← Custom theme (warm beige/brown)
│   └── widget.css              ← Widget-specific styles
└── js/
    ├── main.js                 ← Main frontend logic (upload, chat, OTP flow)
    ├── widget.js               ← Widget JS
    └── widget_chat.js          ← Widget chat interactions

staticfiles/                    ← Output of `collectstatic` (served by nginx in prod)

uploads/                        ← Temporary upload staging (auto-deleted after OCR)
markdown_cache/                 ← Processed markdown + page JSON per document
qdrant_storage/                 ← Qdrant vector database files

requirements.txt                ← Python package dependencies
manage.py                       ← Django CLI entry point
.env                            ← Secrets (never commit to git)
.gitignore                      ← Files excluded from git
db.sqlite3                      ← SQLite database (development)
app.log                         ← Application log (rotated at 10 MB)
```

---

## 4. `dochat/settings.py` — Deep Dive

This is the most important configuration file. Let us walk through the key settings:

### Basic Settings

```python
DEBUG = True
```
When `True`, Django shows detailed error pages in the browser if something crashes. Set to `False` in production — never expose debug pages to the public.

```python
ALLOWED_HOSTS = ["*"]
```
Which domain names Django will accept requests for. `"*"` means any domain — fine for development, but lock this down in production (e.g., `["docchat.example.com"]`).

```python
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
```
A random string Django uses to sign cookies and tokens. Keep it secret. The `.env` file supplies this.

### Installed Apps

```python
INSTALLED_APPS = [
    "django.contrib.admin",      # The /admin/ panel
    "django.contrib.contenttypes",
    "django.contrib.auth",       # User authentication
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles", # CSS/JS serving
    "rest_framework",            # DRF for API views
    "drf_spectacular",           # Auto OpenAPI docs
    "chat",                      # Our app
]
```

### Database Configuration

DocChat reads `DB_ENGINE` from `.env` and switches database backends automatically:

```python
_DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").lower()

if _DB_ENGINE == "mysql":
    DATABASES = { "default": { "ENGINE": "django.db.backends.mysql", ... } }
elif _DB_ENGINE == "postgres":
    DATABASES = { "default": { "ENGINE": "django.db.backends.postgresql", ... } }
else:
    # Default: SQLite — no extra config needed
    DATABASES = { "default": { "ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3" } }
```

For development, leave `DB_ENGINE` out of `.env` (or set it to `sqlite`). For production, set `DB_ENGINE=mysql`.

### Session Settings

```python
SESSION_COOKIE_AGE = 86400  # 24 hours
```
Django admin sessions last 24 hours. The chat token system (for end users) is separate and stored in the browser's `localStorage` — not Django sessions.

### Upload Size Limit

```python
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024   # 50 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024   # 50 MB
```
Users can upload documents up to 50 MB.

### Custom App Settings

These are project-specific settings that DocChat adds to Django's standard ones:

```python
UPLOAD_FOLDER = BASE_DIR / "uploads"
```
Temporary folder where uploaded files are saved before OCR. Files are deleted immediately after text extraction.

```python
MARKDOWN_FOLDER = BASE_DIR / "markdown_cache"
```
Where the extracted markdown and page JSON are stored permanently (until the admin deletes the document).

```python
QDRANT_PATH = BASE_DIR / "qdrant_storage"
```
Where Qdrant stores its vector database files on disk.

```python
CONTEXT_CHAR_THRESHOLD = 12_000   # ~3K tokens
```
This is the core decision point for RAG vs full-context mode. If a document's extracted text is fewer than 12,000 characters, DocChat sends the whole document to the LLM on every question (full-context mode). If it is larger, DocChat only retrieves and sends the most relevant pages (RAG mode). See File 06 for details.

### Static Files

```python
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"   # output dir for collectstatic
STATICFILES_DIRS = [BASE_DIR / "static"]
```
- `static/` — where you put your CSS and JS source files
- `staticfiles/` — where Django collects everything for production serving (run `python manage.py collectstatic`)
- `/static/` — the URL prefix browsers use to access static files

### Email (Gmail SMTP)

```python
EMAIL_BACKEND       = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST          = "smtp.gmail.com"
EMAIL_PORT          = 587
EMAIL_USE_TLS       = True
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
```

All email settings come from `.env`. See File 09 for how to configure Gmail App Passwords.

### Logging

DocChat writes structured logs to `app.log` and the console:

```python
LOGGING = {
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "app.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB per file
            "backupCount": 5,               # keep 5 old files
        },
    },
    "loggers": {
        "chat": { "level": "DEBUG" },       # all chat app logs
    },
}
```

When you run `python manage.py runserver`, log lines appear in the terminal. They also go to `app.log`. This is invaluable for debugging OCR, RAG, and LLM issues.

### How `.env` is Loaded

At the very top of `settings.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

`load_dotenv()` reads `.env` and sets each key-value pair as an environment variable *before* the rest of `settings.py` runs. This is why `os.environ.get("GEMINI_API_KEY")` works.

---

## 5. `dochat/urls.py` — Root URL Configuration

This file is the front door for all incoming requests:

```python
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path("admin/", admin.site.urls),          # Django admin panel
    path("api/schema/", SpectacularAPIView.as_view()),
    path("api/docs/", SpectacularSwaggerView.as_view()),   # Swagger UI
    path("api/redoc/", SpectacularRedocView.as_view()),    # ReDoc UI
    path("", include("chat.urls")),           # all chat app URLs
]
```

`include("chat.urls")` tells Django: "for any URL that does not match the patterns above, look in `chat/urls.py`."

---

## 6. `chat/urls.py` — App URL Patterns

Every URL the chat app handles:

```python
urlpatterns = [
    path("",                  views.index_view,          name="index"),
    path("widget/",           views.widget_view,         name="widget"),
    path("status/",           views.status_view,         name="status"),
    path("history/",          views.history_view,        name="history"),
    path("session-config/",   views.session_config_view, name="session-config"),
    path("start-session/",    views.start_session_view,  name="start-session"),
    path("request-otp/",      views.request_otp_view,   name="request-otp"),
    path("verify-otp/",       views.verify_otp_view,    name="verify-otp"),
    path("resend-otp/",       views.resend_otp_view,    name="resend-otp"),
    path("chat/",             views.chat_view,          name="chat"),
    path("reset/",            views.reset_view,         name="reset"),
]
```

Each `path()` maps a URL string to a view function. For example, when a browser sends `POST http://127.0.0.1:8000/chat/`, Django finds the `chat/` pattern and calls `views.chat_view`.

---

## 7. URL Routing — Traced End-to-End Example

Let us trace exactly what happens when a user asks a question:

```
1. Browser sends:
   POST http://127.0.0.1:8000/chat/
   Headers: { "X-Chat-Token": "abc123", "Content-Type": "application/json" }
   Body: { "question": "What are the fees?" }

2. Django middleware runs:
   - SecurityMiddleware (HTTPS checks)
   - SessionMiddleware (loads admin session if any)
   - CsrfViewMiddleware (validates CSRF token)

3. Django checks dochat/urls.py:
   - "admin/" → no match
   - "api/schema/" → no match
   - "" → include("chat.urls") → check chat/urls.py

4. Django checks chat/urls.py:
   - "" → no match (this is not empty — it's POST /chat/)
   - "chat/" → MATCH → call views.chat_view

5. views.chat_view runs:
   - Validates session token (X-Chat-Token header)
   - Loads active document
   - Calls pipeline.ask_streaming()
   - Returns StreamingHttpResponse with SSE stream

6. Browser receives:
   data: The\n\n
   data:  fees\n\n
   data:  are\n\n
   ...
   data: [DONE]\n\n
```

---

## 8. Key File Roles (Quick Reference)

| File | What It Does | When You Edit It |
|------|-------------|-----------------|
| `dochat/settings.py` | Global config | Adding new settings, changing DB, email |
| `dochat/urls.py` | Root URL router | Rarely — only to add new apps |
| `chat/urls.py` | App URL patterns | Adding new endpoints |
| `chat/models.py` | Database tables | Adding/changing data you store |
| `chat/views.py` | Request handlers | Adding/changing API endpoints |
| `chat/pipeline.py` | OCR + LLM logic | Changing how documents are processed or how LLMs are called |
| `chat/admin.py` | Admin panel UI | Changing what appears in the admin |
| `chat/providers/*.py` | LLM providers | Adding/changing LLM integrations |
| `chat/agent/loop.py` | ReAct agent | Changing agent behaviour |
| `templates/index.html` | Chat UI HTML | Changing the page layout |
| `static/js/main.js` | Frontend JS | Changing UI behaviour |
| `static/css/style.css` | Theme/styles | Changing visual appearance |

---

## What to Do Next

Read [File 04 — Database Models](04_database_models.md) to understand how DocChat stores documents, sessions, messages, and configuration in the database.

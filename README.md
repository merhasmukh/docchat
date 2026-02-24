# DocChat

A Django-based document Q&A application. Upload a PDF or image, ask questions about it in natural language, and get streaming answers from a local Ollama model or Google Gemini. Supports Gujarati + English documents with multilingual OCR and retrieval.

---

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Setup & Installation](#setup--installation)
5. [Configuration](#configuration)
6. [Admin Panel](#admin-panel)
7. [How It Works](#how-it-works)
   - [Upload Pipeline (OCR)](#upload-pipeline-ocr)
   - [Context Modes (Full vs RAG)](#context-modes-full-vs-rag)
   - [LLM Streaming](#llm-streaming)
   - [Gemini Context Caching](#gemini-context-caching)
   - [Cost Tracking](#cost-tracking)
8. [API Endpoints](#api-endpoints)
9. [Database Models](#database-models)
10. [Logging](#logging)
11. [Development Notes](#development-notes)

---

## Features

- **Document upload** — PDF, PNG, JPG, TIFF, BMP, WEBP (up to 50 MB)
- **Streaming chat** — responses stream token-by-token via Server-Sent Events
- **Multi-turn conversations** — full chat history preserved per session
- **Dual LLM support** — Ollama (local/offline) and Google Gemini (API)
- **Three OCR engines** — Docling, Tesseract (Gujarati + English), Gemini Vision
- **Smart context routing** — full context for small docs, BM25/embedding RAG for large docs
- **Multilingual RAG** — sentence-transformers or Gemini embeddings for cross-language retrieval (Gujarati question → English doc, or vice versa)
- **Gemini context caching** — documents cached server-side, ~4× cheaper on repeat queries
- **Cost tracking** — per-message and per-session token counts + INR cost in Django admin
- **Structured logging** — all operations logged to `app.log` with timing data

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5, Django REST Framework |
| LLM (local) | Ollama (`llama3.2-vision` or any model) |
| LLM (cloud) | Google Gemini (`gemini-2.0-flash`) |
| OCR | Docling / Tesseract / Gemini Vision |
| RAG retrieval | BM25 (`rank_bm25`) / sentence-transformers / Gemini embeddings |
| PDF rendering | `pdf2image` (Poppler) |
| Frontend | Bootstrap 5, Font Awesome 6, `marked.js`, `DOMPurify` |
| Database | SQLite (Django sessions + cost tracking) |

---

## Project Structure

```
docchat/
├── chat/
│   ├── admin.py          # Django admin registrations
│   ├── models.py         # LLMConfig, ModelPricing, ChatSession, ChatMessage
│   ├── pipeline.py       # OCR, LLM, Gemini cache, RAG/embedding logic
│   ├── urls.py           # URL patterns
│   └── views.py          # Upload, chat (SSE), status, reset views
├── dochat/
│   ├── settings.py       # Django settings + CONTEXT_CHAR_THRESHOLD
│   └── urls.py           # Root URL conf (includes admin)
├── static/
│   ├── css/style.css     # Warm beige/brown theme
│   └── js/main.js        # Upload, streaming chat, copy-to-clipboard
├── templates/
│   └── index.html        # Single-page UI
├── uploads/              # Temporary upload staging (files deleted after OCR)
├── markdown_cache/       # Processed markdown + JSON + RAG chunks (per session)
├── app.log               # Rotating application log
├── requirements.txt
└── .env                  # GEMINI_API_KEY, SECRET_KEY
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running (for local LLM)
- [Poppler](https://poppler.freedesktop.org) installed (for PDF → image conversion)
- Tesseract OCR with Gujarati pack (optional, for Tesseract OCR engine)

```bash
# macOS
brew install poppler tesseract tesseract-lang

# Ubuntu / Debian
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-guj
```

### Install

```bash
# 1. Clone the repo
git clone <repo-url>
cd docchat

# 2. Activate your virtual environment
source ../../Projects/tetsvisionmodel/venv/bin/activate
# or create a new one:
python -m venv venv && source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file
cp .env.example .env   # or create manually — see Configuration section

# 5. Run migrations
python manage.py migrate

# 6. Create a Django superuser (for admin access)
python manage.py createsuperuser

# 7. Start the development server
python manage.py runserver
```

The app is available at `http://127.0.0.1:8000/` and the admin at `http://127.0.0.1:8000/admin/`.

### Pull an Ollama model

```bash
ollama pull llama3.2-vision
```

---

## Configuration

### `.env` file

```ini
SECRET_KEY=your-django-secret-key
GEMINI_API_KEY=your-google-gemini-api-key   # required only when using Gemini provider
```

### `dochat/settings.py` — key settings

| Setting | Default | Description |
|---------|---------|-------------|
| `CONTEXT_CHAR_THRESHOLD` | `100_000` | Docs below this char count use full-context mode; larger docs use RAG |
| `UPLOAD_FOLDER` | `uploads/` | Temporary storage for uploaded files (deleted after OCR) |
| `MARKDOWN_FOLDER` | `markdown_cache/` | Processed markdown, JSON metadata, RAG chunk files |
| `SESSION_COOKIE_AGE` | `86400` | Session lifetime in seconds (24 hours) |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `50 MB` | Maximum upload file size |

---

## Admin Panel

Go to `http://127.0.0.1:8000/admin/` and log in with your superuser credentials.

### LLM Configuration *(singleton — one row only)*

| Field | Options | Description |
|-------|---------|-------------|
| **Provider** | `ollama` / `gemini` | Which LLM to use for answering questions |
| **Ollama model** | e.g. `llama3.2-vision` | Model name as listed by `ollama list` |
| **Gemini model** | e.g. `gemini-2.0-flash` | Gemini model ID |
| **OCR engine** | `docling` / `tesseract` / `gemini_vision` | Engine for extracting text from uploaded docs |
| **RAG embedding** | `bm25` / `multilingual_local` / `gemini_embedding` | Retrieval method used when docs exceed the context threshold |

> **Note:** Changes to LLM Configuration take effect on the next document upload (context mode + embeddings are built at upload time) or next question (provider/model is read per-request).

### Model Pricing

Add one row per model to enable cost tracking. Fields:

| Field | Example | Description |
|-------|---------|-------------|
| **Provider** | `gemini` | Must match the LLM Configuration provider |
| **Model name** | `gemini-2.0-flash` | Must exactly match the model ID in LLM Configuration |
| **Input price per million** | `1.0000` | INR per 1 million input tokens |
| **Output price per million** | `3.0000` | INR per 1 million output tokens |
| **Is active** | ✓ | Uncheck to pause cost tracking for this model |

### Chat Sessions & Messages

Read-only views showing:
- **Chat Sessions** — one row per Django session with cumulative totals (tokens, cost, message count)
- **Chat Messages** — every individual Q&A exchange with input/output/total tokens, cost breakdown, response time, and whether tokens were estimated (Ollama fallback)

---

## How It Works

### Upload Pipeline (OCR)

```
User uploads file
  → Saved to uploads/ with a UUID name
  → convert_to_markdown() in pipeline.py:
      ├── PDF: rendered to PNG images via pdf2image (200 DPI)
      └── Image: used directly

      Per page → OCR engine:
        ├── docling      → Docling DocumentConverter (default, markdown-aware)
        ├── tesseract    → pytesseract, lang="guj+eng" (Gujarati + English)
        └── gemini_vision → Gemini Vision API (cloud, any language)

      Output: combined markdown + pages_data dict
  → Upload temp file deleted
  → Markdown + JSON metadata saved to markdown_cache/
  → Context mode determined (full vs RAG)
  → Gemini cache created OR RAG chunks built + embedded
  → Session updated with all paths/references
```

### Context Modes (Full vs RAG)

The char count of the extracted markdown determines which mode is used.

```
doc_chars < CONTEXT_CHAR_THRESHOLD (100K chars, ~25K tokens)
  → FULL CONTEXT MODE
      All document text is sent to the LLM on every question.
      For Gemini: a context cache is created on upload.
      For Ollama: markdown sent directly in the system prompt.

doc_chars ≥ CONTEXT_CHAR_THRESHOLD
  → RAG MODE
      Document split into page-level chunks.
      Each chunk embedded on upload (method from LLM Configuration).
      On each question: top-5 most relevant chunks retrieved and sent.
```

#### RAG Embedding Methods

| Method | Cross-language? | Notes |
|--------|----------------|-------|
| `bm25` | ❌ Same language only | No embeddings, keyword overlap. Fast, zero cost. |
| `multilingual_local` | ✅ Yes | `paraphrase-multilingual-MiniLM-L12-v2` via `sentence-transformers`. ~400 MB download, fully offline. |
| `gemini_embedding` | ✅ Yes | `text-multilingual-embedding-002` via Gemini API. No local model needed. |

> For Gujarati documents with English questions (or vice versa), use `multilingual_local` or `gemini_embedding`. BM25 scores will all be 0 across scripts.

### LLM Streaming

```
Question received
  → Context resolved (full markdown or RAG-retrieved chunks)
  → ask_streaming() dispatches to provider:
      ├── Gemini: generate_content_stream()
      │     ├── with cache_name → uses cached doc context (cheap)
      │     └── without cache   → sends full context in system_instruction
      └── Ollama: ollama.chat(stream=True)
  → Tokens yielded one-by-one as SSE events ("data: token\n\n")
  → Frontend accumulates tokens, re-renders markdown live
  → [DONE] event sent after stream completes
  → usage_out dict populated with token counts (try/finally)
  → Session history + cost records saved
```

### Gemini Context Caching

When a document is uploaded and the provider is Gemini:

1. A cache is created via `client.caches.create()` containing the document as the system instruction
2. Cache TTL is 1 hour (refreshed on re-upload)
3. Each subsequent chat call passes `cached_content=cache_name` instead of the full document text
4. Cached input tokens are billed at ~4× lower rate than regular input tokens
5. On reset or re-upload, the old cache is explicitly deleted

If cache creation fails (e.g. doc too short, API error), the app falls back to full-context mode silently.

### Cost Tracking

After each streaming response completes:

```
Token counts from stream:
  Gemini  → usage_metadata.prompt_token_count / candidates_token_count
  Ollama  → prompt_eval_count / eval_count from final chunk
            (falls back to chars ÷ 4 estimation if both are 0)

Cost calculation:
  input_cost  = input_tokens  × pricing.input_price_per_million  / 1_000_000
  output_cost = output_tokens × pricing.output_price_per_million / 1_000_000
  (cost = 0 if no ModelPricing row exists for this provider+model)

Records saved:
  ChatMessage  → one row per Q&A with full token/cost/timing breakdown
  ChatSession  → running totals updated atomically via F() expressions
```

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Main chat UI |
| `GET` | `/status/` | Check if a document is loaded in the current session |
| `POST` | `/upload/` | Upload a document (multipart/form-data, field: `file`) |
| `POST` | `/chat/` | Send a question, receive SSE token stream |
| `POST` | `/reset/` | Clear session, delete files, remove Gemini cache |
| `GET/POST` | `/admin/` | Django admin panel |

### `/upload/` response

```json
{
  "status": "ok",
  "filename": "document.pdf",
  "total_pages": 13,
  "message": "Document processed. You can now ask questions.",
  "char_count": 42301,
  "context_mode": "full"
}
```

### `/chat/` request

```json
{ "question": "What is the total amount on invoice #5?" }
```

### `/chat/` SSE stream format

```
data: Here\n\n
data: is\n\n
data: the\n\n
data: answer.\n\n
data: [DONE]\n\n
```

Newlines within tokens are escaped as `\n` (literal backslash-n) and unescaped by the frontend.
Error events: `data: [ERROR: <message>]\n\n`

---

## Database Models

### `LLMConfig` — singleton configuration

One row (`pk=1`). All LLM and OCR settings are stored here.

### `ModelPricing`

Admin-managed pricing table. `unique_together = (provider, model_name)`.

### `ChatSession`

One row per Django session. Updated atomically after each message.

| Field | Type | Description |
|-------|------|-------------|
| `session_key` | CharField | Django session key (40 chars) |
| `document_name` | CharField | Last uploaded filename |
| `message_count` | IntegerField | Total Q&A turns |
| `total_input_tokens` | BigIntegerField | Cumulative input tokens |
| `total_output_tokens` | BigIntegerField | Cumulative output tokens |
| `total_cost` | DecimalField(14,6) | Total cost in INR |

### `ChatMessage`

One row per Q&A exchange.

| Field | Type | Description |
|-------|------|-------------|
| `session` | FK → ChatSession | Parent session |
| `provider` | CharField | `ollama` or `gemini` |
| `model_name` | CharField | Exact model ID used |
| `question` | TextField | User's question |
| `answer` | TextField | Full LLM response |
| `input_tokens` | IntegerField | Input token count |
| `output_tokens` | IntegerField | Output token count |
| `tokens_estimated` | BooleanField | `True` if Ollama returned 0 and chars÷4 was used |
| `input_cost` | DecimalField(14,6) | Input cost in INR |
| `output_cost` | DecimalField(14,6) | Output cost in INR |
| `total_cost` | DecimalField(14,6) | Total cost in INR |
| `response_time_seconds` | FloatField | Wall-clock time for LLM response |

---

## Logging

All application events are logged to `app.log` (rotating, 10 MB × 5 files) and stdout.

Log format:
```
2025-02-24 14:32:01 [INFO] chat.views: Upload received | file=invoice.pdf | size=524288 bytes | ext=.pdf
2025-02-24 14:32:08 [INFO] chat.pipeline: OCR complete | engine=tesseract | pages=13 | total_chars=42301 | total_time=6.84s
2025-02-24 14:32:09 [INFO] chat.pipeline: Gemini cache created | name=cachedContents/abc123 | model=gemini-2.0-flash
2025-02-24 14:32:09 [INFO] chat.views: Upload complete | file=invoice.pdf | session_id=abc...xyz | mode=full | cache=yes
2025-02-24 14:32:15 [INFO] chat.pipeline: LLM stream start | provider=gemini | model=gemini-2.0-flash | history_turns=0 | q_chars=28 | cached=True
2025-02-24 14:32:17 [INFO] chat.pipeline: LLM stream done  | provider=gemini | model=gemini-2.0-flash | response_chars=312 | time=2.14s | cached=True
2025-02-24 14:32:17 [INFO] chat.views: Cost | session=abc...xyz | provider=gemini | model=gemini-2.0-flash | in=10482 out=89 est=False | cost=₹0.000357
```

Key log events:
- `Upload received` → file accepted, size and extension
- `OCR complete` → engine, pages, chars, timing
- `Gemini cache created/deleted` → cache lifecycle
- `Context mode` → `full` or `rag`, char count vs threshold
- `BM25 chunks built` / `Local embeddings built` → RAG index creation
- `RAG retrieval` → which pages were selected and why
- `LLM stream start/done` → provider, model, cached flag, timing
- `Cost` → token counts, estimated flag, INR cost per message

---

## Development Notes

### Running the server

```bash
source ../../Projects/tetsvisionmodel/venv/bin/activate
python manage.py runserver
```

### Applying migrations after model changes

```bash
python manage.py makemigrations chat
python manage.py migrate
```

### Sentence-transformers model download

The `multilingual_local` embedding mode downloads `paraphrase-multilingual-MiniLM-L12-v2` (~400 MB) on first use. It is cached in `~/.cache/huggingface/hub/` and reused on subsequent runs.

### Session files lifecycle

| File | Created | Deleted |
|------|---------|---------|
| `markdown_cache/<uuid>.md` | On upload | On reset or re-upload |
| `markdown_cache/<uuid>.json` | On upload | On reset or re-upload |
| `markdown_cache/<uuid>_chunks.json` | On upload (RAG mode) | On reset or re-upload |
| `uploads/<uuid>.<ext>` | On upload | Immediately after OCR |

Stale files older than 24 hours in `markdown_cache/` are deleted on server startup.

### Gemini context cache lifetime

Gemini caches have a 1-hour TTL. If a session is idle for more than 1 hour, the next chat request will fail with a cache-not-found error (logged as a warning) and the error will surface to the user. The workaround is to re-upload the document (which creates a fresh cache). Future improvement: auto-recreate the cache on expiry.

### Adding a new LLM provider

1. Add the provider to `LLMConfig.PROVIDER_CHOICES` in `models.py`
2. Add the model field and streaming function in `pipeline.py`
3. Update the dispatch in `ask_streaming()` and `ask()`
4. Add the model field to `LLMConfigAdmin.list_display` in `admin.py`
5. Run `makemigrations` + `migrate`

# DocChat

A Django-based document Q&A application. Upload a PDF or image, ask questions about it in any language (English, Gujarati, Hindi), and get streaming answers from a local Ollama model, Google Gemini, or Sarvam AI. Features multilingual OCR, smart context routing, Gemini context caching, cost tracking, and email OTP verification.

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
   - [Email OTP Verification](#email-otp-verification)
8. [API Endpoints](#api-endpoints)
9. [Database Models](#database-models)
10. [Logging](#logging)
11. [Development Notes](#development-notes)

---

## Features

- **Document upload** — PDF, PNG, JPG, TIFF, BMP, WEBP (up to 50 MB)
- **Streaming chat** — responses stream token-by-token via Server-Sent Events
- **Multi-turn conversations** — full chat history preserved per session
- **Three LLM providers** — Ollama (local/offline), Google Gemini (API), and Sarvam AI
- **Four OCR engines** — Auto, Docling, Tesseract (Hindi + Gujarati + English), Gemini Vision
- **Smart OCR routing** — Auto engine detects digital PDFs (→ Docling) vs scanned (→ Tesseract) automatically
- **Smart context routing** — full context for small docs (~3K tokens), BM25/embedding RAG for large docs
- **Multilingual Q&A** — Hindi/Hinglish questions answered from Gujarati/English documents and vice versa
- **Multilingual RAG** — sentence-transformers or Gemini embeddings for cross-language retrieval
- **Gemini context caching** — documents cached server-side, ~4× cheaper on repeat queries; auto-recaches on model change
- **Cost tracking** — per-message and per-session token counts + INR cost in Django admin
- **Email OTP verification** — users verify their email before chatting
- **Single active document** — admin enforces exactly one document active at a time
- **Structured logging** — all operations logged to `app.log` with timing data

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5, Django REST Framework |
| LLM (local) | Ollama (`llama3.2-vision` or any model) |
| LLM (cloud) | Google Gemini (`gemini-2.5-flash` etc.) |
| LLM (Indic) | Sarvam AI (`sarvam-m`) |
| OCR | Docling / Tesseract / Gemini Vision / Auto |
| RAG retrieval | BM25 (`rank_bm25`) / sentence-transformers / Gemini embeddings |
| PDF rendering | `pdf2image` (Poppler) |
| PDF text detection | `pdfplumber` |
| Frontend | Bootstrap 5, Font Awesome 6, `marked.js`, `DOMPurify` |
| Database | SQLite (Django sessions + cost tracking) |

---

## Project Structure

```
docchat/
├── chat/
│   ├── admin.py          # Django admin — document management, cost views
│   ├── models.py         # LLMConfig, ModelPricing, ChatSession, ChatMessage, Document, EmailVerification
│   ├── pipeline.py       # OCR engines, Auto routing, RAG/embedding, LLM dispatch
│   ├── providers/
│   │   ├── utils.py      # Shared prompts (DOCUMENT_SYSTEM_PROMPT, DOCUMENT_SYSTEM_INSTRUCTION),
│   │   │                 #   conversational detection, citation scrubber
│   │   ├── gemini.py     # Gemini streaming, context caching, cache expiry/recache handling
│   │   ├── ollama.py     # Ollama streaming
│   │   └── sarvam.py     # Sarvam AI streaming
│   ├── urls.py           # URL patterns
│   └── views.py          # Upload, chat (SSE), status, reset, OTP views
├── dochat/
│   ├── settings.py       # Django settings + CONTEXT_CHAR_THRESHOLD
│   └── urls.py           # Root URL conf (includes admin)
├── static/
│   ├── css/style.css     # Warm beige/brown theme
│   └── js/main.js        # Upload, streaming chat, copy-to-clipboard
├── templates/
│   └── index.html        # Single-page UI
├── uploads/              # Temporary upload staging (files deleted after OCR)
├── markdown_cache/       # Processed markdown + JSON + RAG chunks (per document)
├── app.log               # Rotating application log
├── requirements.txt
└── .env                  # GEMINI_API_KEY, SARVAM_API_KEY, SECRET_KEY
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running (for local LLM)
- [Poppler](https://poppler.freedesktop.org) installed (for PDF → image conversion)
- Tesseract OCR with Hindi + Gujarati language packs

```bash
# macOS
brew install poppler tesseract tesseract-lang

# Ubuntu / Debian
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-hin tesseract-ocr-guj
```

### Install

```bash
# 1. Clone the repo
git clone <repo-url>
cd docchat

# 2. Create and activate a virtual environment
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
GEMINI_API_KEY=your-google-gemini-api-key     # required for Gemini provider or Gemini Vision OCR
SARVAM_API_KEY=your-sarvam-api-key            # required for Sarvam AI provider
```

### `dochat/settings.py` — key settings

| Setting | Default | Description |
|---------|---------|-------------|
| `CONTEXT_CHAR_THRESHOLD` | `12_000` (~3K tokens) | Docs below this char count use full-context mode; larger docs use RAG |
| `UPLOAD_FOLDER` | `uploads/` | Temporary storage for uploaded files (deleted after OCR) |
| `MARKDOWN_FOLDER` | `markdown_cache/` | Processed markdown, JSON metadata, RAG chunk files |
| `SESSION_COOKIE_AGE` | `86400` | Session lifetime in seconds (24 hours) |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `50 MB` | Maximum upload file size |

---

## Admin Panel

Go to `http://127.0.0.1:8000/admin/` and log in with your superuser credentials.

### Documents

The admin manages a library of processed documents. Key behaviours:

- **Only one document can be active at a time.** Activating a document via the "Set as active document" action, or checking `is_active` in the edit form, automatically deactivates all others.
- **Context mode** (`full` or `rag`) is determined at upload time based on the document's character count vs `CONTEXT_CHAR_THRESHOLD`.
- **Gemini cache name** is stored per-document and reused across chat sessions.

### LLM Configuration *(singleton — one row only)*

| Field | Options | Description |
|-------|---------|-------------|
| **Provider** | `ollama` / `gemini` / `sarvam` | Which LLM to use for answering questions |
| **Ollama model** | e.g. `llama3.2-vision` | Model name as listed by `ollama list` |
| **Gemini model** | e.g. `gemini-2.5-flash` | Gemini model ID |
| **Sarvam model** | e.g. `sarvam-m` | Sarvam AI model ID |
| **OCR engine** | `auto` / `docling` / `tesseract` / `gemini_vision` | Engine for extracting text from uploaded docs |
| **RAG embedding** | `bm25` / `multilingual_local` / `gemini_embedding` | Retrieval method used when docs exceed the context threshold |

> **Tip:** Use the **Auto** OCR engine — it automatically chooses Docling for digital PDFs (with a real text layer) and Tesseract for scanned PDFs and images, giving you the best quality for both types without manual switching.

> **Note:** Changing the Gemini model after a document has been cached will cause the next chat request to automatically delete the old cache and create a new one with the current model. This is transparent to the user.

### Model Pricing

Add one row per model to enable cost tracking. Fields:

| Field | Example | Description |
|-------|---------|-------------|
| **Provider** | `gemini` | Must match the LLM Configuration provider |
| **Model name** | `gemini-2.5-flash` | Must exactly match the model ID in LLM Configuration |
| **Input price per million** | `1.0000` | INR per 1 million input tokens |
| **Output price per million** | `3.0000` | INR per 1 million output tokens |
| **Is active** | ✓ | Uncheck to pause cost tracking for this model |

### Chat Sessions & Messages

Read-only views showing:
- **Chat Sessions** — one row per session with cumulative totals (tokens, cost, message count)
- **Chat Messages** — every individual Q&A exchange with input/output/total tokens, cost breakdown, response time, and whether tokens were estimated (Ollama fallback)

---

## How It Works

### Upload Pipeline (OCR)

```
Admin uploads file
  → Saved to uploads/ with a UUID name
  → convert_to_markdown() in pipeline.py:

      ── Engine resolution ──────────────────────────────
      If engine = "auto":
        PDF with text layer? (pdfplumber check) → Docling
        Scanned PDF / image?                    → Tesseract
      Else: use configured engine directly.

      ── DPI ─────────────────────────────────────────────
      Tesseract: 300 DPI  (Devanagari/Gujarati need clarity)
      Docling / Gemini Vision: 200 DPI

      ── Per page → OCR engine ──────────────────────────
      docling      → Docling DocumentConverter (markdown-aware, tables)
      tesseract    → pytesseract, lang="hin+guj+eng", --oem 3 --psm 6
                     + grayscale + contrast preprocessing
      gemini_vision → Gemini Vision API (Hindi, Gujarati, English, mixed)

      Output: combined markdown + pages_data dict
  → Upload temp file deleted
  → Markdown + JSON metadata saved to markdown_cache/
  → Context mode determined: full (<12K chars) or rag (≥12K chars)
  → RAG chunks built + embedded (always, as fallback)
  → Gemini cache created if provider=gemini and context_mode=full
  → Document record saved to DB
```

### Context Modes (Full vs RAG)

The character count of the extracted markdown determines which mode is used.

```
doc_chars < CONTEXT_CHAR_THRESHOLD (12K chars, ~3K tokens)
  → FULL CONTEXT MODE
      All document text is sent to the LLM on every question.
      For Gemini: a context cache is created on upload.
      For Ollama/Sarvam: markdown sent directly in the system prompt.

doc_chars ≥ CONTEXT_CHAR_THRESHOLD
  → RAG MODE
      Document split into page-level chunks.
      Each chunk embedded on upload (method from LLM Configuration).
      On each question: top-3 most relevant chunks retrieved and sent.
      Gemini context caching is NOT used in RAG mode (chunks change per question).
```

#### RAG Embedding Methods

| Method | Cross-language? | Notes |
|--------|----------------|-------|
| `bm25` | ❌ Same language only | No embeddings, keyword overlap. Fast, zero cost. |
| `multilingual_local` | ✅ Yes | `paraphrase-multilingual-MiniLM-L12-v2` via `sentence-transformers`. ~400 MB download, fully offline. |
| `gemini_embedding` | ✅ Yes | `text-multilingual-embedding-002` via Gemini API. Best cross-lingual accuracy. |

> For Hindi/Gujarati documents with English questions (or vice versa), use `multilingual_local` or `gemini_embedding`. BM25 scores will all be 0 across scripts.

### Multilingual Q&A

All LLM providers share a single centralized system prompt (`DOCUMENT_SYSTEM_PROMPT` in `chat/providers/utils.py`) with a dedicated multilingual rule:

- The document may be in Gujarati, Hindi, English, or a mix.
- Users may ask questions in any of these languages.
- The LLM matches concepts across languages (e.g. Hindi "syllabus" ↔ Gujarati "અભ્યાસક્રમ").
- Answers are always returned in the same language the user asked in.

### LLM Streaming

```
Question received
  → Conversational check (greeting/small-talk → skip document context)
  → Context resolved:
      full mode  → full markdown (or Gemini cached content)
      rag mode   → top-k chunks retrieved by embedding/BM25
  → ask_streaming() dispatches to provider:
      ├── Gemini: generate_content_stream()
      │     ├── cached → uses cached_content=cache_name (cheap)
      │     └── inline → full context in system_instruction
      ├── Sarvam: chat.completions() (non-streaming SDK, yielded as one token)
      └── Ollama: ollama.chat(stream=True)
  → Tokens yielded one-by-one as SSE events ("data: token\n\n")
  → Frontend accumulates tokens, re-renders markdown live
  → [DONE] event sent after stream completes
  → usage_out dict populated with token counts (try/finally)
  → Session history + cost records saved
```

### Gemini Context Caching

When a document is uploaded with `context_mode=full` and the provider is Gemini:

1. A cache is created via `client.caches.create()` with the document in `contents` and the behavioural rules in `system_instruction`
2. Cache TTL is 1 hour
3. Each chat call passes `cached_content=cache_name` — cached tokens are billed at ~4× lower rate
4. **Model mismatch recovery** — if the Gemini model is changed after caching, the next request detects the `INVALID_ARGUMENT` error, deletes the stale cache, creates a new one with the current model, and retries — fully transparent to the user
5. **Cache expiry recovery** — if the cache has expired (403 / not found), same transparent retry: delete stale name, recache, retry

Cache is never used in RAG mode (different chunks are sent each request).

### Cost Tracking

After each streaming response completes:

```
Token counts from stream:
  Gemini  → usage_metadata.prompt_token_count / candidates_token_count
  Sarvam  → response.usage.prompt_tokens / completion_tokens
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

### Email OTP Verification

Before a user can start chatting, they must verify their email:

1. User submits their name and email address
2. A 6-digit OTP is generated and sent to their email (1-minute expiry)
3. One resend is allowed if the OTP expires
4. On successful verification, the session is marked as verified and the chat UI unlocks

`EmailVerification` records are stored in the DB and cleaned up automatically.

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Main chat UI |
| `GET` | `/status/` | Check if a document is loaded in the current session |
| `POST` | `/chat/` | Send a question, receive SSE token stream |
| `POST` | `/reset/` | Clear session, delete files, remove Gemini cache |
| `POST` | `/request-otp/` | Send OTP to user's email |
| `POST` | `/verify-otp/` | Verify OTP and unlock chat |
| `GET/POST` | `/admin/` | Django admin panel |

### `/chat/` request

```json
{ "question": "exam ka syllabus kya che?" }
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

### `Document` — admin-managed document library

| Field | Type | Description |
|-------|------|-------------|
| `original_filename` | CharField | Uploaded file name |
| `markdown_path` | CharField | Path to extracted markdown file |
| `rag_chunks_path` | CharField | Path to JSON chunk+embedding file |
| `gemini_cache_name` | CharField | Gemini cache ID (e.g. `cachedContents/abc123`) |
| `char_count` | IntegerField | Total extracted character count |
| `context_mode` | CharField | `full` or `rag` |
| `is_active` | BooleanField | Only one document can be active at a time |
| `status` | CharField | `pending` / `ready` / `error` |

### `LLMConfig` — singleton configuration

One row (`pk=1`). All LLM, OCR, and embedding settings live here.

### `ModelPricing`

Admin-managed pricing table. `unique_together = (provider, model_name)`.

### `ChatSession`

One row per user session. Updated atomically after each message.

| Field | Type | Description |
|-------|------|-------------|
| `session_key` | CharField | Token stored in browser localStorage |
| `user_name` | CharField | Verified user name |
| `user_email` | CharField | Verified user email |
| `document_name` | CharField | Active document filename |
| `message_count` | IntegerField | Total Q&A turns |
| `total_input_tokens` | BigIntegerField | Cumulative input tokens |
| `total_output_tokens` | BigIntegerField | Cumulative output tokens |
| `total_cost` | DecimalField(14,6) | Total cost in INR |

### `ChatMessage`

One row per Q&A exchange.

| Field | Type | Description |
|-------|------|-------------|
| `session` | FK → ChatSession | Parent session |
| `provider` | CharField | `ollama`, `gemini`, or `sarvam` |
| `model_name` | CharField | Exact model ID used |
| `question` | TextField | User's question |
| `answer` | TextField | Full LLM response |
| `input_tokens` | IntegerField | Input token count |
| `output_tokens` | IntegerField | Output token count |
| `tokens_estimated` | BooleanField | `True` if Ollama returned 0 and chars÷4 was used |
| `input_cost` | DecimalField(14,6) | Input cost in INR |
| `output_cost` | DecimalField(14,6) | Output cost in INR |
| `response_time_seconds` | FloatField | Wall-clock time for LLM response |

### `EmailVerification`

Temporary OTP records. Fields: `email`, `name`, `code` (6-digit), `expires_at` (1 min), `is_verified`, `resend_count` (max 1).

---

## Logging

All application events are logged to `app.log` (rotating, 10 MB × 5 files) and stdout.

Sample log sequence for a chat request:

```
2026-02-27 01:36:30 [INFO] chat.views: Chat request | session_pk=10 | user=Demo User | q_chars=22 | history_turns=11 | doc=guide.pdf | mode=rag
2026-02-27 01:36:30 [INFO] chat.pipeline: RAG retrieval | method=embedding | q_chars=22 | top_k=3 | selected_pages=[9, 11, 12]
2026-02-27 01:36:30 [INFO] chat.pipeline: LLM stream start | provider=gemini | model=gemini-2.5-flash | history_turns=11 | q_chars=22 | cached=True | conversational=False
2026-02-27 01:36:32 [INFO] chat.pipeline: LLM stream done  | provider=gemini | model=gemini-2.5-flash | response_chars=312 | time=2.14s | cached=True
2026-02-27 01:36:32 [INFO] chat.views: Cost | session_pk=10 | provider=gemini | model=gemini-2.5-flash | in=10482 out=89 est=False | cost=₹0.095
```

Key log events:

| Event | What it tells you |
|-------|------------------|
| `OCR start / complete` | Engine used (effective vs configured), pages, chars, timing |
| `Auto OCR: digital/scanned` | Which branch the Auto engine chose |
| `Gemini cache created` | Cache name, model, char count |
| `Gemini cache invalid … recaching` | Model mismatch or expiry detected, new cache created transparently |
| `RAG retrieval` | Embedding method, top-k, selected page numbers |
| `LLM stream start/done` | Provider, model, `cached=True/False`, timing |
| `Cost` | Token counts, estimated flag, INR cost per message |

> **`cached=False` in RAG mode is expected.** Gemini context caching only applies when `context_mode=full` (the entire document is sent). In RAG mode, different chunks are sent per question, so there is no fixed content to cache.

---

## Development Notes

### Running the server

```bash
source venv/bin/activate
python manage.py runserver
```

### Applying migrations after model changes

```bash
python manage.py makemigrations chat
python manage.py migrate
```

### Sentence-transformers model download

The `multilingual_local` embedding mode downloads `paraphrase-multilingual-MiniLM-L12-v2` (~400 MB) on first use. It is cached in `~/.cache/huggingface/hub/` and reused on subsequent runs.

### Document files lifecycle

| File | Created | Deleted |
|------|---------|---------|
| `markdown_cache/<uuid>.md` | On upload | When admin deletes the document |
| `markdown_cache/<uuid>.json` | On upload | When admin deletes the document |
| `markdown_cache/<uuid>_chunks.json` | On upload | When admin deletes the document |
| `uploads/<uuid>.<ext>` | On upload | Immediately after OCR completes |

### Gemini context cache behaviour

- TTL: 1 hour from creation
- **On model change**: next request detects `INVALID_ARGUMENT`, deletes stale cache, creates new one with current model, retries — no user-visible error
- **On expiry**: next request detects 403, deletes stale name, attempts to recache, retries — no user-visible error
- **In RAG mode**: caching is never used; `cached=False` in logs is expected

### Adding a new LLM provider

1. Add the provider to `LLMConfig.PROVIDER_CHOICES` in `models.py`
2. Create `chat/providers/<provider>.py` — implement a streaming generator using `DOCUMENT_SYSTEM_PROMPT` and `CONVERSATIONAL_SYSTEM_PROMPT` from `utils.py`
3. Add the dispatch branch in `ask_streaming()` and `ask()` in `pipeline.py`
4. Add the model field to `LLMConfig` and `LLMConfigAdmin.list_display`
5. Run `makemigrations` + `migrate`

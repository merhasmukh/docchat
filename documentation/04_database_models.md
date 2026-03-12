# 04 — Database Models

## What This File Covers

Every database table in DocChat, explained field by field. You will learn what Django models are, how migrations work, and the design patterns (like the singleton) used throughout.

**Prerequisites:** File 03 (Django Project Structure).

---

## 1. What is a Django Model?

A Django model is a Python class that represents a database table. Each attribute of the class is a column in the table. Each instance of the class is a row.

```python
class ChatSession(models.Model):
    session_key = models.CharField(max_length=40)  # VARCHAR(40) column
    user_name   = models.CharField(max_length=200) # VARCHAR(200) column
    started_at  = models.DateTimeField(auto_now_add=True)  # DATETIME column
```

This one class creates a table called `chat_chatsession` with three columns (Django prefixes table names with the app name). You never write SQL — Django does it for you.

---

## 2. Migrations — Blueprint Then Build

When you define or change a model, you need to tell Django to update the database. This is a two-step process:

**Step 1 — Create the migration (write the blueprint):**

```bash
python manage.py makemigrations chat
```

Django inspects `chat/models.py`, compares it to the last migration, and generates a Python file in `chat/migrations/` that describes what changed.

**Step 2 — Apply the migration (build the table):**

```bash
python manage.py migrate
```

Django reads the migration files and executes the corresponding SQL to create or alter tables.

> **Rule of thumb:** Every time you add, remove, or change a field in `models.py`, run `makemigrations` then `migrate`. Never edit migration files manually.

---

## 3. The Singleton Pattern

Three models in DocChat are **singletons** — only one row should ever exist:

- `LLMConfig` — the active LLM/OCR configuration
- `DocumentConfig` — the fallback contact text
- `ChatSessionConfig` — the user info collection settings

Each uses this pattern:

```python
@classmethod
def get_active(cls):
    obj, _ = cls.objects.get_or_create(pk=1)
    return obj
```

`get_or_create(pk=1)` means: "find the row with primary key 1, or create it with defaults if it does not exist." This guarantees there is always exactly one configuration row, and it is created automatically on first access.

The admin panel enforces this by disabling the "Add" button once a row exists (`has_add_permission` returns `False` when count ≥ 1).

---

## 4. All Models Explained

All models live in `chat/models.py`.

---

### ModelPricing

Stores how much each LLM model costs per token, in Indian Rupees.

```python
class ModelPricing(models.Model):
    provider                 = models.CharField(max_length=20, choices=[...])
    model_name               = models.CharField(max_length=100)
    input_price_per_million  = models.DecimalField(...)  # INR per 1M input tokens
    output_price_per_million = models.DecimalField(...)  # INR per 1M output tokens
    cache_read_price_per_million            = models.DecimalField(null=True, blank=True)
    cache_storage_price_per_million_per_hour = models.DecimalField(null=True, blank=True)
    is_active  = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("provider", "model_name")]
```

**Field explanations:**
- `provider` — which LLM company: `ollama`, `gemini`, or `sarvam`
- `model_name` — the exact model ID, e.g. `gemini-2.0-flash`
- `input_price_per_million` — cost in INR for every 1 million input tokens sent to the model
- `output_price_per_million` — cost in INR for every 1 million tokens the model generates
- `cache_read_price_per_million` — (Gemini only) cheaper rate for tokens read from Gemini's context cache
- `cache_storage_price_per_million_per_hour` — (Gemini only) cost to keep the cache alive per hour
- `is_active` — set to `False` to pause cost tracking without deleting the row
- `unique_together` — prevents duplicate entries for the same provider + model combination

**Why `DecimalField` instead of `FloatField`?** Financial amounts require exact arithmetic. Floating-point numbers have rounding errors; `DecimalField` stores the exact decimal value.

---

### ChatSession

One row per user session. Stores who the user is, what document they were chatting about, and accumulated token/cost totals.

```python
class ChatSession(models.Model):
    session_key             = models.CharField(max_length=40, unique=True)
    user_name               = models.CharField(max_length=200, blank=True, default="")
    user_email              = models.CharField(max_length=254, blank=True, default="")
    user_mobile             = models.CharField(max_length=20, blank=True, default="")
    document_name           = models.CharField(max_length=500, blank=True)
    started_at              = models.DateTimeField(auto_now_add=True)
    last_activity           = models.DateTimeField(auto_now=True)
    message_count           = models.IntegerField(default=0)
    total_input_tokens      = models.BigIntegerField(default=0)
    total_output_tokens     = models.BigIntegerField(default=0)
    total_tokens            = models.BigIntegerField(default=0)
    avg_tokens_per_message  = models.FloatField(default=0)
    total_cached_input_tokens = models.BigIntegerField(default=0)
    total_cost              = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cache_read_cost   = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cache_storage_cost = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    avg_cost_per_message    = models.DecimalField(max_digits=14, decimal_places=6, default=0)
```

**Key fields explained:**
- `session_key` — a UUID (e.g., `f47ac10b-58cc-4372-a567-0e02b2c3d479`) generated at login and stored in the browser's `localStorage`. This is how the system identifies the user without cookies.
- `user_name/email/mobile` — collected during the OTP login flow (see File 09). Blank if the admin disabled collection.
- `document_name` — which document was active when this session started (stored for analytics even if the document is later deleted)
- `BigIntegerField` for token counts — a long-running session could accumulate millions of tokens; `IntegerField` (max ~2.1 billion on most DBs) might overflow, so `BigIntegerField` (max ~9.2 quintillion) is safer
- `avg_tokens_per_message` and `avg_cost_per_message` — computed atomically each time a message is saved (see how in File 08)

---

### ChatMessage

One row per question/answer exchange. Every message records exactly what was sent, what was received, and the cost.

```python
class ChatMessage(models.Model):
    session               = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    created_at            = models.DateTimeField(auto_now_add=True)
    provider              = models.CharField(max_length=20)
    model_name            = models.CharField(max_length=100)
    question              = models.TextField()
    answer                = models.TextField()
    input_tokens          = models.IntegerField(default=0)
    output_tokens         = models.IntegerField(default=0)
    total_tokens          = models.IntegerField(default=0)
    tokens_estimated      = models.BooleanField(default=False)
    cached_input_tokens   = models.IntegerField(default=0)
    input_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    output_cost           = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    cache_read_cost       = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    cache_storage_cost    = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    response_time_seconds = models.FloatField(default=0)
```

**Key fields explained:**
- `session` — foreign key linking this message to its `ChatSession`. `on_delete=models.CASCADE` means if the session is deleted, all its messages are deleted too.
- `provider` and `model_name` — which LLM answered this specific question (the model can change between messages if the admin changes the config)
- `tokens_estimated = True` — Ollama sometimes returns 0 for token counts. When this happens, DocChat estimates using `len(text) ÷ 4` (a rough approximation of tokens from character count). This flag marks the estimate.
- `cached_input_tokens` — (Gemini only) how many input tokens were served from Gemini's context cache (billed at a cheaper rate)
- `cache_read_cost` and `cache_storage_cost` — (Gemini only) the cost of reading and storing cached content, tracked separately from normal LLM costs
- `response_time_seconds` — wall-clock time from question sent to stream completed; useful for performance monitoring

---

### Document

One row per document managed by the admin. This is the central record for everything related to a document: where its files are, what OCR found, and whether it is currently active.

```python
class Document(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("ready", "Ready"), ("error", "Error")]
    SOURCE_CHOICES = [("file", "File Upload"), ("text", "Pasted Text")]

    original_filename = models.CharField(max_length=500)
    source_type       = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="file")
    markdown_path     = models.CharField(max_length=500, blank=True)
    json_path         = models.CharField(max_length=500, blank=True)
    qdrant_collection = models.CharField(max_length=100, blank=True)
    gemini_cache_name = models.CharField(max_length=200, blank=True)
    total_pages       = models.IntegerField(default=0)
    char_count        = models.IntegerField(default=0)
    context_mode      = models.CharField(max_length=20, default="full")
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error_message     = models.TextField(blank=True)
    is_active         = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True, status="ready").first()
```

**The document lifecycle:**

```
Admin uploads file
       ↓
status = "pending"   (record created, OCR not yet done)
       ↓
OCR runs (pipeline.py)
       ↓
status = "ready"     (text extracted, Qdrant loaded, optional Gemini cache created)
   OR
status = "error"     (OCR failed; error_message has details)
```

**Key fields explained:**
- `markdown_path` — absolute path to the extracted markdown text file in `markdown_cache/`
- `json_path` — absolute path to the per-page JSON structure (used by the agent's `get_page()` tool)
- `qdrant_collection` — the name of the Qdrant vector collection holding this document's chunks
- `gemini_cache_name` — the Gemini context cache ID (e.g., `cachedContents/abc123`). Empty if caching is not used.
- `context_mode` — set at upload time based on `char_count` vs `CONTEXT_CHAR_THRESHOLD`. Either `"full"` or `"rag"`.
- `is_active` — only one document can be active at a time. The admin ensures this via the "Set as active" action.
- `get_active()` — the classmethod all views call to find which document to answer questions about. Returns `None` if no document is active and ready.

---

### LLMConfig

The singleton configuration for all AI settings. One row, always.

```python
class LLMConfig(models.Model):
    provider     = models.CharField(choices=[("ollama", ...), ("gemini", ...), ("sarvam", ...)], default="ollama")
    ollama_model = models.CharField(default="llama3.2-vision")
    gemini_model = models.CharField(default="gemini-2.0-flash")
    sarvam_model = models.CharField(default="sarvam-m")
    ocr_engine   = models.CharField(choices=[("auto", ...), ("docling", ...), ("tesseract", ...), ("gemini_vision", ...), ("pdftext", ...)], default="docling")
    rag_embedding = models.CharField(choices=[("bm25", ...), ("multilingual_local", ...), ("gemini_embedding", ...)], default="multilingual_local")
    context_mode = models.CharField(choices=[("auto", ...), ("full", ...), ("rag", ...)], default="auto")
    use_gemini_cache = models.BooleanField(default=True)
    agent_mode   = models.BooleanField(default=False)

    @classmethod
    def get_active(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
```

**Field explanations:**
- `provider` — which LLM to use for chat responses: `ollama` (local), `gemini` (cloud), or `sarvam` (Indic cloud)
- `ollama_model / gemini_model / sarvam_model` — the specific model within each provider; only the field matching the active `provider` is used
- `ocr_engine` — which OCR method to use when processing new document uploads
- `rag_embedding` — how to compute embeddings for RAG retrieval: `bm25` (keyword, no embeddings), `multilingual_local` (offline sentence-transformers), or `gemini_embedding` (cloud API)
- `context_mode` — overrides per-document context mode: `auto` (use what was computed at upload time), `full` (force sending whole document), `rag` (force chunk retrieval)
- `use_gemini_cache` — when `True` and using Gemini in full-context mode, the document is uploaded to Gemini's cache (cheaper repeated access). Set `False` to always send inline (no cache storage cost).
- `agent_mode` — when `True`, chat requests go through the ReAct agent loop instead of direct LLM call. See File 10.

---

### DocumentConfig

Singleton. Controls what happens when the bot cannot find the answer.

```python
class DocumentConfig(models.Model):
    fallback_contact = models.TextField(blank=True, default="")
```

- `fallback_contact` — text shown to users when the LLM determines the document does not contain the answer. If blank, the bot gives a generic "I don't know" reply. If set, it appends the contact info so users know where to get help.

Example value:
```
Gujarat Vidyapith
Ashram Marg, Navrangpura, Ahmedabad – 380 009
Phone: 079-27541148
Website: https://gujaratvidyapith.org/
```

---

### ChatSessionConfig

Singleton. Controls what information is collected from users before they can chat.

```python
class ChatSessionConfig(models.Model):
    collect_name   = models.BooleanField(default=True)
    collect_email  = models.BooleanField(default=True)
    collect_mobile = models.BooleanField(default=False)
    verify_email   = models.BooleanField(default=True)
```

**Field explanations:**
- `collect_name` — if `True`, the chat UI shows a "Your name" input before the user can send a message
- `collect_email` — if `True`, the chat UI asks for an email address
- `collect_mobile` — if `True`, the chat UI asks for a mobile number (no OTP verification for mobile)
- `verify_email` — if `True` (and `collect_email` is `True`), the user receives a 6-digit OTP email they must enter before chatting. Set `False` to collect email without verification.

**Common presets:**

| Use Case | collect_name | collect_email | collect_mobile | verify_email |
|----------|-------------|--------------|---------------|-------------|
| Full verification (default) | ✓ | ✓ | ✗ | ✓ |
| Name only, no OTP | ✓ | ✗ | ✗ | — |
| Collect all, no OTP | ✓ | ✓ | ✓ | ✗ |
| Completely anonymous | ✗ | ✗ | ✗ | — |

---

### EmailVerification

Temporary records for the OTP login flow. Created when a user requests an OTP, consumed when they verify it.

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

    @classmethod
    def generate_code(cls):
        return str(_secrets.randbelow(900_000) + 100_000)

    @property
    def is_expired(self):
        return _tz.now() >= self.expires_at

    def refresh_code(self):
        self.code = self.generate_code()
        self.expires_at = _tz.now() + _dt.timedelta(minutes=1)
```

**Field explanations:**
- `code` — a 6-digit string (`"123456"`) generated by `generate_code()`
- `expires_at` — set to 1 minute after creation. After this, the code is invalid.
- `is_verified` — set to `True` after the user enters the correct code. The record is kept as an audit log.
- `resend_count` — starts at 0, incremented each time the user clicks "Resend". Capped at 1.

**Why `secrets.randbelow` instead of `random.randint`?**
Python's `random` module is designed for simulations, not security — its output is predictable if you know the seed. `secrets` is designed for cryptographic use and produces genuinely unpredictable values. For a security code, always use `secrets`.

**The `generate_code()` logic:**
```python
_secrets.randbelow(900_000) + 100_000
```
`randbelow(900_000)` returns a random integer from 0 to 899,999. Adding 100,000 shifts the range to 100,000–999,999, ensuring the code is always exactly 6 digits.

---

### AgentMemory

Cross-session memory for the ReAct agent. One row per user email — the agent builds up a small text summary of what it knows about each user over multiple sessions.

```python
class AgentMemory(models.Model):
    user_email     = models.EmailField(unique=True, db_index=True)
    memory_text    = models.TextField(blank=True)
    total_sessions = models.IntegerField(default=0)
    last_updated   = models.DateTimeField(auto_now=True)
```

**Field explanations:**
- `user_email` — the unique key (one memory per user)
- `memory_text` — a short plain-text summary, capped at 500 characters. Example: `"User is interested in MCA admission. Prefers Gujarati responses. Asked about fees twice."`
- `total_sessions` — how many chat sessions this user has had (incremented each time memory is saved)

Memory is updated automatically in a background thread every 5 messages. See File 10 for how the agent uses and updates memory.

---

## 5. How to Inspect the Database

### Using Django's shell

```bash
python manage.py shell
```

```python
from chat.models import Document, ChatSession, LLMConfig

# See all documents
Document.objects.all()

# Get the active document
Document.get_active()

# Get the LLM config
cfg = LLMConfig.get_active()
print(cfg.provider, cfg.gemini_model)

# Count chat messages
from chat.models import ChatMessage
ChatMessage.objects.count()
```

### Using DB Browser for SQLite (GUI)

Download [DB Browser for SQLite](https://sqlitebrowser.org/) and open `db.sqlite3` from the project root. You can browse all tables, run SQL queries, and export data — without using the terminal.

### Using Django's dbshell

```bash
python manage.py dbshell
```

Opens a direct SQL shell to your database. For SQLite, this is `sqlite3`. Type `.tables` to list all tables, or run SQL queries directly.

---

## 6. After Changing Models

Every time you edit `models.py`, you must regenerate and apply migrations:

```bash
# 1. Generate the migration
python manage.py makemigrations chat

# 2. Apply it to the database
python manage.py migrate

# 3. Restart the development server
python manage.py runserver
```

If you forget to run `makemigrations`, your Python code and database will be out of sync — Django will raise errors when it tries to save data to columns that do not exist yet.

---

## What to Do Next

Read [File 05 — OCR & Document Pipeline](05_ocr_and_document_pipeline.md) to understand how uploaded documents are converted into searchable text.

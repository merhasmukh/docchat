# 11 — Admin Panel Guide

## What This File Covers

A complete walkthrough of every section in the Django admin panel — for both technical users setting up the system and non-technical administrators managing day-to-day operations.

**Prerequisites:** File 04 (Database Models) — understanding the models helps you understand what each admin form controls.

---

## 1. Accessing the Admin Panel

1. Start the development server: `python manage.py runserver`
2. Go to `http://127.0.0.1:8000/admin/`
3. Log in with the superuser credentials you created with `python manage.py createsuperuser`

You will see the main admin index with these sections:

```
CHAT
  ├── Agent Memories
  ├── Chat Messages
  ├── Chat Sessions
  ├── Chat Session Configurations
  ├── Document Configurations
  ├── Documents
  ├── Email Verifications
  ├── LLM Configurations
  └── Model Pricing

AUTHENTICATION AND AUTHORIZATION
  ├── Groups
  └── Users
```

---

## 2. Documents — Uploading and Managing

This is the most important section — it is how you get content into the chatbot.

### Adding a Document

Click **Documents** → **Add Document** (top right).

You will see a form with two modes (selected by radio button):

**Mode 1: Upload a File**

- **Document name** — a human-readable label (e.g., "Admission Prospectus 2024-25")
- **Upload file** — click to browse and select your file
- Supported formats: PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP
- Maximum size: 50 MB

**Mode 2: Paste Text**

- **Document name** — a label for this content
- **Paste text** — type or paste the document content directly
- **Context mode** — choose Full (document fits in one LLM prompt) or RAG (too large, use retrieval)
- Use this for content that is not in a file format — FAQs, structured data, etc.

Click **Save** to start processing. The page will redirect to the document list.

### Document Status

After saving, the document goes through processing:

| Status | Color | Meaning |
|--------|-------|---------|
| Pending | Orange | OCR/processing is running (refresh in a few seconds) |
| Ready | Green | Processing complete, document is usable |
| Error | Red | Something went wrong (click to see the error message) |

**Refresh the page** every few seconds to see status updates. Large PDFs can take 1-5 minutes.

### Setting a Document as Active

Only one document can be active (used for chat) at a time.

**Method 1 — Bulk action:**
1. In the Documents list, check the checkbox next to your document
2. In the "Action" dropdown, select "Set as active document"
3. Click "Go"

**Method 2 — Edit form:**
1. Click on the document
2. Check the "Is active" checkbox
3. Save

> **Note:** When you activate a new document, the previously active one is automatically deactivated. You cannot have two active documents simultaneously.

### Deleting a Document

When you delete a document from the admin, DocChat automatically:
- Removes the markdown file from `markdown_cache/`
- Removes the page JSON file from `markdown_cache/`
- Deletes the Qdrant vector collection
- Deletes the Gemini context cache (if one exists)

This cleanup happens in the `DocumentAdmin.delete_queryset()` and `DocumentAdmin.delete_model()` methods in `chat/admin.py`.

> **Warning:** If the deleted document was active, users will get "No document loaded" errors until you activate a new one.

### Document List Columns

| Column | Description |
|--------|-------------|
| Name | Document name + ACTIVE badge if it is the current active document |
| Status | Pending / Ready / Error |
| Pages | Number of pages extracted |
| Chars | Total character count of extracted text |
| Mode | "full" or "rag" — which context strategy was computed at upload |
| Source | File Upload or Pasted Text |
| Created | When it was added |

---

## 3. LLM Configuration — The Control Panel

Click **LLM Configurations**. There will be exactly one row. Click it to edit.

> Only one configuration row ever exists (singleton pattern). The "Add" button is hidden once a row exists.

### Provider

Choose which AI service to use for answering questions:

| Option | Best For |
|--------|---------|
| **Ollama (Local)** | Privacy, offline use, zero cost, development |
| **Gemini (Google)** | Best quality, multilingual, large documents |
| **Sarvam AI** | Specialised Hindi/Gujarati, Indic script quality |

### Model Names

Each provider has its own model name field. Only the field matching the active provider is used.

**Ollama model examples:**
- `llama3.2-vision` (default, supports image understanding)
- `llama3.1:8b` (lighter, text-only)
- `mistral` (good quality, medium size)

To see available Ollama models: `ollama list` in your terminal.

**Gemini model examples:**
- `gemini-2.0-flash` (default, fast and capable)
- `gemini-1.5-pro` (more capable, higher cost)
- `gemini-2.0-flash-lite` (cheapest)

See [Google AI Studio](https://aistudio.google.com) for current model names and pricing.

**Sarvam AI model:**
- `sarvam-m` (default, the main multilingual Indic model)

### OCR Engine

Determines how **new documents** are processed (does not reprocess existing ones):

| Engine | Best For |
|--------|---------|
| **Auto (recommended)** | Most documents — detects digital vs scanned automatically |
| **Docling** | Digital PDFs with tables and structured layout |
| **Tesseract** | Scanned documents in Hindi, Gujarati, or English |
| **Gemini Vision** | Complex, mixed-script, or poor-quality scans (requires Gemini API key) |
| **PDF to Text** | Digital PDFs only — fastest, no image conversion |

### RAG Embedding

Determines how chunk retrieval works for large documents (in RAG mode):

| Method | Description |
|--------|-------------|
| **BM25** | Keyword search — fast, offline, same-language only |
| **Multilingual Local** | Sentence-transformers — offline, cross-language, downloads ~90 MB once |
| **Gemini Multilingual Embeddings** | Best quality, cross-language, requires Gemini API, has cost |

### Context Mode

Overrides the per-document mode:

| Setting | Effect |
|---------|--------|
| **Auto (recommended)** | Each document uses the mode computed at upload time |
| **Full context** | Always send the entire document to the LLM |
| **RAG** | Always retrieve relevant chunks only |

Use "Full context" with Gemini if you want to take advantage of context caching even for larger documents. Use "RAG" to save costs on repetitive queries to the same large document.

### Use Gemini Cache

When checked: the document is uploaded to Gemini's context cache at upload time. Subsequent questions use the cache (cheaper). Recommended when using Gemini with full-context documents.

When unchecked: the full document is sent inline with every request (no cache storage cost but higher per-request cost).

### Agent Mode

When checked: chat requests go through the ReAct agent loop (multiple tool calls, user memory). More powerful but slower (4+ LLM calls per question instead of 1).

Requires email collection to be enabled (memory is keyed by user email).

### Get Embed Script

At the top of the LLM Configuration change form, you will see a "Get embed script" link. Clicking it opens a page showing the `<iframe>` code to embed the chatbot on any external website. See File 12 for widget details.

---

## 4. Chat Session Configuration

Click **Chat Session Configurations**. One row (singleton).

Controls what information users must provide before chatting:

| Field | Default | Description |
|-------|---------|-------------|
| Collect name | Yes | Ask for user's name |
| Collect email | Yes | Ask for user's email |
| Collect mobile | No | Ask for user's phone number |
| Verify email | Yes | Send OTP to email (only if Collect email is on) |

**Common setups:**

**Full verification (default):** All defaults — users enter name, email, receive OTP.

**Anonymous access:** Uncheck everything — the chat opens immediately with no info collected.

**Name only:** Check only Collect name, uncheck email. Users enter a name but no verification.

---

## 5. Document Configuration

Click **Document Configurations**. One row (singleton).

**Fallback contact** — the text shown to users when the bot determines the document does not contain the answer.

**Example value:**
```
For questions not covered in this document, please contact:
Gujarat Vidyapith Admission Office
Phone: 079-27541148
Email: admission@gujaratvidyapith.org
Website: https://gujaratvidyapith.org/
```

**If left blank:** The bot gives a generic "I'm sorry, I don't have information about that" response.

**Tip:** Include multiple contact channels (phone, email, website) so users can choose their preferred way to reach you.

---

## 6. Model Pricing — Tracking Costs in INR

Click **Model Pricing** to manage the pricing table.

DocChat uses this table to calculate how much each message costs in Indian Rupees (INR). Without pricing rows, all costs show as ₹0.

### Adding a Pricing Row

Click **Add Model Pricing**:

- **Provider** — Ollama, Gemini, or Sarvam AI
- **Model name** — exact model ID (e.g., `gemini-2.0-flash`). Must match what is in LLM Configuration.
- **Input price per million** — INR per 1,000,000 input tokens
- **Output price per million** — INR per 1,000,000 output tokens
- **Cache read price per million** — (Gemini only) INR per 1M tokens read from cache
- **Cache storage price per million per hour** — (Gemini only) INR per 1M cached tokens per hour

### Where to Find Current Prices

**For Gemini:** Go to [Google AI pricing page](https://ai.google.dev/pricing) and find your model. Prices are usually in USD. Convert to INR (1 USD ≈ 84 INR as of early 2025).

**Example for `gemini-2.0-flash`** (approximate, check current pricing):
- Input: $0.075 / 1M tokens → ₹6.30 / 1M tokens
- Output: $0.30 / 1M tokens → ₹25.20 / 1M tokens
- Cache read: $0.01875 / 1M tokens → ₹1.57 / 1M tokens
- Cache storage: $1.00 / 1M tokens / hour → ₹84 / 1M tokens / hour

**For Ollama:** Ollama is free — enter `0.0000` for all price fields. Or leave `is_active` unchecked to skip cost tracking entirely.

**For Sarvam AI:** Check [sarvam.ai](https://sarvam.ai) for current pricing.

---

## 7. Chat Sessions — Monitoring Usage

Click **Chat Sessions** to see all user sessions.

### List View

| Column | Description |
|--------|-------------|
| Session key | First 12 chars of the UUID token |
| User name / email | Collected during login |
| Document | Which document was active when this session started |
| Messages | Total questions asked |
| Total tokens | Input + output tokens for the whole session |
| Total cost | INR cost for the whole session |
| Started | When the session began |
| Last activity | Last message time |

Click on a session to see:
- All individual messages (inline, most recent first)
- Per-message token counts and costs
- `tokens_estimated = True` flag (when Ollama returned 0 and estimation was used)
- Response times
- Cached token counts (for Gemini)

### Useful for Budgeting

- `avg_cost_per_message` — the typical cost per question. Multiply by expected daily messages to estimate monthly costs.
- `total_cached_input_tokens` — if high, context caching is working effectively.

---

## 8. Chat Messages — Read-Only Message Log

Click **Chat Messages** to browse all individual Q&A pairs across all sessions.

**Useful for:**
- Seeing what questions users are asking (product insights)
- Verifying that answers are accurate
- Debugging high-cost or long-response-time messages

**Filters available:** By provider, model, date range, session.

---

## 9. Email Verifications — OTP Audit Log

Click **Email Verifications** to see all OTP records.

**Useful for debugging:**
- User says "I didn't get the OTP" → find their email, check `created_at` and `expires_at`, see if `is_verified` is True (they already verified)
- Check `resend_count` to see if they used the resend feature
- See if the code was recently created (within 1 minute of when they tried)

**Columns:** Email, code (visible for debugging), verified status, expiry, resend count, created time.

---

## 10. Agent Memories — Cross-Session User Knowledge

Click **Agent Memories** (only visible when agent mode has been used).

One row per user email. Shows:
- **User email** — the unique key
- **Memory text** — what the agent has learned about this user
- **Total sessions** — how many sessions have contributed to this memory
- **Last updated** — when it was last compressed/updated

**Admins can:**
- **Edit** memory text manually if it contains errors
- **Delete** a memory row to clear all knowledge about a user (they start fresh next session)

---

## What to Do Next

Read [File 12 — Frontend and Widget](12_frontend_and_widget.md) to understand the HTML/JavaScript frontend, how the SSE stream is consumed, and how to embed the chatbot on any external website.

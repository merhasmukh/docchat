# 01 — Introduction and Project Overview

## What This File Covers

This is the starting point. Before writing a single line of code, you need a clear mental picture of what DocChat is, how all its pieces connect, and what you will build by the end of this guide. Read this file first — everything else builds on it.

---

## 1. What is DocChat?

DocChat is a web application that lets users upload a document (a PDF or image) and then ask questions about it in plain language — including in Hindi and Gujarati — and get answers streamed back in real time, word by word.

Think of it as a smart assistant that has read your document and can answer questions about it, in multiple languages, without the user having to search through pages manually.

**Concrete example:** An admission office uploads the university prospectus (a 150-page PDF in Gujarati). A prospective student types "MCA ma admission mate su joyeye?" (What is needed for MCA admission?). The system finds the relevant pages and streams back the answer in Gujarati — in seconds.

---

## 2. What You Will Build

By the end of this documentation series, you will have a fully working system with all of these features:

| Feature | Description |
|---------|-------------|
| **Document Upload** | Upload PDFs and images (up to 50 MB) via the admin panel |
| **OCR** | Extract text from scanned and digital documents using 5 different engines |
| **Multilingual Q&A** | Ask questions in English, Hindi, or Gujarati |
| **3 LLM Providers** | Ollama (local/offline), Google Gemini (cloud), Sarvam AI (Indic-focused cloud) |
| **Streaming Responses** | Answers appear word-by-word in real time (like ChatGPT) |
| **RAG Retrieval** | Smart search through large documents using BM25 or vector embeddings |
| **Email OTP Login** | Users verify their email with a 6-digit one-time code before chatting |
| **Cost Tracking** | Every message tracks tokens used and cost in Indian Rupees (INR) |
| **ReAct Agent** | Advanced mode where the AI uses tools to search the document step-by-step |
| **Admin Panel** | Manage documents, configure the AI, monitor usage — no coding needed |
| **Embeddable Widget** | Embed the chatbot on any external website using an `<iframe>` |
| **Deployment** | Run in production with gunicorn, nginx, MySQL, and SSL |

---

## 3. How All the Pieces Fit Together

Here is the high-level architecture. Do not worry about understanding every term yet — the glossary below explains them, and later files cover each piece in depth.

```
┌─────────────────────────────────────────────────────────────┐
│                        BROWSER (User)                       │
│                                                             │
│   1. Types question → POST /chat/                           │
│   2. Reads streaming answer ← SSE token stream             │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP
┌───────────────────────▼─────────────────────────────────────┐
│                    DJANGO (Web Server)                       │
│                                                             │
│   views.py   →  chat_view()  →  ask_streaming()            │
│                               ↓                             │
│                        pipeline.py                          │
│                     ┌─────────────┐                         │
│                     │  RAG mode?  │                         │
│                     │  Retrieve   │                         │
│                     │  top pages  │                         │
│                     └──────┬──────┘                         │
│                            │                                │
│            ┌───────────────▼────────────────┐              │
│            │         LLM Provider           │              │
│            │  Ollama │ Gemini │ Sarvam AI   │              │
│            └───────────────┬────────────────┘              │
│                            │ token stream                   │
└────────────────────────────┼────────────────────────────────┘
                             │ SSE (text/event-stream)
                    back to browser

──── Admin uploads a document ────────────────────────────────

┌─────────────────────────────────────────────────────────────┐
│                   ADMIN PANEL (/admin/)                      │
│                                                             │
│   Upload PDF/Image                                          │
│        ↓                                                    │
│   pipeline.py → OCR Engine (Docling / Tesseract /           │
│                              Gemini Vision / pdftext)       │
│        ↓                                                    │
│   Save markdown to markdown_cache/                          │
│        ↓                                                    │
│   Build RAG chunks → Store in Qdrant (vector DB)            │
│        ↓                                                    │
│   Create Gemini Context Cache (if applicable)               │
│        ↓                                                    │
│   Document.status = "ready" → chat is now live              │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Technology Stack — Why Each Was Chosen

| Technology | Role | Why This One |
|-----------|------|--------------|
| **Django 5** | Web framework | Battle-tested Python web framework; excellent admin panel built-in |
| **Django REST Framework** | API layer | Adds clean `@api_view` decorators and auto-generated OpenAPI docs |
| **SQLite / MySQL / PostgreSQL** | Database | SQLite for development (zero setup); MySQL/PostgreSQL for production |
| **Ollama** | Local LLM | Free, private, runs offline — no API key or internet required |
| **Google Gemini** | Cloud LLM | Excellent multilingual quality; supports context caching (cheaper) |
| **Sarvam AI** | Indic cloud LLM | Specialised for Hindi/Gujarati; best Indic language quality |
| **Docling** | OCR (digital PDFs) | Layout-aware: preserves tables, headings, and structure as markdown |
| **Tesseract** | OCR (scanned docs) | Open-source, supports Hindi (`hin`) and Gujarati (`guj`) scripts |
| **Gemini Vision** | OCR (cloud) | Highest accuracy for complex mixed-script scanned documents |
| **pdfplumber** | OCR (text PDFs) | Direct text extraction — no image conversion, fastest option |
| **rank_bm25** | Keyword search | Fast, offline BM25 retrieval — good for same-language queries |
| **sentence-transformers** | Vector embeddings | Multilingual model, runs offline, cross-language search |
| **Qdrant** | Vector database | Embedded (no separate server), fast, disk-backed vector storage |
| **Bootstrap 5** | Frontend CSS | Responsive, well-documented UI framework — no custom CSS needed |
| **marked.js + DOMPurify** | Markdown rendering | Render LLM markdown output safely in the browser |
| **Server-Sent Events (SSE)** | Streaming | Native browser API for one-way real-time server → client streaming |

---

## 5. Reading Order

Work through the files in this order. Each file builds on the previous one.

| # | File | What You Will Build | Prerequisite |
|---|------|---------------------|-------------|
| 01 | This file | Mental model | None |
| 02 | [Environment Setup](02_environment_setup.md) | Working Python environment + `runserver` | None |
| 03 | [Django Project Structure](03_django_project_structure.md) | Understand every file's role | File 02 |
| 04 | [Database Models](04_database_models.md) | All database tables with migrations | File 03 |
| 05 | [OCR & Document Pipeline](05_ocr_and_document_pipeline.md) | Document upload + text extraction | File 04 |
| 06 | [RAG Retrieval System](06_rag_retrieval_system.md) | Intelligent chunk retrieval | File 05 |
| 07 | [LLM Providers](07_llm_providers.md) | Ollama, Gemini, Sarvam AI streaming | File 06 |
| 08 | [API Endpoints & Views](08_api_endpoints_and_views.md) | All REST endpoints + SSE chat | File 07 |
| 09 | [Email OTP Authentication](09_email_otp_authentication.md) | Email verification flow | File 08 |
| 10 | [ReAct Agent Loop](10_react_agent_loop.md) | Multi-step reasoning with tools | File 07 |
| 11 | [Admin Panel Guide](11_admin_panel_guide.md) | Full admin walkthrough | File 04 |
| 12 | [Frontend & Widget](12_frontend_and_widget.md) | Chat UI + embeddable widget | File 08 |
| 13 | [Deployment](13_deployment.md) | Production server with nginx + SSL | All files |

---

## 6. Glossary of Terms

These terms appear throughout the documentation. Refer back here whenever you see an unfamiliar word.

**LLM (Large Language Model)**
An AI model trained on vast amounts of text that can generate human-like answers to questions. Examples: Gemini, Llama 3, GPT-4. DocChat uses the LLM to actually answer the user's question.

**OCR (Optical Character Recognition)**
The process of "reading" an image (such as a scanned PDF page) and converting it into machine-readable text. Without OCR, a scanned document is just a picture — the LLM cannot read it.

**RAG (Retrieval-Augmented Generation)**
A technique for handling large documents. Instead of sending the entire document to the LLM (which may be too large), you first *retrieve* the most relevant pages, then *augment* the LLM prompt with those pages, then *generate* an answer. This saves cost and avoids context window limits.

**SSE (Server-Sent Events)**
A browser technology that allows the server to push data to the browser over a single HTTP connection, continuously. DocChat uses SSE to stream the LLM's answer one word at a time, so the user sees it appearing as it is generated.

**OTP (One-Time Password)**
A temporary code (usually 6 digits) sent to a user's email or phone. The user enters it to prove they own that email address. DocChat uses email OTP to verify users before allowing them to chat.

**Embedding**
A way of representing text as a list of numbers (a vector) such that texts with similar meanings have similar numbers. Embeddings allow the RAG system to find pages that are *semantically* related to a question, even if they use different words.

**Token**
LLMs do not process text word-by-word but as "tokens" — chunks roughly the size of a word or syllable. API providers charge per token. DocChat tracks input tokens (what you send the LLM) and output tokens (what it sends back) to compute cost.

**Context Window**
The maximum amount of text an LLM can "see" at once. Older models had small context windows (e.g., 4,096 tokens). Modern models like Gemini 2.0 Flash can handle over 1 million tokens. DocChat uses this concept to decide between full-context mode (send everything) and RAG mode (send only relevant pages).

**Context Caching (Gemini)**
A Gemini feature where you upload a large document once and it stays stored on Google's servers for up to 1 hour. Subsequent requests that use the cached document cost much less (~75% cheaper for the cached part).

**ReAct Agent**
An LLM that can use *tools* (like a search function or page fetcher) in a loop: reason about what tool to call, call it, observe the result, and repeat — until it has enough information to answer the question. Stands for Reason + Act.

**Vector Database**
A specialised database that stores embeddings (text as numbers) and can quickly find which stored items are most similar to a query. DocChat uses Qdrant as its vector database.

**Qdrant**
An open-source vector database that DocChat runs locally (embedded — no separate server process needed). It stores RAG chunks and their embeddings, and answers similarity search queries.

---

## What to Do Next

Read [File 02 — Environment Setup](02_environment_setup.md) to install Python, all dependencies, and get the project running on your machine for the first time.

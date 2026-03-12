# 07 — LLM Providers

## What This File Covers

How the three LLM providers (Ollama, Gemini, Sarvam AI) are integrated, how prompts are built with multilingual rules, how streaming works, and how to add a new provider.

**Prerequisites:** File 06 (RAG Retrieval System) — you need to understand how context is prepared before being sent to the LLM.

---

## 1. The Provider Architecture

All LLM provider code lives in `chat/providers/`:

```
chat/providers/
├── utils.py       ← Shared prompts, language rules, conversational detection
├── gemini.py      ← Google Gemini streaming + context caching
├── ollama.py      ← Ollama (local) streaming
├── sarvam.py      ← Sarvam AI streaming
└── __init__.py
```

The main dispatch point is `ask_streaming()` in `chat/pipeline.py`:

```python
def ask_streaming(question, history, context, cfg=None, usage_out=None):
    if cfg is None:
        cfg = LLMConfig.get_active()

    if cfg.provider == "gemini":
        yield from ask_streaming_gemini(question, history, context, cfg, usage_out)
    elif cfg.provider == "sarvam":
        yield from ask_streaming_sarvam(question, history, context, cfg, usage_out)
    else:  # ollama (default)
        yield from ask_streaming_ollama(question, history, context, cfg, usage_out)
```

Every provider is a **generator function** — it `yield`s string tokens one at a time. The calling code (`chat_view` in `views.py`) forwards each token to the browser via SSE.

---

## 2. Shared Utilities (`chat/providers/utils.py`)

This module contains everything shared across all providers.

### Conversational Detection

Some questions are small talk — greetings, thank-yous, simple acknowledgements — that have nothing to do with the document.

```python
_CONVERSATIONAL = {
    "hi", "hello", "hey", "hii", "helo",
    "ok", "okay", "fine", "thanks", "thank you", "ty",
    "bye", "goodbye", "see you",
    "good morning", "good afternoon", "good evening", "good night",
    "namaste", "kem cho", "shu chhe",
    # ... and many more
}

def is_conversational(question: str) -> bool:
    normalised = question.strip().lower()
    return normalised in _CONVERSATIONAL
```

When `is_conversational()` returns `True`, DocChat does not look up any document context — it just responds with a friendly reply. This avoids wasting tokens and avoids awkward responses like "The document does not mention any greeting."

### Building the Document Prompt

When the question is not conversational, `build_document_prompt()` constructs the full system message:

```python
def build_document_prompt(doc_text: str, doc_config, question: str) -> str:
    rules = _build_rules(doc_config)
    return f"""You are a helpful assistant for document Q&A.

DOCUMENT CONTENT:
{doc_text}

{rules}"""
```

### The Language Rules (`_build_rules()`)

This is the most important part of the prompt. It enforces the language-matching behaviour:

```
STRICT RULES:
1. LANGUAGE: Detect the language of the user's question.
   - If the question is in Gujarati → answer in Gujarati
   - If the question is in Hindi → answer in Hindi
   - If the question is in English → answer in English
   - Mixed language → match the dominant language
2. DOCUMENT-ONLY: Answer only from the provided document content.
   Do not use outside knowledge.
3. NOT FOUND: If the answer is not in the document, say so clearly.
   {fallback_contact}
4. CONVERSATION: Use the conversation history for context.
5. NO CITATIONS: Do not say "the document states" or "according to the document".
   Answer directly.
6. CROSS-LANGUAGE: If the user asks in Gujarati about a topic written in English
   in the document, still find and answer it — translate the concept, not just words.
```

Without rule 1, LLMs default to English even when the question is in Gujarati. Rule 6 enables cross-language RAG — the user can ask in their preferred language regardless of what language the document is in.

### Citation Scrubber

```python
_CITATION_RE = re.compile(
    r'\b(according to (the )?(document|text|content|information|pdf)|'
    r'the (document|text|pdf) (states?|says?|mentions?|indicates?|shows?)|'
    r'based on (the )?(document|provided|given))\b',
    re.IGNORECASE
)

def strip_citation_phrases(text: str) -> str:
    return _CITATION_RE.sub("", text)
```

Applied to all Sarvam AI responses (and available for other providers). Removes phrases like "According to the document, the fees are..." → "The fees are..."

---

## 3. Ollama Provider (`chat/providers/ollama.py`)

Ollama runs open-source LLMs locally. No API key, no internet, completely free.

### How to Run Ollama

```bash
# In a separate terminal (keep it running while DocChat runs)
ollama serve

# Pull your chosen model (one-time download)
ollama pull llama3.2-vision   # default model in LLMConfig
```

### Message Format

Ollama uses a list of messages — same format as OpenAI:

```python
def _build_messages(question, history, context, cfg, is_conv):
    if is_conv:
        system = CONVERSATIONAL_SYSTEM_PROMPT
    else:
        system = build_document_prompt(context, doc_config, question)

    messages = [{"role": "system", "content": system}]

    # Add conversation history
    for msg in history[-20:]:  # last 10 pairs
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Add the current question
    messages.append({"role": "user", "content": question + _LANG_HINT})
    return messages
```

`_LANG_HINT` is a short instruction appended to every non-conversational question:

```python
_LANG_HINT = "\n\n[Respond in the same language as this question.]"
```

This reinforces the language rule at the message level (some models need this reminder even with system prompt instructions).

### Streaming

```python
def ask_streaming_ollama(question, history, context, cfg, usage_out):
    messages = _build_messages(question, history, context, cfg, is_conversational(question))

    stream = ollama.chat(model=cfg.ollama_model, messages=messages, stream=True)

    full_response = ""
    final_chunk = None

    for chunk in stream:
        token = chunk["message"]["content"]
        full_response += token
        yield token           # send to browser immediately
        final_chunk = chunk

    # Token counting from the final chunk
    if final_chunk and final_chunk.get("done"):
        input_tokens  = final_chunk.get("prompt_eval_count", 0)
        output_tokens = final_chunk.get("eval_count", 0)

        # Ollama sometimes returns 0 — fall back to character estimation
        if input_tokens == 0 and output_tokens == 0:
            input_tokens  = len(" ".join(m["content"] for m in messages)) // 4
            output_tokens = len(full_response) // 4
            usage_out["estimated"] = True

        usage_out["input"]  = input_tokens
        usage_out["output"] = output_tokens
```

`ollama.chat(stream=True)` returns an iterator. Each iteration yields a chunk dict. `chunk["message"]["content"]` is the next token. The final chunk has `done=True` and includes token counts.

---

## 4. Gemini Provider (`chat/providers/gemini.py`)

Google's cloud LLM. Best multilingual quality. Supports context caching for cost savings.

### Message Conversion

Gemini uses a different message format — `Content` objects instead of dicts:

```python
from google.genai import types as genai_types

def _build_gemini_contents(history, question):
    contents = []
    for msg in history[-20:]:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            genai_types.Content(role=role, parts=[genai_types.Part(text=msg["content"])])
        )
    contents.append(
        genai_types.Content(role="user", parts=[genai_types.Part(text=question + _LANG_HINT)])
    )
    return contents
```

### Three Execution Paths

Depending on the situation, Gemini uses one of three paths:

**Path 1: Conversational**
```python
response = client.models.generate_content_stream(
    model=model_name,
    config=genai_types.GenerateContentConfig(
        system_instruction=CONVERSATIONAL_SYSTEM_PROMPT,
    ),
    contents=contents,
)
```
Simple — just the system prompt + question, no document context.

**Path 2: Cached (using Gemini Context Cache)**
```python
response = client.models.generate_content_stream(
    model=model_name,
    config=genai_types.GenerateContentConfig(
        system_instruction=build_document_instruction(doc_config),
        cached_content=cache_name,   # e.g., "cachedContents/abc123"
    ),
    contents=contents,
)
```
The document is already stored in Gemini's cache from the upload step. The `system_instruction` contains only the rules (not the document text). The cached document is automatically injected by Gemini — you pay only the cheap "cache read" rate.

**Path 3: Inline (full document in prompt)**
```python
system_prompt = build_document_prompt(doc_text, doc_config, question)
response = client.models.generate_content_stream(
    model=model_name,
    config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
    contents=contents,
)
```
The full document is included in the system prompt each time. More expensive than caching but works without a cache.

### Gemini Context Caching

Context caching lets you upload a document once to Gemini's servers and reuse it for 1 hour without sending it again. Each request that uses the cached document pays a cheaper "cache read" rate instead of the full "input" rate — roughly 75% cheaper.

**Creating a cache (at document upload time):**

```python
def create_gemini_cache(doc_text: str, model_name: str) -> str:
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    cache = client.caches.create(
        model=model_name,
        config=genai_types.CreateCachedContentConfig(
            contents=[genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=doc_text)]
            )],
            ttl="3600s",  # 1-hour time-to-live
        )
    )
    return cache.name  # stored in Document.gemini_cache_name
```

**Error handling:**

Gemini caches can become invalid in two ways:

1. **Expired (403 error)** — the 1-hour TTL passed. DocChat catches `GeminiCacheExpiredError`, deletes the old cache, creates a new one, and retries transparently. The user never sees an error.

2. **Model mismatch (`INVALID_ARGUMENT`)** — the admin changed the Gemini model after the cache was created. The old cache was created for `gemini-2.0-flash` but the config now says `gemini-1.5-pro`. Same fix: delete and recreate.

**Deleting a cache (when document is deleted):**

```python
def delete_gemini_cache(cache_name: str):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    client.caches.delete(name=cache_name)
```

### Token Counting

```python
# After streaming completes, from usage_metadata
input_tokens  = response.usage_metadata.prompt_token_count
output_tokens = response.usage_metadata.candidates_token_count
cached_tokens = response.usage_metadata.cached_content_token_count
```

### Model Fallback on 503 Errors

If Gemini returns a 503 (service unavailable — happens during high load), DocChat automatically retries with backup models:

```python
_GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash"]
```

It tries the fallbacks in order. If all fail, it raises `GeminiUnavailableError` which the view layer catches and reports to the user.

---

## 5. Sarvam AI Provider (`chat/providers/sarvam.py`)

Sarvam AI is an Indian AI company specialising in Indic languages. Their `sarvam-m` model has excellent Hindi and Gujarati quality.

### The Token Budget Constraint

Sarvam's models have a **7,168 token total input limit**. This is quite small — approximately 5,000 words. DocChat enforces a hard limit:

```python
MAX_CONTEXT_CHARS = 9_000  # ~2,250 tokens, leaving room for history and question
```

If the document context is longer than 9,000 characters, it is truncated before being sent.

### Non-Streaming

Unlike Ollama and Gemini, the Sarvam Python SDK does not support true streaming — it returns the full response at once. DocChat `yield`s the entire response as a single token, so the user sees the answer appear all at once rather than word-by-word.

```python
def ask_streaming_sarvam(question, history, context, cfg, usage_out):
    messages = _build_messages(question, history, context, cfg, is_conversational(question))

    response = client.chat.completions(
        model=cfg.sarvam_model,
        messages=messages,
    )

    content = _extract_content(response)
    content = strip_citation_phrases(content)  # remove "According to the document..."
    yield content                              # all at once
```

### Why `strip_citation_phrases` for Sarvam

Sarvam AI tends to produce more verbose citation phrases ("As mentioned in the document...") than other models. The scrubber removes these for cleaner output.

---

## 6. The Language Hint

Every non-conversational question has this appended before being sent to the LLM:

```python
_LANG_HINT = "\n\n[Respond in the same language as this question.]"
```

Why is this needed even though the system prompt already says "answer in the same language as the question"?

LLMs are trained on mostly English text. Without a strong signal at the user message level, some models "forget" the language instruction from the system prompt and default to English. The `_LANG_HINT` acts as a second, immediate reminder right next to the question.

---

## 7. Adding a New LLM Provider

Follow these steps to add a new provider (e.g., "openai"):

**Step 1:** Add to `PROVIDER_CHOICES` in `chat/models.py`:

```python
PROVIDER_CHOICES = [
    ("ollama", "Ollama (Local)"),
    ("gemini", "Gemini (Google)"),
    ("sarvam", "Sarvam AI"),
    ("openai", "OpenAI"),       # ← add this
]
```

**Step 2:** Add a model field to `LLMConfig`:

```python
openai_model = models.CharField(max_length=100, default="gpt-4o")
```

**Step 3:** Run migrations:

```bash
python manage.py makemigrations chat
python manage.py migrate
```

**Step 4:** Create `chat/providers/openai.py`:

```python
from .utils import build_document_prompt, is_conversational, CONVERSATIONAL_SYSTEM_PROMPT, _LANG_HINT

def ask_streaming_openai(question, history, context, cfg, usage_out):
    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    system = CONVERSATIONAL_SYSTEM_PROMPT if is_conversational(question) else build_document_prompt(context, ...)
    messages = [{"role": "system", "content": system}]
    for msg in history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": question + _LANG_HINT})

    stream = client.chat.completions.create(model=cfg.openai_model, messages=messages, stream=True)
    for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        if token:
            yield token
```

**Step 5:** Add the dispatch in `chat/pipeline.py`:

```python
elif cfg.provider == "openai":
    from .providers.openai import ask_streaming_openai
    yield from ask_streaming_openai(question, history, context, cfg, usage_out)
```

**Step 6:** Register the model field in `chat/admin.py` (add `openai_model` to the `LLMConfigAdmin` fieldsets).

**Step 7:** Add `OPENAI_API_KEY` to `dochat/settings.py` and `.env`.

---

## 8. Testing Providers

**Switch the provider:**
1. Go to `http://127.0.0.1:8000/admin/`
2. Click "LLM Configuration"
3. Change "Provider" dropdown
4. Save

**Ask a question and check `app.log`:**

```
LLM stream start | provider=gemini | model=gemini-2.0-flash | q='What are the fees?'
LLM stream done  | provider=gemini | model=gemini-2.0-flash | tokens=1234/89 | cost=₹0.0023 | time=1.8s
```

**For Ollama specifically:** Ensure `ollama serve` is running in another terminal. If not, you will see a connection refused error in the logs.

---

## What to Do Next

Read [File 08 — API Endpoints & Views](08_api_endpoints_and_views.md) to understand how all these provider calls are wired up to REST endpoints and how SSE streaming is implemented in Django.

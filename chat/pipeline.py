import json
import logging
import os
import tempfile
import time

import ollama
import pytesseract
from google import genai
from google.genai import types as genai_types
from django.conf import settings
from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image
from docling.document_converter import DocumentConverter
from rank_bm25 import BM25Okapi

logger = logging.getLogger("chat.pipeline")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a helpful assistant. The following is extracted markdown text from a document.\n"
    "Use ONLY this context to answer the user's questions.\n"
    "If the answer cannot be found in the context, say so clearly.\n\n"
    "## Document Context (Markdown)\n\n"
    "{markdown_text}"
)


# ── OCR backends ───────────────────────────────────────────────────────────────

def _ocr_page_docling(image_path: str, converter: DocumentConverter) -> str:
    t0 = time.perf_counter()
    result = converter.convert(image_path)
    text = result.document.export_to_markdown()
    logger.debug("Docling OCR: %.2fs, %d chars", time.perf_counter() - t0, len(text))
    return text


def _ocr_page_tesseract(image_path: str) -> str:
    t0 = time.perf_counter()
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, lang="guj+eng")
    logger.debug("Tesseract OCR: %.2fs, %d chars", time.perf_counter() - t0, len(text))
    return text


def _ocr_page_gemini_vision(image_path: str, client, model_name: str) -> str:
    t0 = time.perf_counter()
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    contents = [
        genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        genai_types.Part(
            text=(
                "Extract all text from this image exactly as it appears. "
                "Preserve the original language (Gujarati, English, or mixed). "
                "Return only the extracted text, no commentary."
            )
        ),
    ]
    response = client.models.generate_content(model=model_name, contents=contents)
    text = response.text or ""
    logger.debug("Gemini Vision OCR (model=%s): %.2fs, %d chars", model_name, time.perf_counter() - t0, len(text))
    return text


def convert_to_markdown(input_path: str) -> tuple[str, dict]:
    """
    Convert a PDF or image file to text using the configured OCR engine.

    Returns:
        (combined_text, pages_data) where pages_data is a dict:
        {
            "source_file": "<filename>",
            "total_pages": N,
            "pages": [{"page": 1, "markdown": "..."}, ...]
        }
    """
    from .models import LLMConfig
    config = LLMConfig.get_active()

    ext = Path(input_path).suffix.lower()
    source_name = Path(input_path).name
    page_entries: list[dict] = []

    logger.info(
        "OCR start | file=%s | engine=%s", source_name, config.ocr_engine
    )
    ocr_total_start = time.perf_counter()

    # Initialise engine-specific resources once
    converter = DocumentConverter() if config.ocr_engine == "docling" else None
    gemini_client = (
        genai.Client(api_key=settings.GEMINI_API_KEY)
        if config.ocr_engine == "gemini_vision" else None
    )

    def _ocr(image_path: str) -> str:
        if config.ocr_engine == "tesseract":
            return _ocr_page_tesseract(image_path)
        elif config.ocr_engine == "gemini_vision":
            return _ocr_page_gemini_vision(image_path, gemini_client, config.gemini_model)
        else:  # docling (default)
            return _ocr_page_docling(image_path, converter)

    if ext == ".pdf":
        images = convert_from_path(input_path, dpi=200)
        logger.info("PDF rendered to %d page(s)", len(images))
        for page_num, page_img in enumerate(images, start=1):
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                page_img.save(tmp_path, "PNG")
            try:
                page_start = time.perf_counter()
                md = _ocr(tmp_path)
                logger.info(
                    "  Page %d/%d OCR done: %.2fs, %d chars",
                    page_num, len(images), time.perf_counter() - page_start, len(md),
                )
                page_entries.append({"page": page_num, "markdown": md})
            finally:
                os.unlink(tmp_path)

        combined = "\n\n---\n\n".join(
            f"<!-- Page {e['page']} -->\n\n{e['markdown']}" for e in page_entries
        )

    elif ext in IMAGE_EXTENSIONS:
        md = _ocr(input_path)
        page_entries.append({"page": 1, "markdown": md})
        combined = md

    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Supported: .pdf, {', '.join(sorted(IMAGE_EXTENSIONS))}"
        )

    total_chars = len(combined)
    logger.info(
        "OCR complete | engine=%s | pages=%d | total_chars=%d | total_time=%.2fs",
        config.ocr_engine, len(page_entries), total_chars,
        time.perf_counter() - ocr_total_start,
    )

    pages_data = {
        "source_file": source_name,
        "total_pages": len(page_entries),
        "pages": page_entries,
    }
    return combined, pages_data


# ── Gemini context cache ───────────────────────────────────────────────────────

def create_gemini_cache(markdown_text: str, model_name: str) -> str | None:
    """
    Create a Gemini context cache for the document.
    Returns the cache name (e.g. 'cachedContents/abc123') or None on failure.

    IMPORTANT: the Gemini Caching API only caches `contents`, NOT `system_instruction`.
    The document is therefore placed as a user-role Content object in `contents`.
    The short behavioural instruction goes in `system_instruction` (uncached, cheap).
    """
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        cache = client.caches.create(
            model=model_name,
            config=genai_types.CreateCachedContentConfig(
                system_instruction=(
                    "You are a helpful assistant. "
                    "Use ONLY the provided document context to answer the user's questions. "
                    "If the answer cannot be found in the context, say so clearly."
                ),
                contents=[
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(
                            text="## Document Context (Markdown)\n\n" + markdown_text
                        )],
                    )
                ],
                ttl="3600s",  # 1-hour TTL — matches a typical session
            ),
        )
        logger.info("Gemini cache created | name=%s | model=%s | chars=%d",
                    cache.name, model_name, len(markdown_text))
        return cache.name
    except Exception as exc:
        logger.warning("Gemini cache creation skipped | model=%s | error=%s", model_name, exc)
        return None


def delete_gemini_cache(cache_name: str) -> None:
    """Delete a Gemini context cache. Silently ignores errors (already expired, etc.)."""
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        client.caches.delete(name=cache_name)
        logger.info("Gemini cache deleted | name=%s", cache_name)
    except Exception as exc:
        logger.debug("Gemini cache deletion skipped (may have expired): %s", exc)


# ── RAG helpers (BM25 + multilingual embeddings) ──────────────────────────────

# Module-level cache for the sentence-transformers model (loaded once on first use)
_st_model = None

def _get_st_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformers model paraphrase-multilingual-MiniLM-L12-v2 …")
        _st_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        logger.info("Sentence-transformers model loaded.")
    return _st_model


def _embed_local(texts: list[str]) -> list[list[float]]:
    """Embed texts using a local multilingual sentence-transformers model."""
    model = _get_st_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    """Embed texts using the Gemini multilingual embedding API."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    embeddings = []
    for text in texts:
        response = client.models.embed_content(
            model="text-multilingual-embedding-002",
            contents=text,
        )
        embeddings.append(list(response.embeddings[0].values))
    return embeddings


def _cosine_scores(query_emb: list[float], chunk_embs: list[list[float]]) -> list[float]:
    """Compute cosine similarity scores (assumes embeddings are L2-normalised)."""
    import numpy as np
    q = np.array(query_emb, dtype=np.float32)
    C = np.array(chunk_embs, dtype=np.float32)
    return (C @ q).tolist()   # dot product of normalised vectors = cosine similarity


def build_rag_chunks(pages_data: dict, embedding_method: str) -> list[dict]:
    """
    Build page-level chunks and optionally embed them.

    Returns a list of dicts:  {"page": N, "text": "...", "embedding": [...]}
    The "embedding" key is absent when embedding_method == "bm25".
    """
    chunks: list[dict] = []
    for page in pages_data.get("pages", []):
        text = page.get("markdown", "").strip()
        if text:
            chunks.append({"page": page["page"], "text": text})

    if not chunks:
        return chunks

    if embedding_method == "multilingual_local":
        t0 = time.perf_counter()
        embeddings = _embed_local([c["text"] for c in chunks])
        for chunk, emb in zip(chunks, embeddings):
            chunk["embedding"] = emb
        logger.info(
            "Local embeddings built | chunks=%d | time=%.2fs", len(chunks), time.perf_counter() - t0
        )
    elif embedding_method == "gemini_embedding":
        t0 = time.perf_counter()
        embeddings = _embed_gemini([c["text"] for c in chunks])
        for chunk, emb in zip(chunks, embeddings):
            chunk["embedding"] = emb
        logger.info(
            "Gemini embeddings built | chunks=%d | time=%.2fs", len(chunks), time.perf_counter() - t0
        )
    else:
        logger.info("BM25 mode — skipping embedding | chunks=%d", len(chunks))

    return chunks


def retrieve_relevant_context(question: str, chunks_path: str,
                              embedding_method: str = "bm25", top_k: int = 5) -> str:
    """
    Load chunks from disk and return the top-k most relevant pages.

    Uses BM25 keyword matching when embedding_method == "bm25", or cosine
    similarity over stored embeddings for the multilingual options.
    Falls back to BM25 if embeddings are absent (e.g. method changed after upload).
    """
    with open(chunks_path, encoding="utf-8") as f:
        chunks: list[dict] = json.load(f)

    if not chunks:
        return ""

    has_embeddings = "embedding" in chunks[0]

    if embedding_method != "bm25" and has_embeddings:
        # ── Embedding-based retrieval ──────────────────────────────────────────
        if embedding_method == "multilingual_local":
            q_emb = _embed_local([question])[0]
        else:
            q_emb = _embed_gemini([question])[0]
        scores = _cosine_scores(q_emb, [c["embedding"] for c in chunks])
    else:
        # ── BM25 keyword retrieval ─────────────────────────────────────────────
        if embedding_method != "bm25":
            logger.warning("Embeddings missing in chunks — falling back to BM25")
        tokenized = [c["text"].lower().split() for c in chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(question.lower().split())

    ranked   = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    selected = sorted(ranked)  # restore page order

    result = "\n\n---\n\n".join(
        f"<!-- Page {chunks[i]['page']} -->\n\n{chunks[i]['text']}" for i in selected
    )
    logger.info(
        "RAG retrieval | method=%s | q_chars=%d | top_k=%d | selected_pages=%s",
        "embedding" if (embedding_method != "bm25" and has_embeddings) else "bm25",
        len(question), top_k, [chunks[i]["page"] for i in selected],
    )
    return result


# ── Ollama helpers ─────────────────────────────────────────────────────────────

def _build_ollama_messages(question: str, history: list, markdown_text: str) -> list:
    system_msg = {
        "role": "system",
        "content": _SYSTEM_PROMPT_TEMPLATE.format(markdown_text=markdown_text),
    }
    # Trim history to last 20 turns (40 messages) to stay within context window
    trimmed_history = history[-40:]
    return [system_msg] + trimmed_history + [{"role": "user", "content": question}]


def _ask_streaming_ollama(question: str, history: list, markdown_text: str, model_name: str,
                          usage_out: dict | None = None):
    logger.info(
        "LLM stream start | provider=ollama | model=%s | history_turns=%d | q_chars=%d",
        model_name, len(history) // 2, len(question),
    )
    t0 = time.perf_counter()
    output_chars = 0
    messages = _build_ollama_messages(question, history, markdown_text)
    stream = ollama.chat(model=model_name, messages=messages, stream=True)
    last_chunk = None
    try:
        for chunk in stream:
            last_chunk = chunk
            token = chunk.get("message", {}).get("content", "")
            if token:
                output_chars += len(token)
                yield token
    finally:
        if usage_out is not None:
            input_tokens  = (last_chunk or {}).get("prompt_eval_count", 0)
            output_tokens = (last_chunk or {}).get("eval_count", 0)
            if input_tokens == 0 and output_tokens == 0:
                input_chars = sum(len(m.get("content", "")) for m in messages)
                input_tokens  = max(1, input_chars // 4)
                output_tokens = max(1, output_chars // 4)
                usage_out["estimated"] = True
            usage_out["input_tokens"]  = input_tokens
            usage_out["output_tokens"] = output_tokens
        logger.info(
            "LLM stream done  | provider=ollama | model=%s | response_chars=%d | time=%.2fs",
            model_name, output_chars, time.perf_counter() - t0,
        )


def _ask_ollama(question: str, history: list, markdown_text: str, model_name: str) -> tuple[str, float]:
    logger.info("LLM ask | provider=ollama | model=%s", model_name)
    messages = _build_ollama_messages(question, history, markdown_text)
    t0 = time.perf_counter()
    response = ollama.chat(model=model_name, messages=messages)
    elapsed = time.perf_counter() - t0
    answer = response["message"]["content"]
    logger.info("LLM done | provider=ollama | model=%s | response_chars=%d | time=%.2fs", model_name, len(answer), elapsed)
    return answer, elapsed


# ── Gemini helpers ─────────────────────────────────────────────────────────────

def _build_gemini_contents(question: str, history: list) -> list:
    """Convert Ollama-style message history to Gemini Content objects."""
    contents = []
    for m in history[-40:]:
        role = "model" if m["role"] == "assistant" else m["role"]
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=m["content"])]))
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=question)]))
    return contents


def _ask_streaming_gemini(question: str, history: list, markdown_text: str, model_name: str,
                          usage_out: dict | None = None, cache_name: str | None = None):
    cached = cache_name is not None
    logger.info(
        "LLM stream start | provider=gemini | model=%s | history_turns=%d | q_chars=%d | cached=%s",
        model_name, len(history) // 2, len(question), cached,
    )
    t0 = time.perf_counter()
    output_chars = 0
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    contents = _build_gemini_contents(question, history)

    if cached:
        # Document context lives in the Gemini cache — no system_instruction needed
        llm_config = genai_types.GenerateContentConfig(cached_content=cache_name)
    else:
        # Full-context or RAG path — send doc (or retrieved chunks) in system instruction
        system = _SYSTEM_PROMPT_TEMPLATE.format(markdown_text=markdown_text)
        llm_config = genai_types.GenerateContentConfig(system_instruction=system)

    last_chunk = None
    try:
        for chunk in client.models.generate_content_stream(
            model=model_name, contents=contents, config=llm_config
        ):
            last_chunk = chunk
            token = chunk.text
            if token:
                output_chars += len(token)
                yield token
    except Exception as exc:
        # Cache may have expired mid-session — fall back to full context and re-raise
        # so the caller can decide whether to retry
        if cached:
            logger.warning("Gemini cached stream failed (cache may have expired): %s", exc)
        raise
    finally:
        if usage_out is not None and last_chunk is not None:
            meta = getattr(last_chunk, "usage_metadata", None)
            if meta:
                usage_out["input_tokens"]  = meta.prompt_token_count or 0
                usage_out["output_tokens"] = meta.candidates_token_count or 0
        logger.info(
            "LLM stream done  | provider=gemini | model=%s | response_chars=%d | time=%.2fs | cached=%s",
            model_name, output_chars, time.perf_counter() - t0, cached,
        )


def _ask_gemini(question: str, history: list, markdown_text: str, model_name: str) -> tuple[str, float]:
    logger.info("LLM ask | provider=gemini | model=%s", model_name)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    system = _SYSTEM_PROMPT_TEMPLATE.format(markdown_text=markdown_text)
    config = genai_types.GenerateContentConfig(system_instruction=system)
    contents = _build_gemini_contents(question, history)
    t0 = time.perf_counter()
    response = client.models.generate_content(model=model_name, contents=contents, config=config)
    elapsed = time.perf_counter() - t0
    answer = response.text
    logger.info("LLM done | provider=gemini | model=%s | response_chars=%d | time=%.2fs", model_name, len(answer), elapsed)
    return answer, elapsed


# ── Public API ─────────────────────────────────────────────────────────────────

def ask_streaming(question: str, history: list, markdown_text: str,
                  usage_out: dict | None = None,
                  gemini_cache_name: str | None = None):
    """
    Generator that yields string tokens from the active LLM's streaming response.
    Used by the /chat SSE route.

    Args:
        markdown_text:      Full document text (full-context mode) or pre-retrieved
                            BM25 chunks (RAG mode).  Ignored when gemini_cache_name
                            is set and the cache is still valid.
        usage_out:          Mutable dict populated with input_tokens / output_tokens
                            (and estimated=True for Ollama fallback).
        gemini_cache_name:  Gemini context cache name.  When provided, the document
                            context is served from the cache instead of the system
                            instruction, reducing costs ~4×.
    """
    from .models import LLMConfig
    config = LLMConfig.get_active()

    if config.provider == "gemini":
        yield from _ask_streaming_gemini(question, history, markdown_text, config.gemini_model,
                                         usage_out=usage_out, cache_name=gemini_cache_name)
    else:
        yield from _ask_streaming_ollama(question, history, markdown_text, config.ollama_model,
                                         usage_out=usage_out)


def ask(question: str, history: list, markdown_text: str) -> tuple[str, float]:
    """
    Non-streaming variant — returns (answer, elapsed_seconds).
    Kept for testing/debugging convenience.
    """
    from .models import LLMConfig
    config = LLMConfig.get_active()

    if config.provider == "gemini":
        return _ask_gemini(question, history, markdown_text, config.gemini_model)
    else:
        return _ask_ollama(question, history, markdown_text, config.ollama_model)

import json
import logging
import os
import tempfile
import time

import pytesseract
from django.conf import settings
from google import genai
from google.genai import types as genai_types
from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image
from docling.document_converter import DocumentConverter
from rank_bm25 import BM25Okapi

logger = logging.getLogger("chat.pipeline")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

# Re-export Gemini cache helpers so views.py import path stays unchanged
from .providers.gemini import create_gemini_cache, delete_gemini_cache, GeminiUnavailableError  # noqa: E402

# Ordered fallback models tried when the primary Gemini model returns 503 UNAVAILABLE.
# The primary model (from LLMConfig) is always tried first; these are fallbacks only.
_GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash"]


# ── OCR backends ───────────────────────────────────────────────────────────────

def _ocr_page_docling(image_path: str, converter: DocumentConverter) -> str:
    t0 = time.perf_counter()
    result = converter.convert(image_path)
    text = result.document.export_to_markdown()
    logger.debug("Docling OCR: %.2fs, %d chars", time.perf_counter() - t0, len(text))
    return text


_TESSERACT_LANG   = "hin+guj+eng"
_TESSERACT_CONFIG = "--oem 3 --psm 6"   # LSTM engine, assume uniform block of text


def _preprocess_for_tesseract(img: Image.Image) -> Image.Image:
    """Grayscale + contrast boost → sharper edges for Indic script recognition."""
    from PIL import ImageEnhance, ImageFilter
    img = img.convert("L")                          # greyscale
    img = ImageEnhance.Contrast(img).enhance(2.0)   # punch up contrast
    img = img.filter(ImageFilter.SHARPEN)           # crisp strokes
    return img


def _ocr_page_tesseract(image_path: str) -> str:
    t0 = time.perf_counter()
    img = Image.open(image_path)
    img = _preprocess_for_tesseract(img)
    text = pytesseract.image_to_string(img, lang=_TESSERACT_LANG, config=_TESSERACT_CONFIG)
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
                "Preserve the original language (Hindi, Gujarati, English, or any mix). "
                "Output Devanagari script for Hindi, Gujarati script for Gujarati. "
                "Return only the extracted text, no commentary."
            )
        ),
    ]
    response = client.models.generate_content(model=model_name, contents=contents)
    text = response.text or ""
    logger.debug("Gemini Vision OCR (model=%s): %.2fs, %d chars", model_name, time.perf_counter() - t0, len(text))
    return text


def _extract_text_pdfplumber(pdf_path: str) -> list[dict]:
    """
    Extract text directly from a digital PDF using pdfplumber — no image
    conversion, no model call, no OCR artifacts.
    Returns one dict per page: {"page": N, "markdown": "..."}
    """
    import pdfplumber
    page_entries = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_entries.append({"page": i, "markdown": text.strip()})
    return page_entries


def _has_text_layer(pdf_path: str, min_chars_per_page: int = 50) -> bool:
    """Return True when the PDF has a selectable text layer (digital PDF)."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return False
            total = sum(len(p.extract_text() or "") for p in pdf.pages)
            return total >= min_chars_per_page * len(pdf.pages)
    except Exception as exc:
        logger.debug("Text-layer detection failed, assuming scanned: %s", exc)
        return False


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

    # ── Resolve effective engine (auto mode picks based on content type) ──────
    effective_engine = config.ocr_engine
    if config.ocr_engine == "auto":
        if ext == ".pdf" and _has_text_layer(input_path):
            effective_engine = "docling"
            logger.info("Auto OCR: digital text layer detected → Docling")
        else:
            effective_engine = "tesseract"
            logger.info("Auto OCR: scanned/image document detected → Tesseract")

    # Higher DPI for Tesseract — Devanagari/Gujarati strokes need clarity.
    dpi = 300 if effective_engine == "tesseract" else 200

    logger.info("OCR start | file=%s | engine=%s (config=%s)",
                source_name, effective_engine, config.ocr_engine)
    ocr_total_start = time.perf_counter()

    # ── PDF direct text extraction (no image conversion, no OCR) ────────────
    if effective_engine == "pdftext":
        if ext == ".pdf":
            t0_pdf = time.perf_counter()
            page_entries = _extract_text_pdfplumber(input_path)
            combined = "\n\n---\n\n".join(
                f"<!-- Page {e['page']} -->\n\n{e['markdown']}" for e in page_entries
            )
            logger.info(
                "PDF-to-text complete | pages=%d | total_chars=%d | time=%.2fs",
                len(page_entries), len(combined), time.perf_counter() - t0_pdf,
            )
            pages_data = {
                "source_file": source_name,
                "total_pages": len(page_entries),
                "pages": page_entries,
            }
            return combined, pages_data
        else:
            logger.warning(
                "pdftext engine selected but input is an image (%s); falling back to Tesseract", ext,
            )
            effective_engine = "tesseract"

    converter = DocumentConverter() if effective_engine == "docling" else None
    gemini_client = (
        genai.Client(api_key=settings.GEMINI_API_KEY)
        if effective_engine == "gemini_vision" else None
    )

    def _ocr(image_path: str) -> str:
        if effective_engine == "tesseract":
            return _ocr_page_tesseract(image_path)
        elif effective_engine == "gemini_vision":
            return _ocr_page_gemini_vision(image_path, gemini_client, config.gemini_model)
        else:  # docling (default)
            return _ocr_page_docling(image_path, converter)

    if ext == ".pdf":
        images = convert_from_path(input_path, dpi=dpi)
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


# ── RAG helpers (BM25 + multilingual embeddings) ──────────────────────────────

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
    model = _get_st_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def _embed_gemini(texts: list[str]) -> list[list[float]]:
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
    import numpy as np
    q = np.array(query_emb, dtype=np.float32)
    C = np.array(chunk_embs, dtype=np.float32)
    return (C @ q).tolist()


# ── Qdrant vector store ────────────────────────────────────────────────────────

_qdrant_client = None

_EMBEDDING_DIMS = {
    "multilingual_local": 384,
    "gemini_embedding":   768,
    "bm25":               1,    # dummy — text stored in payload, BM25 in-memory
}


def get_qdrant_client():
    """Return a process-level singleton Qdrant client (embedded, local disk)."""
    global _qdrant_client
    if _qdrant_client is None:
        from django.conf import settings
        from qdrant_client import QdrantClient
        Path(settings.QDRANT_PATH).mkdir(parents=True, exist_ok=True)
        _qdrant_client = QdrantClient(path=str(settings.QDRANT_PATH))
        logger.info("Qdrant client initialised at %s", settings.QDRANT_PATH)
    return _qdrant_client


def store_rag_chunks_qdrant(chunks: list[dict], collection_name: str, embedding_method: str) -> None:
    """
    Upsert chunks into a Qdrant collection.
    - For vector methods: stores actual embedding vectors.
    - For BM25: stores a dummy [0.0] vector; text lives in payload only.
    """
    from qdrant_client.models import Distance, VectorParams, PointStruct

    client = get_qdrant_client()
    dim    = _EMBEDDING_DIMS.get(embedding_method, 384)

    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    points = [
        PointStruct(
            id=i,
            vector=chunk.get("embedding") or [0.0],
            payload={"page": chunk["page"], "text": chunk["text"]},
        )
        for i, chunk in enumerate(chunks)
    ]

    if points:
        client.upsert(collection_name=collection_name, points=points)

    logger.info(
        "Qdrant upsert | collection=%s | method=%s | points=%d",
        collection_name, embedding_method, len(points),
    )


def retrieve_relevant_context_qdrant(question: str, collection_name: str,
                                      embedding_method: str = "bm25", top_k: int = 5) -> str:
    """
    Retrieve the top-k most relevant chunks from Qdrant.
    - Vector methods: cosine similarity search.
    - BM25: fetch all points via scroll, compute BM25 in-memory.
    """
    client = get_qdrant_client()

    if embedding_method == "bm25":
        all_points, _ = client.scroll(
            collection_name=collection_name, with_payload=True, limit=10_000
        )
        chunks    = [{"page": p.payload["page"], "text": p.payload["text"]} for p in all_points]
        tokenized = [c["text"].lower().split() for c in chunks]
        scores    = BM25Okapi(tokenized).get_scores(question.lower().split())
        ranked    = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        selected  = sorted(ranked)
        result    = "\n\n---\n\n".join(
            f"<!-- Page {chunks[i]['page']} -->\n\n{chunks[i]['text']}" for i in selected
        )
        method_used = "bm25"
    else:
        q_emb = (
            _embed_local([question])[0]
            if embedding_method == "multilingual_local"
            else _embed_gemini([question])[0]
        )
        hits = client.query_points(
            collection_name=collection_name,
            query=q_emb,
            limit=top_k,
            with_payload=True,
        ).points
        hits.sort(key=lambda p: p.payload["page"])
        result      = "\n\n---\n\n".join(
            f"<!-- Page {p.payload['page']} -->\n\n{p.payload['text']}" for p in hits
        )
        method_used = "embedding"

    logger.info(
        "Qdrant RAG | method=%s | collection=%s | q_chars=%d | top_k=%d",
        method_used, collection_name, len(question), top_k,
    )
    return result


def split_text_into_pages(text: str, chunk_size: int = 1_000) -> dict:
    """
    Split plain pasted text into synthetic 'pages' for RAG embedding.

    Splits on paragraph boundaries (double newlines), merging paragraphs until
    a chunk reaches *chunk_size* characters.  Each resulting chunk becomes one
    page in the pages_data dict that the rest of the pipeline expects.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pages: list[str] = []
    current: list[str] = []
    size = 0

    for para in paragraphs:
        if size + len(para) > chunk_size and current:
            pages.append("\n\n".join(current))
            current, size = [para], len(para)
        else:
            current.append(para)
            size += len(para)

    if current:
        pages.append("\n\n".join(current))

    # Guarantee at least one page even for very short text
    if not pages:
        pages = [text]

    return {
        "total_pages": len(pages),
        "pages": [{"page": i + 1, "markdown": p} for i, p in enumerate(pages)],
    }


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
        logger.info("Local embeddings built | chunks=%d | time=%.2fs", len(chunks), time.perf_counter() - t0)
    elif embedding_method == "gemini_embedding":
        t0 = time.perf_counter()
        embeddings = _embed_gemini([c["text"] for c in chunks])
        for chunk, emb in zip(chunks, embeddings):
            chunk["embedding"] = emb
        logger.info("Gemini embeddings built | chunks=%d | time=%.2fs", len(chunks), time.perf_counter() - t0)
    else:
        logger.info("BM25 mode — skipping embedding | chunks=%d", len(chunks))

    return chunks


def retrieve_relevant_context(question: str, chunks_path: str,
                              embedding_method: str = "bm25", top_k: int = 5) -> str:
    """
    Load chunks from disk and return the top-k most relevant pages.
    """
    with open(chunks_path, encoding="utf-8") as f:
        chunks: list[dict] = json.load(f)

    if not chunks:
        return ""

    has_embeddings = "embedding" in chunks[0]

    if embedding_method != "bm25" and has_embeddings:
        if embedding_method == "multilingual_local":
            q_emb = _embed_local([question])[0]
        else:
            q_emb = _embed_gemini([question])[0]
        scores = _cosine_scores(q_emb, [c["embedding"] for c in chunks])
    else:
        if embedding_method != "bm25":
            logger.warning("Embeddings missing in chunks — falling back to BM25")
        tokenized = [c["text"].lower().split() for c in chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(question.lower().split())

    ranked   = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    selected = sorted(ranked)

    result = "\n\n---\n\n".join(
        f"<!-- Page {chunks[i]['page']} -->\n\n{chunks[i]['text']}" for i in selected
    )
    logger.info(
        "RAG retrieval | method=%s | q_chars=%d | top_k=%d | selected_pages=%s",
        "embedding" if (embedding_method != "bm25" and has_embeddings) else "bm25",
        len(question), top_k, [chunks[i]["page"] for i in selected],
    )
    return result


# ── Public API ─────────────────────────────────────────────────────────────────

def ask_streaming(question: str, history: list, markdown_text: str,
                  usage_out: dict or None = None,
                  gemini_cache_name: str or None = None):
    """
    Generator that yields string tokens from the active LLM's streaming response.
    Used by the /chat SSE route.
    """
    from .models import LLMConfig, DocumentConfig
    from .providers.gemini import _ask_streaming_gemini
    from .providers.ollama import _ask_streaming_ollama
    from .providers.sarvam import _ask_streaming_sarvam

    config = LLMConfig.get_active()
    fallback_contact = DocumentConfig.get_active().fallback_contact

    if config.provider == "gemini":
        # Build ordered list: primary model first, then fallbacks (skipping duplicates)
        models_to_try = [config.gemini_model] + [
            m for m in _GEMINI_FALLBACK_MODELS if m != config.gemini_model
        ]
        for attempt, model in enumerate(models_to_try):
            # Only use cache for the primary model — cache is model-specific
            cache = gemini_cache_name if attempt == 0 else None
            try:
                yield from _ask_streaming_gemini(
                    question, history, markdown_text, model,
                    usage_out=usage_out, cache_name=cache,
                    fallback_contact=fallback_contact,
                )
                break  # success — stop trying fallbacks
            except GeminiUnavailableError:
                if attempt < len(models_to_try) - 1:
                    next_model = models_to_try[attempt + 1]
                    logger.warning(
                        "Gemini model %s unavailable, retrying with %s", model, next_model
                    )
                else:
                    logger.error("All Gemini fallback models exhausted — raising 503")
                    raise
    elif config.provider == "sarvam":
        yield from _ask_streaming_sarvam(
            question, history, markdown_text, config.sarvam_model,
            usage_out=usage_out, fallback_contact=fallback_contact,
        )
    else:  # ollama
        yield from _ask_streaming_ollama(
            question, history, markdown_text, config.ollama_model,
            usage_out=usage_out, fallback_contact=fallback_contact,
        )


def ask(question: str, history: list, markdown_text: str) -> tuple[str, float]:
    """Non-streaming variant — returns (answer, elapsed_seconds)."""
    from .models import LLMConfig
    from .providers.gemini import _ask_gemini
    from .providers.ollama import _ask_ollama
    from .providers.sarvam import _ask_sarvam

    config = LLMConfig.get_active()

    if config.provider == "gemini":
        return _ask_gemini(question, history, markdown_text, config.gemini_model)
    elif config.provider == "sarvam":
        return _ask_sarvam(question, history, markdown_text, config.sarvam_model)
    else:  # ollama
        return _ask_ollama(question, history, markdown_text, config.ollama_model)


def ask_raw(prompt: str) -> str:
    """
    Single non-streaming LLM call with a plain prompt — no document context, no history.
    Used by the agent layer for tool-iteration reasoning and memory compression.
    """
    answer, _ = ask(prompt, [], "")
    return answer

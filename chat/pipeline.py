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
_GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash"]


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


_GEMINI_VISION_OCR_PROMPT = """\
You are extracting content from a document page image to build a knowledge base for a question-answering system.
Your output will be used directly as LLM context — it must be clean, structured, and information-dense.

## LANGUAGE RULES
- Preserve ALL text in its original script: Gujarati (Unicode), English, or mixed Gujarati+English.
- Do NOT translate, transliterate, or paraphrase any text.
- If a word is in Gujarati script, output it in Gujarati script. Same for English.

## STRUCTURE RULES
- Use ## for main section headings, ### for sub-headings.
- Use - for bullet lists and numbered lists where they appear in the document.
- Use **label** (bold) for field names or important terms.

## TABLE RULES  ← most important
Tables in PDFs often contain course details, fees, seat counts, eligibility criteria etc.
Do NOT output raw table borders or grid characters.
Convert every table into labeled plain-text rows like this:

  If the table is a course list with columns (Code, Course Name, Seats, Eligibility):
    CB4. **M.C.A.** | બેઠક: 60 | લાયકાત: કોઈ પણ વિષયમાં સ્નાતક 50% સાથે, ધોરણ 12 અથવા સ્નાતકમાં ગણિત જરૂરી
    CB1. **B.C.A.** | બેઠક: 60 | લાયકાત: ધોરણ 12 (કોઈ પણ પ્રવાહ) 40% સાથે

  General rule: use each column header as a label for that row's value.
  Each table row → one line of labeled text.
  For spanning cells, write the value once on its first row only.

## IGNORE COMPLETELY
- Page numbers (e.g. "Page 1", "1 / 12", "- 3 -")
- Repeated page headers / footers (university name banner, document title at top/bottom)
- Watermarks, logos, decorative lines, borders, stamps
- Any purely decorative or structural element that carries no information

## OUTPUT FORMAT
Return only the structured content — no commentary, no "Here is the extracted text:" preamble.
Start directly with the content.
"""


def _ocr_page_gemini_vision(image_path: str, client, model_name: str) -> str:
    t0 = time.perf_counter()
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    contents = [
        genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        genai_types.Part(text=_GEMINI_VISION_OCR_PROMPT),
    ]
    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=genai_types.GenerateContentConfig(max_output_tokens=8192),
    )
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

import re as _re

# Collapse dotted abbreviations so "M.C.A." and "MCA" tokenise identically.
# Matches 2+ single letters separated by dots, with an optional trailing dot.
# Examples: M.C.A. → MCA   B.C.A → BCA   M.A. → MA
_ABBR_RE = _re.compile(r'\b([A-Za-z]\.){2,}[A-Za-z]?\b')


def _normalize_abbr(text: str) -> str:
    """Strip dots from dotted abbreviations to unify 'M.C.A.' with 'MCA'."""
    return _ABBR_RE.sub(lambda m: m.group(0).replace(".", ""), text)


# Strip ASCII punctuation only — preserves Unicode combining marks (Gujarati ā/ā,
# Hindi maatras, virama, etc.) which Python's \w does NOT match but are part of words.
# Matches any non-whitespace ASCII char that is not a-z / 0-9 / underscore.
_PUNCT_RE = _re.compile(r'[^\w\s\u0080-\uFFFF]', _re.UNICODE)


def _tokenize_bm25(text: str) -> list[str]:
    """
    Normalize abbreviations → lowercase → strip ASCII punctuation → split.
    Ensures 'brs?' and 'B.R.S.' both tokenise to ['brs'].
    Gujarati/Hindi Unicode script (including combining vowel signs) is preserved.
    """
    normalized = _normalize_abbr(text).lower()
    cleaned = _PUNCT_RE.sub(" ", normalized)
    return cleaned.split()

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
            model="gemini-embedding-001",
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
    "gemini_embedding":   3072,  # gemini-embedding-001 output dimension
    "bm25":               1,     # dummy — text stored in payload, BM25 in-memory
}


def get_qdrant_client():
    """Return a process-level singleton Qdrant client (Qdrant server)."""
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(url="http://localhost:6333")
        logger.info("Qdrant client initialised at http://localhost:6333")
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


def _retrieve_hybrid_qdrant(question: str, collection_name: str,
                             embedding_method: str, top_k: int) -> str:
    """
    Hybrid BM25 + vector retrieval with Reciprocal Rank Fusion (RRF).

    Runs BM25 over all chunks (in-memory) and vector search (Qdrant), then
    merges the two ranked lists using RRF so pages that score well in both
    methods are surfaced first.  Returns the same formatted string as
    retrieve_relevant_context_qdrant.
    """
    client = get_qdrant_client()
    K = 60  # RRF constant — standard value, balances early vs. late ranks

    # 1. Scroll all points (payload only, no stored vectors needed)
    all_points, _ = client.scroll(
        collection_name=collection_name, limit=10_000,
        with_payload=True, with_vectors=False,
    )
    if not all_points:
        return ""

    page_texts = {p.payload["page"]: p.payload["text"] for p in all_points}

    # 2. BM25 ranking over every chunk
    corpus = [_tokenize_bm25(p.payload["text"]) for p in all_points]
    bm25 = BM25Okapi(corpus)
    q_tokens = _tokenize_bm25(question)
    bm25_scores = bm25.get_scores(q_tokens)
    bm25_ranked = sorted(range(len(all_points)), key=lambda i: bm25_scores[i], reverse=True)
    bm25_rank = {all_points[i].payload["page"]: rank for rank, i in enumerate(bm25_ranked)}

    # 3. Vector ranking via Qdrant (retrieve 4× as many candidates for RRF pool)
    norm_q = _normalize_abbr(question)
    if embedding_method == "gemini_embedding":
        q_emb = _embed_gemini([norm_q])[0]
    else:
        q_emb = _embed_local([norm_q])[0]
    vec_limit = min(top_k * 4, len(all_points))
    vec_hits = client.query_points(
        collection_name=collection_name,
        query=q_emb,
        limit=vec_limit,
        with_payload=True,
    ).points
    vec_rank = {h.payload["page"]: rank for rank, h in enumerate(vec_hits)}

    # 4. RRF fusion: score(page) = 1/(K + bm25_rank) + 1/(K + vec_rank)
    #    Pages absent from the vector pool get a heavy penalty rank.
    rrf: dict[int, float] = {}
    for page in page_texts:
        r_b = bm25_rank.get(page, len(all_points))
        r_v = vec_rank.get(page, vec_limit)
        rrf[page] = 1.0 / (K + r_b) + 1.0 / (K + r_v)

    top_pages = sorted(rrf, key=rrf.get, reverse=True)[:top_k]
    top_pages.sort()  # reading order within the document

    logger.info(
        "Hybrid RAG (RRF) | collection=%s | q_chars=%d | top_k=%d | pages=%s",
        collection_name, len(question), top_k, top_pages,
    )
    return "\n\n---\n\n".join(
        f"<!-- Page {p} -->\n\n{page_texts[p]}" for p in top_pages
    )


def retrieve_relevant_context_qdrant(question: str, collection_name: str,
                                      embedding_method: str = "bm25", top_k: int = 5) -> str:
    """
    Retrieve the top-k most relevant chunks from Qdrant.
    - Vector methods (gemini_embedding, multilingual_local): hybrid BM25+vector with RRF.
    - BM25: fetch all points via scroll, compute BM25 in-memory only.
    """
    # Hybrid search for any embedding-based method
    if embedding_method in ("gemini_embedding", "multilingual_local"):
        return _retrieve_hybrid_qdrant(question, collection_name, embedding_method, top_k)

    client = get_qdrant_client()

    if embedding_method == "bm25":
        all_points, _ = client.scroll(
            collection_name=collection_name, with_payload=True, limit=10_000
        )
        chunks    = [{"page": p.payload["page"], "text": p.payload["text"]} for p in all_points]
        tokenized = [_tokenize_bm25(c["text"]) for c in chunks]
        scores    = BM25Okapi(tokenized).get_scores(_tokenize_bm25(question))
        ranked    = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        selected  = sorted(ranked)
        result    = "\n\n---\n\n".join(
            f"<!-- Page {chunks[i]['page']} -->\n\n{chunks[i]['text']}" for i in selected
        )
        method_used = "bm25"
    else:
        norm_q = _normalize_abbr(question)
        q_emb = (
            _embed_local([norm_q])[0]
            if embedding_method == "multilingual_local"
            else _embed_gemini([norm_q])[0]
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


def split_text_into_pages(
    text: str,
    chunk_size: int = 1_000,
    chunk_overlap: int = 100,
) -> dict:
    """
    Split plain pasted text into synthetic 'pages' for RAG embedding.

    Uses LangChain's RecursiveCharacterTextSplitter with a hierarchy of
    separators (paragraph → line → Gujarati danda → sentence → word → char)
    so it always produces correctly-sized chunks regardless of whether the
    input has double newlines or not.

    chunk_size and chunk_overlap are in characters (Unicode-safe for Gujarati).
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "।", ".", " ", ""],
    )
    pages = splitter.split_text(text)

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
        # Normalize abbreviations before embedding so "mca" queries match "M.C.A." chunks.
        # The original text is kept in chunk["text"] for display; only the embedding uses norm.
        norm_texts = [_normalize_abbr(c["text"]) for c in chunks]
        embeddings = _embed_local(norm_texts)
        for chunk, emb in zip(chunks, embeddings):
            chunk["embedding"] = emb
        logger.info("Local embeddings built | chunks=%d | time=%.2fs", len(chunks), time.perf_counter() - t0)
    elif embedding_method == "gemini_embedding":
        t0 = time.perf_counter()
        norm_texts = [_normalize_abbr(c["text"]) for c in chunks]
        embeddings = _embed_gemini(norm_texts)
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
        norm_q = _normalize_abbr(question)
        if embedding_method == "multilingual_local":
            q_emb = _embed_local([norm_q])[0]
        else:
            q_emb = _embed_gemini([norm_q])[0]
        scores = _cosine_scores(q_emb, [c["embedding"] for c in chunks])
    else:
        if embedding_method != "bm25":
            logger.warning("Embeddings missing in chunks — falling back to BM25")
        tokenized = [_tokenize_bm25(c["text"]) for c in chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(_tokenize_bm25(question))

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


# ── Liked-QA Qdrant cache ──────────────────────────────────────────────────────

_LIKED_SUFFIX     = "_liked"
_LIKED_VECTOR_DIM = 384   # sentence-transformers multilingual-local, always


def get_question_embedding(question: str) -> "np.ndarray":
    """
    Compute a sentence-transformer embedding for *question*.

    Always uses multilingual-local (384-dim, L2-normalised) regardless of the
    RAG embedding setting so the session cache and liked-QA lookup share a
    single, consistent vector space.
    """
    import numpy as np
    return np.array(_embed_local([question])[0], dtype=np.float32)


def _liked_col(base: str) -> str:
    return base + _LIKED_SUFFIX


def _ensure_liked_collection(base: str) -> None:
    """Create the liked-QA Qdrant collection if it does not already exist."""
    from qdrant_client.models import Distance, VectorParams
    client = get_qdrant_client()
    col    = _liked_col(base)
    names  = {c.name for c in client.get_collections().collections}
    if col not in names:
        client.create_collection(
            collection_name=col,
            vectors_config=VectorParams(size=_LIKED_VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("Liked-QA collection created: %s", col)


def search_liked_qa(
    question_embedding: "np.ndarray",
    base_collection: str,
    threshold: float = 0.90,
) -> "tuple[str, float] | None":
    """
    Search the liked-QA Qdrant collection for an answer whose question
    embedding is ≥ *threshold* similar to *question_embedding*.

    Returns (answer_text, score) on a hit, or None on a miss / missing collection.
    """
    client = get_qdrant_client()
    col    = _liked_col(base_collection)
    names  = {c.name for c in client.get_collections().collections}
    if col not in names:
        return None

    hits = client.query_points(
        collection_name=col,
        query=question_embedding.tolist(),
        limit=1,
        with_payload=True,
        score_threshold=threshold,
    ).points

    if not hits:
        return None

    best = hits[0]
    logger.info(
        "Liked-QA HIT | collection=%s | score=%.3f | q=%r",
        col, best.score, best.payload.get("question", "")[:80],
    )
    return best.payload["answer"], best.score


def add_liked_qa_to_qdrant(
    question: str,
    answer: str,
    question_embedding: "np.ndarray",
    base_collection: str,
    message_id: int,
) -> int:
    """
    Store a liked Q&A pair in the liked-QA Qdrant collection.
    Returns the Qdrant point ID (a random 63-bit integer).
    """
    import random
    from qdrant_client.models import PointStruct

    _ensure_liked_collection(base_collection)
    client   = get_qdrant_client()
    col      = _liked_col(base_collection)
    point_id = random.getrandbits(63)

    client.upsert(
        collection_name=col,
        points=[PointStruct(
            id=point_id,
            vector=question_embedding.tolist(),
            payload={"question": question, "answer": answer, "message_id": message_id},
        )],
    )
    logger.info(
        "Liked-QA added | collection=%s | point_id=%d | message_id=%d",
        col, point_id, message_id,
    )
    return point_id


def delete_liked_collection(base_collection: str) -> None:
    """Remove the liked-QA collection for a document (called on document delete)."""
    client = get_qdrant_client()
    col    = _liked_col(base_collection)
    try:
        client.delete_collection(col)
        logger.info("Liked-QA collection deleted: %s", col)
    except Exception as exc:
        logger.warning("Could not delete liked-QA collection %s: %s", col, exc)


# ── Public API ─────────────────────────────────────────────────────────────────

_ECHOED_HINT_RE = _re.compile(
    r"^\s*"
    r"(?:"
    # parenthetical hint we append: "(Please reply in English.)" etc.
    r"\([^)]{0,120}\)\s*"
    r"|"
    # [INTERNAL: ...] or [INST: ...] style tags the model may echo
    r"\[(?:INTERNAL|INST)[^\]]{0,200}\]\s*"
    r"|"
    # [INST]...[/INST] reasoning block
    r"\[INST\][\s\S]{0,600}?\[/INST\]\s*"
    r")",
    _re.DOTALL,
)


def _scrub_echoed_hint_stream(gen):
    """
    Buffer the start of a streaming response and strip any echoed language-hint
    prefix before yielding tokens to the caller.
    """
    BUFFER_CAP = 400          # [INST]...[/INST] blocks can be ~300 chars
    buf = ""
    prefix_stripped = False

    for token in gen:
        if prefix_stripped:
            yield token
            continue
        buf += token
        # Flush once we have enough context OR we see a clear end of any tag
        if len(buf) >= BUFFER_CAP or (buf.lstrip() and not buf.lstrip().startswith(("[", "("))):
            buf = _ECHOED_HINT_RE.sub("", buf).lstrip()
            prefix_stripped = True
            if buf:
                yield buf

    if not prefix_stripped:
        buf = _ECHOED_HINT_RE.sub("", buf).lstrip()
        if buf:
            yield buf


def ask_streaming(question: str, history: list, markdown_text: str,
                  usage_out: dict | None = None,
                  gemini_cache_name: str | None = None):
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
                yield from _scrub_echoed_hint_stream(_ask_streaming_gemini(
                    question, history, markdown_text, model,
                    usage_out=usage_out, cache_name=cache,
                    fallback_contact=fallback_contact,
                ))
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
        yield from _scrub_echoed_hint_stream(_ask_streaming_sarvam(
            question, history, markdown_text, config.sarvam_model,
            usage_out=usage_out, fallback_contact=fallback_contact,
        ))
    else:  # ollama
        yield from _scrub_echoed_hint_stream(_ask_streaming_ollama(
            question, history, markdown_text, config.ollama_model,
            usage_out=usage_out, fallback_contact=fallback_contact,
        ))


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

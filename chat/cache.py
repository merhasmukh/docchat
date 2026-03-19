"""
Per-session in-memory Q&A cache.

Prevents redundant vector-DB + LLM calls when a user asks the same or very
similar question within the same chat session.  Uses cosine similarity on
sentence-transformer embeddings (always the multilingual-local model,
regardless of the RAG embedding setting in LLMConfig).

CACHE_SIMILARITY_THRESHOLD : 0.90 — questions must be ≥90 % similar to match.
_MAX_ENTRIES_PER_SESSION   : 50  — oldest entry evicted when the limit is hit.

Data structure
--------------
_SESSION_CACHE  :  { session_key -> [ (np.ndarray, question_str, answer_str), ... ] }
The numpy array is the L2-normalised sentence-transformer embedding of the
question (shape=(384,), dtype=float32).
"""

import logging
from collections import defaultdict

import numpy as np

logger = logging.getLogger("chat.cache")

CACHE_SIMILARITY_THRESHOLD: float = 0.90
_MAX_ENTRIES_PER_SESSION: int = 50

# process-level store — lives for the lifetime of the gunicorn worker
_SESSION_CACHE: dict[str, list[tuple[np.ndarray, str, str]]] = defaultdict(list)


# ── helpers ────────────────────────────────────────────────────────────────────

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D numpy arrays."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 0.0 else 0.0


# ── public API ─────────────────────────────────────────────────────────────────

def get_cached_answer(session_key: str, query_emb: np.ndarray) -> str | None:
    """
    Return the cached answer whose stored question embedding is ≥
    CACHE_SIMILARITY_THRESHOLD similar to *query_emb*, or None on a miss.

    Scans all entries for the session (typically ≤50) — fast enough since
    numpy dot products on 384-dim vectors are microsecond-level.
    """
    for stored_emb, question, answer in _SESSION_CACHE[session_key]:
        sim = _cosine(query_emb, stored_emb)
        if sim >= CACHE_SIMILARITY_THRESHOLD:
            logger.info(
                "Session-cache HIT | session=%s | sim=%.3f | q=%r",
                session_key[:8], sim, question[:80],
            )
            return answer
    return None


def add_to_cache(session_key: str, emb: np.ndarray, question: str, answer: str) -> None:
    """
    Append a Q&A pair to the session cache.
    Evicts the oldest entry when the per-session limit is reached.
    """
    cache = _SESSION_CACHE[session_key]
    if len(cache) >= _MAX_ENTRIES_PER_SESSION:
        cache.pop(0)  # FIFO eviction
    cache.append((emb, question, answer))
    logger.debug(
        "Session-cache ADD | session=%s | total=%d | q=%r",
        session_key[:8], len(cache), question[:60],
    )


def clear_session_cache(session_key: str) -> None:
    """Remove all cached entries for a session (called on /reset/)."""
    removed = _SESSION_CACHE.pop(session_key, None)
    if removed is not None:
        logger.debug("Session-cache CLEARED | session=%s | had=%d entries", session_key[:8], len(removed))

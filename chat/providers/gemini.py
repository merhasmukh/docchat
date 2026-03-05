import logging
import time

from django.conf import settings
from google import genai
from google.genai import types as genai_types

from .utils import (
    CONVERSATIONAL_SYSTEM_PROMPT,
    DOCUMENT_SYSTEM_INSTRUCTION,
    DOCUMENT_SYSTEM_PROMPT,
    is_conversational,
)

logger = logging.getLogger("chat.pipeline")


class GeminiCacheExpiredError(Exception):
    """Raised when a Gemini cached content is not found or has expired (HTTP 403)."""


# ── Context cache ──────────────────────────────────────────────────────────────

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
                system_instruction=DOCUMENT_SYSTEM_INSTRUCTION,
                contents=[
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(
                            text="## Document Context (Markdown)\n\n" + markdown_text
                        )],
                    )
                ],
                ttl="3600s",
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


# ── Chat helpers ───────────────────────────────────────────────────────────────

def _build_gemini_contents(question: str, history: list) -> list:
    """Convert Ollama-style message history to Gemini Content objects."""
    contents = []
    for m in history[-20:]:
        role = "model" if m["role"] == "assistant" else m["role"]
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=m["content"])]))
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=question)]))
    return contents


def _ask_streaming_gemini(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None, cache_name: str | None = None):
    conversational = is_conversational(question)
    # Bypass cache for conversational messages — the cache's system_instruction
    # ("Use ONLY the document context") cannot be overridden per-request.
    cached = cache_name is not None and not conversational
    logger.info(
        "LLM stream start | provider=gemini | model=%s | history_turns=%d | q_chars=%d | cached=%s | conversational=%s",
        model_name, len(history) // 2, len(question), cached, conversational,
    )
    t0 = time.perf_counter()
    output_chars = 0
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    contents = _build_gemini_contents(question, history)

    if conversational:
        llm_config = genai_types.GenerateContentConfig(system_instruction=CONVERSATIONAL_SYSTEM_PROMPT)
    elif cached:
        llm_config = genai_types.GenerateContentConfig(cached_content=cache_name)
    else:
        system = DOCUMENT_SYSTEM_PROMPT.format(markdown_text=markdown_text)
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
        if cached and (
            "403" in str(exc)
            or "PERMISSION_DENIED" in str(exc)
            or "CachedContent not found" in str(exc)
            or (
                "400" in str(exc)
                and "INVALID_ARGUMENT" in str(exc)
                and "CachedContent" in str(exc)
            )
        ):
            logger.warning("Gemini cache invalid (expired or model mismatch), will recache: %s", exc)
            raise GeminiCacheExpiredError(str(exc)) from exc
        if cached:
            logger.warning("Gemini cached stream failed: %s", exc)
        raise
    finally:
        if usage_out is not None and last_chunk is not None:
            meta = getattr(last_chunk, "usage_metadata", None)
            if meta:
                usage_out["input_tokens"]         = meta.prompt_token_count or 0
                usage_out["output_tokens"]        = meta.candidates_token_count or 0
                usage_out["cached_input_tokens"]  = getattr(meta, "cached_content_token_count", None) or 0
                # Distinguish explicit cache (we created it) from Gemini's implicit/automatic
                # caching.  Storage cost is only billable for explicit cache; implicit cache
                # gives a read-rate discount with no storage charge.
                usage_out["gemini_explicit_cache"] = cached
        logger.info(
            "LLM stream done  | provider=gemini | model=%s | response_chars=%d | time=%.2fs | cached=%s",
            model_name, output_chars, time.perf_counter() - t0, cached,
        )


def _ask_gemini(question: str, history: list, markdown_text: str, model_name: str) -> tuple[str, float]:
    logger.info("LLM ask | provider=gemini | model=%s", model_name)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    if is_conversational(question):
        system = CONVERSATIONAL_SYSTEM_PROMPT
    else:
        system = DOCUMENT_SYSTEM_PROMPT.format(markdown_text=markdown_text)
    config = genai_types.GenerateContentConfig(system_instruction=system)
    contents = _build_gemini_contents(question, history)
    t0 = time.perf_counter()
    response = client.models.generate_content(model=model_name, contents=contents, config=config)
    elapsed = time.perf_counter() - t0
    answer = response.text
    logger.info("LLM done | provider=gemini | model=%s | response_chars=%d | time=%.2fs",
                model_name, len(answer), elapsed)
    return answer, elapsed

import logging
import time

from django.conf import settings
from google import genai
from google.genai import types as genai_types

from .utils import (
    CONVERSATIONAL_SYSTEM_PROMPT,
    build_document_instruction,
    build_document_prompt,
    is_conversational,
)

logger = logging.getLogger("chat.pipeline")


class GeminiCacheExpiredError(Exception):
    """Raised when a Gemini cached content is not found or has expired (HTTP 403)."""


class GeminiUnavailableError(Exception):
    """Raised when Gemini returns 503 UNAVAILABLE (high demand / temporary outage)."""


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
                system_instruction=build_document_instruction(),
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

_LANG_HINT = (
    "\n\n[CRITICAL LANGUAGE RULE: Your reply MUST be in the exact same language as the question above. "
    "If the question has Gujarati words/script → reply in Gujarati (keep English acronyms as-is). "
    "If the question has Hindi/Devanagari words → reply in Hindi. "
    "If the question is English only → reply in English. "
    "NEVER translate a Gujarati or Hindi question into English.]"
)


def _build_gemini_contents(question: str, history: list, lang_hint: bool = False) -> list:
    """Convert Ollama-style message history to Gemini Content objects."""
    contents = []
    for m in history[-10:]:
        role = "model" if m["role"] == "assistant" else m["role"]
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=m["content"])]))
    text = question + _LANG_HINT if lang_hint else question
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=text)]))
    return contents


def _ask_streaming_gemini(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None, cache_name: str | None = None,
                           fallback_contact: str = ""):
    conversational = is_conversational(question)
    # Bypass cache for conversational messages — the cache's system_instruction
    # ("Use ONLY the document context") cannot be overridden per-request.
    cached = cache_name is not None and not conversational
    logger.info(
        "LLM stream start | provider=gemini | model=%s | history_turns=%d | q_chars=%d | ctx_chars=%d | explicit_cache=%s | conversational=%s",
        model_name, len(history) // 2, len(question), len(markdown_text), cached, conversational,
    )
    t0 = time.perf_counter()
    output_chars = 0
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    contents = _build_gemini_contents(question, history, lang_hint=not conversational)

    if conversational:
        llm_config = genai_types.GenerateContentConfig(system_instruction=CONVERSATIONAL_SYSTEM_PROMPT)
    elif cached:
        # For cached path: inject fallback_contact as an additional per-request
        # system instruction on top of the baked-in cache instruction.
        extra = (
            build_document_instruction(fallback_contact)
            if fallback_contact else None
        )
        llm_config = genai_types.GenerateContentConfig(
            cached_content=cache_name,
            **({"system_instruction": extra} if extra else {}),
        )
    else:
        system = build_document_prompt(markdown_text, fallback_contact)
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
        exc_str = str(exc)
        if cached and (
            "403" in exc_str
            or "PERMISSION_DENIED" in exc_str
            or "CachedContent not found" in exc_str
            or (
                "400" in exc_str
                and "INVALID_ARGUMENT" in exc_str
                and "CachedContent" in exc_str
            )
        ):
            logger.warning("Gemini cache invalid (expired or model mismatch), will recache: %s", exc)
            raise GeminiCacheExpiredError(exc_str) from exc
        if "503" in exc_str or "UNAVAILABLE" in exc_str:
            logger.warning("Gemini model %s unavailable (503): %s", model_name, exc)
            raise GeminiUnavailableError(exc_str) from exc
        if cached:
            logger.warning("Gemini cached stream failed: %s", exc)
        raise
    finally:
        in_tokens  = 0
        out_tokens = 0
        auto_cached_tokens = 0
        if usage_out is not None and last_chunk is not None:
            meta = getattr(last_chunk, "usage_metadata", None)
            if meta:
                in_tokens                         = meta.prompt_token_count or 0
                out_tokens                        = meta.candidates_token_count or 0
                auto_cached_tokens                = getattr(meta, "cached_content_token_count", None) or 0
                usage_out["input_tokens"]         = in_tokens
                usage_out["output_tokens"]        = out_tokens
                usage_out["cached_input_tokens"]  = auto_cached_tokens
                # Distinguish explicit cache (we created it) from Gemini's implicit/automatic
                # caching.  Storage cost is only billable for explicit cache; implicit cache
                # gives a read-rate discount with no storage charge.
                usage_out["gemini_explicit_cache"] = cached
        logger.info(
            "LLM stream done  | provider=gemini | model=%s | in_tokens=%d auto_cached=%d out_tokens=%d | time=%.2fs | explicit_cache=%s",
            model_name, in_tokens, auto_cached_tokens, out_tokens, time.perf_counter() - t0, cached,
        )


def _ask_gemini(question: str, history: list, markdown_text: str, model_name: str,
                fallback_contact: str = "") -> tuple[str, float]:
    logger.info("LLM ask | provider=gemini | model=%s", model_name)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    if is_conversational(question):
        system = CONVERSATIONAL_SYSTEM_PROMPT
    else:
        system = build_document_prompt(markdown_text, fallback_contact)
    config = genai_types.GenerateContentConfig(system_instruction=system)
    contents = _build_gemini_contents(question, history, lang_hint=not is_conversational(question))
    t0 = time.perf_counter()
    response = client.models.generate_content(model=model_name, contents=contents, config=config)
    elapsed = time.perf_counter() - t0
    answer = response.text
    logger.info("LLM done | provider=gemini | model=%s | response_chars=%d | time=%.2fs",
                model_name, len(answer), elapsed)
    return answer, elapsed

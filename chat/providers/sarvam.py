import json
import logging
import time

import httpx
from django.conf import settings

from .utils import (CONVERSATIONAL_SYSTEM_PROMPT, add_language_hint,
                    build_document_prompt, is_conversational, strip_citation_phrases)

logger = logging.getLogger("chat.pipeline")

# Sarvam's OpenAI-compatible chat completions endpoint
_SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"


def _extract_content(response) -> str:
    """
    Safely extract the visible answer text from a Sarvam response.
    Falls back to reasoning_content when content is empty (sarvam-m think mode
    may count thinking tokens without putting text in the content field).
    """
    try:
        text = response.choices[0].message.content or ""
        if text:
            return text
    except (AttributeError, IndexError):
        pass
    try:
        text = getattr(response.choices[0].message, "reasoning_content", None) or ""
        if text:
            return text
    except (AttributeError, IndexError):
        pass
    return ""


def _build_messages(question: str, history: list, markdown_text: str,
                    fallback_contact: str = "") -> list:
    if is_conversational(question):
        # Skip history entirely for greetings/small-talk:
        # document Q&A history confuses the model when it receives a greeting,
        # producing empty or nonsensical responses.
        logger.debug("Sarvam: conversational message — fresh context, no history")
        return [
            {"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

    # Keep last 5 turns (10 messages) of history
    trimmed_history = history[-10:]

    return (
        [{"role": "system", "content": build_document_prompt(markdown_text, fallback_contact)}]
        + trimmed_history
        + [{"role": "user", "content": add_language_hint(question)}]
    )


# ── API callers ────────────────────────────────────────────────────────────────

def _ask_streaming_sarvam(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None, fallback_contact: str = ""):
    logger.info(
        "LLM call start | provider=sarvam | model=%s | history_turns=%d | q_chars=%d | conversational=%s",
        model_name, len(history) // 2, len(question), is_conversational(question),
    )
    t0 = time.perf_counter()
    messages = _build_messages(question, history, markdown_text, fallback_contact)
    full_response: list[str] = []
    input_tokens = output_tokens = 0

    headers = {
        "Authorization": f"Bearer {settings.SARVAM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": model_name, "messages": messages, "stream": True}

    try:
        with httpx.stream(
            "POST", _SARVAM_CHAT_URL, headers=headers,
            json=payload, timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        full_response.append(delta)
                        yield delta
                    # usage in final chunk (if server sends it)
                    u = chunk.get("usage")
                    if u:
                        input_tokens  = u.get("prompt_tokens", 0)
                        output_tokens = u.get("completion_tokens", 0)
                except (json.JSONDecodeError, IndexError):
                    continue
    except Exception as exc:
        # Streaming failed — fall back to non-streaming via the sarvamai SDK
        logger.warning("Sarvam streaming failed (%s) — falling back to non-streaming SDK", exc)
        full_response.clear()
        try:
            from sarvamai import SarvamAI
            sdk_client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
            response = sdk_client.chat.completions(
                messages=messages, wiki_grounding=False, model=model_name,
            )
            answer = _extract_content(response)
            if not answer:
                answer = "Hello! I'm here and ready to help you with questions about your document."
            else:
                answer = strip_citation_phrases(answer)
            full_response.append(answer)
            yield answer
            try:
                input_tokens  = response.usage.prompt_tokens or 0
                output_tokens = response.usage.completion_tokens or 0
            except AttributeError:
                pass
        except Exception as sdk_exc:
            logger.error("Sarvam SDK fallback also failed: %s", sdk_exc)
            yield "Sorry, I'm having trouble connecting to the Sarvam AI service. Please try again."
            return

    answer = "".join(full_response)
    elapsed = time.perf_counter() - t0

    if usage_out is not None:
        if input_tokens or output_tokens:
            usage_out["input_tokens"]  = input_tokens
            usage_out["output_tokens"] = output_tokens
        else:
            # Estimate if streaming gave no usage metadata
            input_chars = sum(len(m.get("content", "")) for m in messages)
            usage_out["input_tokens"]  = max(1, input_chars // 4)
            usage_out["output_tokens"] = max(1, len(answer) // 4)
            usage_out["estimated"] = True

    logger.info(
        "LLM call done   | provider=sarvam | model=%s | response_chars=%d | time=%.2fs",
        model_name, len(answer), elapsed,
    )


def _ask_sarvam(question: str, history: list, markdown_text: str, model_name: str,
                fallback_contact: str = "") -> tuple[str, float]:
    from sarvamai import SarvamAI

    logger.info("LLM ask | provider=sarvam | model=%s", model_name)
    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    messages = _build_messages(question, history, markdown_text, fallback_contact)

    t0 = time.perf_counter()
    response = client.chat.completions(
        messages=messages,
        wiki_grounding=False,
        model=model_name,
    )
    elapsed = time.perf_counter() - t0

    answer = _extract_content(response)
    if not answer:
        logger.warning("Sarvam returned empty content | question=%r", question)
        answer = "Hello! I'm here and ready to help you with questions about your document."
    else:
        answer = strip_citation_phrases(answer)
    logger.info("LLM done | provider=sarvam | model=%s | response_chars=%d | time=%.2fs",
                model_name, len(answer), elapsed)
    return answer, elapsed

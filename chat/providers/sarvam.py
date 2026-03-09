import logging
import time

from django.conf import settings

from .utils import CONVERSATIONAL_SYSTEM_PROMPT, build_document_prompt, is_conversational, strip_citation_phrases

logger = logging.getLogger("chat.pipeline")

# Sarvam API limit: 7168 tokens total input.
# Overhead breakdown:
#   system prompt template (rules, no context): ~250 tokens
#   history (last 10 messages × ~50 tokens):    ~500 tokens
#   question + constraint suffix:               ~150 tokens
#   safety buffer:                              ~200 tokens
#   total overhead:                            ~1100 tokens
#   available for context:                     ~6068 tokens
#
# Gujarati / Indic script: ~2–3 chars per token (denser than English's ~4).
# 9 000 chars ≈ 3 000–4 500 tokens → well within budget even for pure Gujarati docs.
_MAX_CONTEXT_CHARS = 9_000


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

    # Sarvam has a tight token budget — keep only the last 5 turns (10 messages)
    trimmed_history = history[-10:]

    # Document question — inject context with hard char limit
    if len(markdown_text) > _MAX_CONTEXT_CHARS:
        markdown_text = markdown_text[:_MAX_CONTEXT_CHARS]
        logger.warning(
            "Sarvam context truncated to %d chars (7168-token limit protection)",
            _MAX_CONTEXT_CHARS,
        )

    constrained_question = (
        f"{question}\n\n"
        "[IMPORTANT: Answer using ONLY the document context in the system prompt. "
        "If the answer is not there, say so — do not use outside knowledge.]"
    )
    return (
        [{"role": "system", "content": build_document_prompt(markdown_text, fallback_contact)}]
        + trimmed_history
        + [{"role": "user", "content": constrained_question}]
    )


# ── API callers ────────────────────────────────────────────────────────────────

def _ask_streaming_sarvam(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None, fallback_contact: str = ""):
    from sarvamai import SarvamAI

    logger.info(
        "LLM call start | provider=sarvam | model=%s | history_turns=%d | q_chars=%d | conversational=%s",
        model_name, len(history) // 2, len(question), is_conversational(question),
    )
    t0 = time.perf_counter()

    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    messages = _build_messages(question, history, markdown_text, fallback_contact)

    # The sarvamai SDK's streaming iterator raises even on HTTP 200 responses.
    # Use the non-streaming API and yield the full answer at once instead.
    response = client.chat.completions(
        messages=messages,
        wiki_grounding=False,
    )

    answer = _extract_content(response)
    if not answer:
        logger.warning("Sarvam returned empty content | question=%r", question)
        answer = "Hello! I'm here and ready to help you with questions about your document."
    else:
        answer = strip_citation_phrases(answer)

    if usage_out is not None:
        try:
            usage_out["input_tokens"]  = response.usage.prompt_tokens or 0
            usage_out["output_tokens"] = response.usage.completion_tokens or 0
        except AttributeError:
            input_chars = sum(len(m.get("content", "")) for m in messages)
            usage_out["input_tokens"]  = max(1, input_chars // 4)
            usage_out["output_tokens"] = max(1, len(answer) // 4)
            usage_out["estimated"] = True

    logger.info(
        "LLM call done   | provider=sarvam | model=%s | response_chars=%d | time=%.2fs",
        model_name, len(answer), time.perf_counter() - t0,
    )
    yield answer


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

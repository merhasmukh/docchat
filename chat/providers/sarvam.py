import logging
import time

from django.conf import settings

from .utils import CONVERSATIONAL_SYSTEM_PROMPT, NOT_FOUND_REPLY, is_conversational, strip_citation_phrases

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

_DOCUMENT_SYSTEM_PROMPT = (
    "You are a document question-answering assistant.\n"
    "Your ONLY source of information is the document context provided below.\n\n"
    "STRICT RULES — you must follow all of them:\n"
    "1. Answer questions ONLY using information explicitly present in the document context.\n"
    "2. Do NOT use any knowledge from your training data or general world knowledge.\n"
    "3. Do NOT reference external websites, resources, or any information outside the document.\n"
    "4. If the answer cannot be found in the document context, respond with exactly:\n"
    f'   "{NOT_FOUND_REPLY}"\n'
    "   Stop there. Do NOT add any additional information after this sentence.\n"
    "5. Never guess, assume, or infer details that are not stated in the document.\n"
    "6. CRITICAL — reply as if you already know the answer from memory. "
    "NEVER start your reply with or include ANY of these phrases (or any variation of them):\n"
    "   - 'the document states', 'the document says', 'the document mentions'\n"
    "   - 'the document context states', 'the document context clearly states'\n"
    "   - 'according to the document', 'as per the document'\n"
    "   - 'based on the document', 'based on the context', 'based on the provided context'\n"
    "   - 'the context states', 'the context mentions', 'the context shows'\n"
    "   - 'from the document', 'from the context', 'as mentioned in the document'\n"
    "   - 'the provided document', 'the provided context'\n"
    "   Just state the answer directly. Example: if asked 'What is the fee?', say '500 rupees.' — "
    "NOT 'The document states the fee is 500 rupees.'\n\n"
    "## Document Context:\n\n"
    "{markdown_text}\n\n"
    "---\n"
    "REMINDER: Use ONLY the document above. Ignore your training knowledge entirely. "
    "Do NOT mention the document or context in your answer — just give the answer directly."
)


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


def _build_messages(question: str, history: list, markdown_text: str) -> list:
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
        [{"role": "system", "content": _DOCUMENT_SYSTEM_PROMPT.format(markdown_text=markdown_text)}]
        + trimmed_history
        + [{"role": "user", "content": constrained_question}]
    )


# ── API callers ────────────────────────────────────────────────────────────────

def _ask_streaming_sarvam(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None):
    from sarvamai import SarvamAI

    logger.info(
        "LLM call start | provider=sarvam | model=%s | history_turns=%d | q_chars=%d | conversational=%s",
        model_name, len(history) // 2, len(question), is_conversational(question),
    )
    t0 = time.perf_counter()

    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    messages = _build_messages(question, history, markdown_text)

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


def _ask_sarvam(question: str, history: list, markdown_text: str, model_name: str) -> tuple[str, float]:
    from sarvamai import SarvamAI

    logger.info("LLM ask | provider=sarvam | model=%s", model_name)
    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    messages = _build_messages(question, history, markdown_text)

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

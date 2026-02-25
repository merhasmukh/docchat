import logging
import time

import ollama

from .utils import CONVERSATIONAL_SYSTEM_PROMPT, NOT_FOUND_REPLY, is_conversational

logger = logging.getLogger("chat.pipeline")

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
    "6. Answer directly and naturally. NEVER say phrases like 'the document states', "
    "'according to the document', 'based on the context', 'the context mentions', "
    "'as per the document', or any similar phrase. Just give the answer.\n\n"
    "## Document Context:\n\n"
    "{markdown_text}\n\n"
    "---\n"
    "REMINDER: Use ONLY the document above. Ignore your training knowledge entirely."
)


def _build_messages(question: str, history: list, markdown_text: str) -> list:
    trimmed_history = history[-40:]

    if is_conversational(question):
        return (
            [{"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT}]
            + trimmed_history
            + [{"role": "user", "content": question}]
        )

    # Document question — use strict prompt with context
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


def _ask_streaming_ollama(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None):
    logger.info(
        "LLM stream start | provider=ollama | model=%s | history_turns=%d | q_chars=%d | conversational=%s",
        model_name, len(history) // 2, len(question), is_conversational(question),
    )
    t0 = time.perf_counter()
    output_chars = 0
    messages = _build_messages(question, history, markdown_text)
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
                input_chars   = sum(len(m.get("content", "")) for m in messages)
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
    messages = _build_messages(question, history, markdown_text)
    t0 = time.perf_counter()
    response = ollama.chat(model=model_name, messages=messages)
    elapsed = time.perf_counter() - t0
    answer = response["message"]["content"]
    logger.info("LLM done | provider=ollama | model=%s | response_chars=%d | time=%.2fs",
                model_name, len(answer), elapsed)
    return answer, elapsed

import logging
import time

import ollama

from .utils import CONVERSATIONAL_SYSTEM_PROMPT, build_document_prompt, is_conversational


def _build_messages(question: str, history: list, markdown_text: str,
                    fallback_contact: str = "") -> list:
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
        "If the answer is not there, say so — do not use outside knowledge.]\n"
        "[CRITICAL LANGUAGE RULE: Reply in the exact same language as the question above. "
        "Gujarati words/script → reply in Gujarati (keep English acronyms as-is). "
        "Hindi/Devanagari words → reply in Hindi. English only → reply in English. "
        "NEVER translate a Gujarati or Hindi question into English.]"
    )
    return (
        [{"role": "system", "content": build_document_prompt(markdown_text, fallback_contact)}]
        + trimmed_history
        + [{"role": "user", "content": constrained_question}]
    )


def _ask_streaming_ollama(question: str, history: list, markdown_text: str, model_name: str,
                           usage_out: dict | None = None, fallback_contact: str = ""):
    logger.info(
        "LLM stream start | provider=ollama | model=%s | history_turns=%d | q_chars=%d | ctx_chars=%d | conversational=%s",
        model_name, len(history) // 2, len(question), len(markdown_text), is_conversational(question),
    )
    t0 = time.perf_counter()
    output_chars = 0
    messages = _build_messages(question, history, markdown_text, fallback_contact)
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
        input_tokens  = 0
        output_tokens = 0
        estimated     = False
        if usage_out is not None:
            input_tokens  = (last_chunk or {}).get("prompt_eval_count", 0)
            output_tokens = (last_chunk or {}).get("eval_count", 0)
            if input_tokens == 0 and output_tokens == 0:
                input_chars   = sum(len(m.get("content", "")) for m in messages)
                input_tokens  = max(1, input_chars // 4)
                output_tokens = max(1, output_chars // 4)
                estimated     = True
                usage_out["estimated"] = True
            usage_out["input_tokens"]  = input_tokens
            usage_out["output_tokens"] = output_tokens
        logger.info(
            "LLM stream done  | provider=ollama | model=%s | in_tokens=%d out_tokens=%d%s | time=%.2fs",
            model_name, input_tokens, output_tokens, " (est)" if estimated else "",
            time.perf_counter() - t0,
        )


def _ask_ollama(question: str, history: list, markdown_text: str, model_name: str,
                fallback_contact: str = "") -> tuple[str, float]:
    logger.info("LLM ask | provider=ollama | model=%s", model_name)
    messages = _build_messages(question, history, markdown_text, fallback_contact)
    t0 = time.perf_counter()
    response = ollama.chat(model=model_name, messages=messages)
    elapsed = time.perf_counter() - t0
    answer = response["message"]["content"]
    logger.info("LLM done | provider=ollama | model=%s | response_chars=%d | time=%.2fs",
                model_name, len(answer), elapsed)
    return answer, elapsed

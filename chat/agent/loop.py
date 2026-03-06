"""
ReAct agent loop.

Orchestrates multi-step reasoning:
  1. Build agent prompt (memory + tools + history + question)
  2. Non-streaming LLM call → look for TOOL_CALL directive
  3. Execute the tool, append observation, repeat (max MAX_ITERATIONS)
  4. On FINAL_ANSWER (or last iteration) → stream the answer token by token

All tool-call iterations are non-streaming (fast). Only the final answer is
streamed so the user receives it token-by-token.
"""
import logging
import re
import time

from chat.providers.utils import is_conversational, CONVERSATIONAL_SYSTEM_PROMPT
from chat.agent.tools import TOOLS, TOOL_DESCRIPTIONS

logger = logging.getLogger("chat.pipeline")

MAX_ITERATIONS = 4

# Matches:  TOOL_CALL: search_document("some query")
#           TOOL_CALL: get_page(3)
#           TOOL_CALL: list_sections()
_TOOL_RE = re.compile(
    r'TOOL_CALL\s*:\s*(\w+)\s*\(([^)]*)\)',
    re.IGNORECASE,
)
_FINAL_RE = re.compile(r'FINAL_ANSWER\s*:\s*', re.IGNORECASE)


# ── Public entry point ─────────────────────────────────────────────────────────

def run_agent_streaming(question, history, doc, cfg, user_memory, usage_out):
    """
    Generator — yields string tokens for the SSE streaming response.

    Falls back to direct streaming if agent loop errors out.
    """
    t0 = time.perf_counter()

    # Conversational bypass — greetings skip tools and memory entirely.
    if is_conversational(question):
        logger.info("Agent loop: conversational bypass | q=%r", question[:60])
        from chat.pipeline import ask_streaming
        yield from ask_streaming(question, history, "", usage_out=usage_out)
        return

    try:
        yield from _react_loop(question, history, doc, cfg, user_memory, usage_out, t0)
    except Exception as exc:
        logger.error("Agent loop error, falling back to direct ask: %s", exc)
        from chat.pipeline import ask_streaming
        # Rebuild markdown context for fallback
        markdown_text = _load_markdown(doc)
        yield from ask_streaming(question, history, markdown_text, usage_out=usage_out)


# ── ReAct loop ─────────────────────────────────────────────────────────────────

def _react_loop(question, history, doc, cfg, user_memory, usage_out, t0):
    from chat.pipeline import ask_raw, ask_streaming

    observations = []

    for iteration in range(MAX_ITERATIONS):
        prompt = _build_agent_prompt(question, history, doc, cfg, user_memory, observations)

        is_last = iteration == MAX_ITERATIONS - 1

        if not is_last:
            # Non-streaming iteration: look for a tool call.
            response = ask_raw(prompt)

            tool_match = _TOOL_RE.search(response)
            if tool_match:
                tool_name = tool_match.group(1)
                raw_arg   = tool_match.group(2).strip().strip("\"'")
                logger.info("Agent tool call | iter=%d | tool=%s | arg=%r",
                            iteration + 1, tool_name, raw_arg[:80])
                result = _execute_tool(tool_name, raw_arg, doc, cfg)
                observations.append(f"[{tool_name}({raw_arg!r})]\n{result}")
                continue  # next iteration

            # LLM gave a direct answer (no tool call) — stream it.
            final_text = _extract_final(response)
            logger.info("Agent done (no tool call) | iter=%d | time=%.2fs",
                        iteration + 1, time.perf_counter() - t0)
            yield from _stream_text(final_text)
            return

        else:
            # Last iteration: force a streaming final answer.
            logger.info("Agent max iterations reached | streaming final answer")
            yield from ask_streaming(prompt, [], "", usage_out=usage_out)
            return


# ── Tool execution ─────────────────────────────────────────────────────────────

def _execute_tool(name: str, raw_arg: str, doc, cfg) -> str:
    tool_fn = TOOLS.get(name)
    if not tool_fn:
        return f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}"
    try:
        if name == "search_document":
            return tool_fn(raw_arg, doc, cfg)
        elif name == "get_page":
            return tool_fn(int(raw_arg), doc)
        elif name == "list_sections":
            return tool_fn(doc)
        return "Tool dispatch error: unrecognised signature."
    except Exception as exc:
        logger.warning("Tool execution error | tool=%s | arg=%r | error=%s", name, raw_arg, exc)
        return f"Tool error: {exc}"


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_agent_prompt(question, history, doc, cfg, user_memory, observations):
    from chat.providers.utils import AGENT_SYSTEM_PROMPT

    # Build observation block
    obs_block = ""
    if observations:
        obs_block = "\n\n".join(f"Observation {i+1}:\n{o}" for i, o in enumerate(observations))

    # Build history snippet (last 10 turns)
    history_lines = []
    for msg in history[-20:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        history_lines.append(f"{role}: {msg.get('content', '').strip()}")
    history_text = "\n".join(history_lines)

    # Document context (full mode inlines text; rag mode leaves it to tools)
    effective_mode = cfg.context_mode if cfg.context_mode != "auto" else doc.context_mode
    if effective_mode == "full":
        doc_context = _load_markdown(doc)
        context_note = "The full document text is provided below."
    else:
        doc_context = ""
        context_note = (
            "Use tools to search the document — it is NOT provided inline. "
            "Call search_document() or get_page() to retrieve content."
        )

    return AGENT_SYSTEM_PROMPT.format(
        user_memory=user_memory or "No prior memory for this user.",
        tool_descriptions=TOOL_DESCRIPTIONS,
        observations=obs_block or "None yet.",
        history_text=history_text or "(start of conversation)",
        doc_name=doc.original_filename,
        context_note=context_note,
        doc_context=doc_context,
        question=question,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_final(text: str) -> str:
    """Strip FINAL_ANSWER: prefix if present, return clean answer text."""
    match = _FINAL_RE.search(text)
    if match:
        return text[match.end():].strip()
    return text.strip()


def _load_markdown(doc) -> str:
    from pathlib import Path
    try:
        return Path(doc.markdown_path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _stream_text(text: str):
    """Yield a pre-computed answer string as token chunks (word-by-word)."""
    words = text.split(" ")
    for i, word in enumerate(words):
        yield word if i == 0 else " " + word

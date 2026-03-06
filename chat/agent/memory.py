"""
Agent memory: load and save per-user cross-session memory.

Memory is a short plain-text block (≤500 chars) that summarises what the agent
knows about the user: name, language preference, expertise level, topics they
care about, and things that confused them.

save_memory() is always called from a daemon thread so it never blocks responses.
"""
import logging

logger = logging.getLogger("chat.pipeline")

MEMORY_CAP = 500  # chars


def load_memory(user_email: str) -> str:
    """Return memory_text for this user, or '' if they have no memory yet."""
    from chat.models import AgentMemory
    try:
        return AgentMemory.objects.get(user_email=user_email).memory_text
    except AgentMemory.DoesNotExist:
        return ""


def save_memory(user_email: str, session_history: list, doc_name: str) -> None:
    """
    Compress the current session into key facts and merge with existing memory.
    Called in a background thread — errors are logged but never raised.
    """
    from chat.models import AgentMemory
    from chat.pipeline import ask_raw

    try:
        history_text = _format_history(session_history[-20:])

        # Step 1: Summarise this session.
        summary_prompt = (
            "You are a memory assistant. Read the following conversation between "
            "a user and a document chatbot assistant.\n\n"
            f"Document: {doc_name}\n\n"
            f"Conversation:\n{history_text}\n\n"
            "Write 3-5 SHORT bullet points about the user — things useful for "
            "personalising future responses. Focus on: name, language preference, "
            "expertise level, main topics asked about, things they struggled with. "
            "Plain text only. No markdown headers. Be concise."
        )
        session_summary = ask_raw(summary_prompt).strip()

        # Step 2: Merge with existing memory (if any).
        existing = load_memory(user_email)
        if existing:
            merge_prompt = (
                "You are a memory assistant.\n\n"
                f"Existing memory:\n{existing}\n\n"
                f"New session facts:\n{session_summary}\n\n"
                "Merge these into a single concise memory block. "
                f"Keep only the most useful and stable facts. "
                f"Maximum {MEMORY_CAP} characters. Plain text, no markdown."
            )
            merged = ask_raw(merge_prompt).strip()
        else:
            merged = session_summary

        merged = merged[:MEMORY_CAP]

        # Step 3: Upsert.
        mem, created = AgentMemory.objects.get_or_create(user_email=user_email)
        mem.memory_text = merged
        if not created:
            mem.total_sessions += 1
        mem.save()

        logger.info("Agent memory updated | email=%s | chars=%d", user_email, len(merged))

    except Exception as exc:
        logger.warning("Agent memory update failed | email=%s | error=%s", user_email, exc)


def _format_history(history: list) -> str:
    """Format message history as a readable conversation transcript."""
    lines = []
    for msg in history:
        role = "User" if msg.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {msg.get('content', '').strip()}")
    return "\n".join(lines)

"""
Shared utilities used by all LLM providers.
"""

# ── Conversational message detection ──────────────────────────────────────────

_GREETING_WORDS = frozenset({
    "hi", "hello", "hey", "hii", "helo", "hai",
    "namaste", "namaskar", "kem cho",
})

_CONVERSATIONAL_PHRASES = frozenset({
    "how are you", "how r u", "how are u", "how r you",
    "what's up", "whats up", "sup",
    "good morning", "good afternoon", "good evening", "good night",
    "thanks", "thank you", "thank u", "thankyou", "ty", "thx",
    "bye", "goodbye", "good bye", "see you", "see ya", "take care",
    "ok", "okay", "sure", "alright", "got it", "understood",
    "who are you", "what are you", "what can you do",
    "how can you help", "what can you help with",
    "help", "help me",
})

CONVERSATIONAL_SYSTEM_PROMPT = (
    "You are a friendly and helpful document assistant. "
    "Respond warmly and naturally to the user's message. "
    "Keep your reply brief. "
    "You can let the user know you are ready to answer questions about their uploaded document."
)

# Standard reply when the answer is not in the document.
# Deliberately avoids mentioning "document" or "context" — keeps the UX clean.
NOT_FOUND_REPLY = "I'm sorry, I don't know the answer to your question."


def is_conversational(question: str) -> bool:
    """
    Return True when the question is general small-talk that does not need
    document context (greetings, pleasantries, meta questions about the bot).
    Uses fast set-lookups — no extra API call required.
    """
    q = question.lower().strip().rstrip("?!.,")

    # Exact phrase match
    if q in _CONVERSATIONAL_PHRASES or q in _GREETING_WORDS:
        return True

    # Greeting word as the first word in a very short message (≤ 3 words)
    words = q.split()
    if words and words[0] in _GREETING_WORDS and len(words) <= 3:
        return True

    return False

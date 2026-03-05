"""
Shared utilities used by all LLM providers.
"""
import re

# ── Citation-phrase scrubber ───────────────────────────────────────────────────
# Matches phrases the model inserts despite being told not to, e.g.
#   "The document context clearly states:"
#   "explicitly mentioned in the document context as"
#   "According to the document,"
# Works both at sentence starts AND inline ("… are explicitly mentioned in the
# document context as …").

_CITATION_RE = re.compile(
    r"(?i)"
    r"(?:"
    # inline: "are/is [clearly] mentioned/stated in the document [context] as/:"
    r"(?:(?:are|is|was|were|has\s+been|have\s+been)\s+)?"
    r"(?:explicitly|clearly|directly|specifically)\s+"
    r"(?:mentioned|stated|specified|described|indicated)\s+"
    r"in\s+the\s+(?:document\s+)?context\s+(?:as\s+|:\s*)?"
    r"|"
    # "the document [context] [clearly] states/says/mentions [that] [:]"
    r"the\s+(?:document\s+)?context\s+(?:clearly\s+|explicitly\s+)?"
    r"(?:states?|says?|mentions?|indicates?|shows?|notes?|explains?)\s*(?:that\s+)?:?\s*"
    r"|"
    r"the\s+document\s+(?:clearly\s+|explicitly\s+)?"
    r"(?:states?|says?|mentions?|indicates?|shows?|notes?|explains?)\s*(?:that\s+)?:?\s*"
    r"|"
    # "according to the document/context [,]"
    r"according\s+to\s+(?:the\s+)?(?:document|context)\s*,?\s*"
    r"|"
    # "based on the [document/context/provided context] [,]"
    r"based\s+on\s+(?:the\s+)?(?:provided\s+)?(?:document|context)\s*,?\s*"
    r"|"
    # "as per the document/context"
    r"as\s+per\s+(?:the\s+)?(?:document|context)\s*,?\s*"
    r"|"
    # "from the document/context [,]"
    r"from\s+the\s+(?:provided\s+)?(?:document|context)\s*,?\s*"
    r"|"
    # "as mentioned/stated in the document/context [,]"
    r"as\s+(?:mentioned|stated|described|specified)\s+in\s+the\s+"
    r"(?:document|context)\s*,?\s*"
    r")"
)

# After stripping a citation phrase, fix "are  10.05" → "are 10.05"
_MULTI_SPACE_RE = re.compile(r"  +")


def strip_citation_phrases(text: str) -> str:
    """
    Remove 'the document states' / 'explicitly mentioned in the document context as'
    style phrases that models insert despite instructions.
    Capitalises the first character of the result if needed.
    """
    cleaned = _CITATION_RE.sub(" ", text)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    # Re-capitalise if the first letter became lowercase after stripping
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


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
)

# Standard reply when the answer is not in the document.
# Deliberately avoids mentioning "document" or "context" — keeps the UX clean.
NOT_FOUND_REPLY = "I'm sorry, I don't know the answer to your question."

# ── Document system prompts ────────────────────────────────────────────────────

# Behavioural rules shared by all prompts — no document section.
_RULES = (
    "STRICT RULES:\n"
    "1. Answer ONLY from the document context — no training data or external knowledge.\n"
    "2. If the answer isn't in the document, reply with exactly:\n"
    f'   "{NOT_FOUND_REPLY}" — then stop.\n'
    "3. Never guess, assume, or infer anything not explicitly stated.\n"
    "4. Reply directly — NEVER reference the source. Banned phrases include any variation of:\n"
    "   'the document/context states/says/mentions', 'according to/based on/from the document/context/The document states', etc.\n"
    "   ✓ Say: '500 rupees.'   ✗ Not: 'The document states the fee is 500 rupees.'\n"
    "5. MULTILINGUAL — Document and questions may be in Gujarati, Hindi, English, or mixed.\n"
    "   Match concepts across languages (e.g. 'syllabus' = 'અભ્યાસક્રમ').\n"
    "   Always reply in the proper and correct language the user used."
)

# Full prompt with document context injected via {markdown_text}.
# Used by Ollama, Sarvam, and Gemini (non-cached / inline mode).
DOCUMENT_SYSTEM_PROMPT = (
    "You are a document question-answering assistant.\n"
    "Your ONLY source of information is the document context provided below.\n\n"
    + _RULES
    + "\n\n"
    "## Document Context:\n\n"
    "{markdown_text}\n\n"
    "---\n"
    "REMINDER: Use ONLY the document above. Ignore your training knowledge entirely. "
    "Do NOT mention the document or context in your answer — just give the answer directly."
)

# Rules-only prompt used as Gemini system_instruction when the document is
# placed in cached `contents` (cache stores the document; this stores the rules).
DOCUMENT_SYSTEM_INSTRUCTION = (
    "You are a document question-answering assistant.\n"
    "Your ONLY source of information is the document context provided in this conversation.\n\n"
    + _RULES
)


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

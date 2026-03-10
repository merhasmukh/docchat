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
    "Always reply in the same language the user used "
    "(Gujarati, Hindi, English, or mixed Gujarati+English or Gujarati+Hindi)."
)

# ── Document system prompts ────────────────────────────────────────────────────

def _build_rules(fallback_contact: str = "") -> str:
    """
    Build the STRICT RULES block.
    When fallback_contact is set, rule 3 instructs the model to reply with
    the not-found message AND then provide the contact details.
    """
    if fallback_contact.strip():
        rule3 = (
            "3. If the answer isn't in the document:\n"
            "   • ALWAYS follow with a helpful suggestion to contact using the details below.\n"
            "   • Use the contact info as context — weave it naturally into one or two sentences.\n"
            "   • Do NOT dump the entire block verbatim. Pick what's relevant (phone, website, address).\n"
            "   • The suggestion must also be in the user's language (Gujarati/Hindi/English).\n"
            "   Examples:\n"
            "   Gujarati Q →'"
            "વધુ જાણકારી માટે Gujarat Vidyapith ના Admission Helpline 079-27541148 પર "
            "સંપર્ક કરો અથવા gujaratvidyapith.org ની મુલાકાત લો.'\n"
            "   English Q → '"
            "For details, please contact Gujarat Vidyapith at 079-27541148 "
            "or visit https://www.gujaratvidyapith.org.'\n"
            "   Contact context (use naturally, do not paste as-is):\n"
            f"   {fallback_contact}\n"
        )
    else:
        rule3 = (
            "3. If the answer isn't in the document, warmly acknowledge in the user's language\n"
            "   that you don't have that information — then stop.\n"
            "   Do NOT mention the document or source. Just say you don't have it.\n"
        )

    return (
        "STRICT RULES:\n"
        "1. LANGUAGE — Always reply in the EXACT same language as the user's question.\n"
        "   The document content language is IRRELEVANT — match the question language, not the document.\n"
        "   • English question → reply ONLY in English. NEVER use Gujarati or Hindi script.\n"
        "     Example: Q='what is the date of geeta exam' → A='The date of the GEETA exam is 10-05-2026.'\n"
        "   • Gujarati question (even mixed with English terms) → reply in Gujarati script.\n"
        "     Example: Q='bca ma admission leva su joyeye' → A='BCA માં પ્રવેશ માટે ધોરણ 12 માં 40% માર્ક્સ જોઈએ.'\n"
        "   • Hindi question → reply in Hindi (Devanagari script).\n"
        "   • Mixed Gujarati+English → reply in Gujarati, keeping English acronyms/proper nouns as-is.\n"
        "   NEVER reply in Gujarati or Hindi when the question is written in English.\n"
        "   NEVER reply in English when the question contains Gujarati or Hindi words.\n"
        "2. Answer ONLY from the document context — no training data or external knowledge.\n"
        + rule3
        + "4. CONVERSATION CONTEXT — Use the conversation history to understand the full meaning of\n"
        "   short or follow-up questions before answering.\n"
        "   Example: if the user previously asked about BCA admission and now asks 'ok for mca?',\n"
        "   interpret this as 'what are the admission requirements for MCA?' and answer from the document.\n"
        "   Never invent facts, but DO resolve what the user is asking using prior turns.\n"
        "5. Reply directly — NEVER reference the source. Banned phrases include any variation of:\n"
        "   'the document/context states/says/mentions', 'according to/based on/from the document', etc.\n"
        "   ✓ Say: '500 rupees.'   ✗ Not: 'The document states the fee is 500 rupees.'\n"
        "6. Cross-language matching — match concepts across scripts:\n"
        "   e.g. 'admission' = 'પ્રવેશ', 'syllabus' = 'અભ્યાસક્રમ' = 'पाठ्यक्रम'."
    )


def build_document_prompt(markdown_text: str, fallback_contact: str = "") -> str:
    """
    Full system prompt with document context injected.
    Used by Ollama, Sarvam, and Gemini (non-cached / inline mode).
    """
    return (
        "You are a document question-answering assistant.\n"
        "Your ONLY source of information is the document context provided below.\n\n"
        + _build_rules(fallback_contact)
        + "\n\n"
        "## Document Context:\n\n"
        f"{markdown_text}\n\n"
        "---\n"
        "REMINDER: Use ONLY the document above. Ignore your training knowledge entirely. "
        "Do NOT mention the document or context in your answer — just give the answer directly."
    )


def build_document_instruction(fallback_contact: str = "") -> str:
    """
    Rules-only prompt used as Gemini system_instruction when the document is
    placed in cached contents (cache stores the document; this stores the rules).
    """
    return (
        "You are a document question-answering assistant.\n"
        "Your ONLY source of information is the document context provided in this conversation.\n\n"
        + _build_rules(fallback_contact)
    )


# Backward-compatible constants — zero-fallback versions for callers that
# still use the old string form (e.g. agent loop).
DOCUMENT_SYSTEM_PROMPT = build_document_prompt("{markdown_text}")
DOCUMENT_SYSTEM_INSTRUCTION = build_document_instruction()


# ── Agent system prompt ────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """\
You are a document assistant with memory and tools.

## Memory About This User
{user_memory}

## Available Tools
Call ONE tool per turn using EXACTLY this format on a line by itself:
  TOOL_CALL: search_document("your search query")
  TOOL_CALL: get_page(3)
  TOOL_CALL: list_sections()

Tool reference:
{tool_descriptions}

When you have enough information, respond with:
  FINAL_ANSWER:
  [your complete answer here]

## Rules
- Answer ONLY from the document — no external knowledge or assumptions
- If the information is not in the document, say so clearly
- Respond in the same language the user wrote in (Gujarati, Hindi, English, or mixed)
- Do NOT reference the source — give the answer directly
- Maximum 4 tool calls per question

## Prior Tool Observations This Turn
{observations}

## Conversation History
{history_text}

## Document: {doc_name}
{context_note}
{doc_context}

User question: {question}\
"""

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

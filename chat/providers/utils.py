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


# ── Language detection ────────────────────────────────────────────────────────
# Romanized Gujarati words that are STRONGLY distinctive (not common in Hindi/English).
# Even 1 hit reliably identifies Romanized Gujarati.
_GUJARATI_STRONG = frozenset({
    "che", "nay", "kevi", "rite", "sakay", "joyeye", "ketli", "ketla",
    "levay", "leva", "aapu", "milse", "hase", "malse", "badha", "aave",
    "thay", "thashe", "karavu", "karvanu", "puchho", "kem",
})
# Common Gujarati words (also appear in Hindi) — 2+ hits = likely Gujarati.
_GUJARATI_WEAK = frozenset({
    "ma", "su", "shu", "thi", "nu", "na", "ni", "no", "ane", "pan",
    "ke", "hoy", "mate", "taro", "tamaro", "maro", "amaro",
})
# Romanized Hindi words distinctive from Gujarati.
_HINDI_STRONG = frozenset({
    "hai", "hain", "kaise", "kyun", "mein", "nahi", "chahiye",
    "hoga", "hogi", "kitne", "kitni", "milega", "milegi", "aur",
})


def detect_question_language(question: str) -> str:
    """
    Return one of: 'gujarati', 'hindi', 'gujarati_roman', 'hindi_roman', 'english'.
    Unicode script ranges take priority over Roman-script heuristics.
    """
    # Unicode script ranges
    if any('\u0A80' <= c <= '\u0AFF' for c in question):
        return "gujarati"
    if any('\u0900' <= c <= '\u097F' for c in question):
        return "hindi"

    words = set(re.sub(r"[^a-z\s]", " ", question.lower()).split())

    if words & _GUJARATI_STRONG:
        return "gujarati_roman"
    if len(words & _GUJARATI_WEAK) >= 2:
        return "gujarati_roman"
    if words & _HINDI_STRONG:
        return "hindi_roman"
    return "english"


def add_language_hint(question: str) -> str:
    """
    For Romanized Gujarati/Hindi questions, prepend a native-script instruction
    so the LLM reliably replies in the correct script regardless of system-prompt
    language rules being followed or not.
    Script-based questions (actual Gujarati/Hindi script) are already unambiguous.
    """
    lang = detect_question_language(question)
    if lang == "gujarati_roman":
        return f"[ગુજરાતીમાં જ જવાબ આપો.]\n{question}"
    if lang == "hindi_roman":
        return f"[केवल हिंदी में उत्तर दें।]\n{question}"
    return question


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
        "1. LANGUAGE — Reply in the EXACT same language as the user's question.\n"
        "   The document content language is IRRELEVANT — match the question language, not the document.\n\n"
        "   ┌─ HOW TO DETECT THE LANGUAGE ─────────────────────────────────────────────────────┐\n"
        "   │ A. Gujarati script (unicode): ગ, ા, ્, etc. → reply in Gujarati script.          │\n"
        "   │ B. Hindi / Devanagari script: क, ख, ग, etc. → reply in Hindi (Devanagari).       │\n"
        "   │ C. Roman script with Gujarati words → reply in GUJARATI SCRIPT.                  │\n"
        "   │    Gujarati words written in Roman: ma, che, ke nay, su, kevi rite, ketla,        │\n"
        "   │    levay, leva, joyeye, aape, thay, sakay, wali, nu, na, ni, no, ane, pan,        │\n"
        "   │    hoy, kem, shu, thi, mate, karva, mali, male, hase, aapu, badha, ketli.         │\n"
        "   │    → These are Gujarati words. Roman Gujarati question → Gujarati script reply.   │\n"
        "   │ D. Pure English (no Gujarati/Hindi words) → reply in English only.               │\n"
        "   └──────────────────────────────────────────────────────────────────────────────────┘\n\n"
        "   EXAMPLES (follow these strictly):\n"
        "   Q='what is the date of geeta exam'          → English reply.\n"
        "   Q='how many seats in mca'                   → English reply.\n"
        "   Q='bca ma admission levay ke nay'           → Gujarati reply: 'BCA માં પ્રવેશ 40% સાથે મળે છે.'\n"
        "   Q='gujarat vidyapith ma bca ma admission levay ke nay' → Gujarati reply.\n"
        "   Q='kevi rite admission lay sakay'           → Gujarati reply: 'પ્રવેશ માટે www.gujaratvidyapith.org પર રજીસ્ટ્રેશન કરો.'\n"
        "   Q='mca ma ketli seats che'                  → Gujarati reply: 'MCA માં 60 બેઠકો છે.'\n"
        "   Q='admission ni last date su che'           → Gujarati reply.\n"
        "   Q='fee ketli hase'                          → Gujarati reply.\n"
        "   Q='BCA ke MCA kaun sa better hai'           → Hindi reply (Devanagari).\n\n"
        "   NEVER reply in English when the question contains ANY Gujarati or Hindi words.\n"
        "   NEVER reply in Gujarati or Hindi when the question is pure English.\n"
        "   Keep English acronyms/proper nouns (BCA, MCA, Gujarat Vidyapith) as-is in any language reply.\n"
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

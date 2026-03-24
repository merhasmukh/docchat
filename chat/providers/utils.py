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

# Scrub sentences where the LLM echoes / explains the language instruction.
# e.g. "Your question contains Gujarati words ... According to the rules, I must reply in Gujarati."
_LANG_ECHO_RE = re.compile(
    r"(?i)"
    r"(?:^|\.\s+)"        # start of text or after a full-stop
    r"[^.]*?"             # any lead-in text
    r"(?:"
    r"according\s+to\s+the\s+rules?"
    r"|your\s+question\s+contains\s+(?:gujarati|hindi|english)\s+words?"
    r"|I\s+must\s+reply\s+in\s+(?:gujarati|hindi|english)"
    r"|as\s+(?:per|per\s+the)\s+(?:language\s+)?(?:rule|instruction)"
    r"|this\s+(?:language\s+)?instruction"
    r")"
    r"[^.]*?(?:\.|$)",    # up to end of sentence or string
    re.DOTALL,
)


def strip_citation_phrases(text: str) -> str:
    """
    Remove 'the document states' / 'explicitly mentioned in the document context as'
    style phrases that models insert despite instructions.
    Also removes sentences where the model echoes or explains the language instruction.
    Capitalises the first character of the result if needed.
    """
    cleaned = _CITATION_RE.sub(" ", text)
    cleaned = _LANG_ECHO_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    # Re-capitalise if the first letter became lowercase after stripping
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


# ── Language detection ────────────────────────────────────────────────────────
# Romanized Gujarati words that are STRONGLY distinctive (not common in Hindi/English).
# Even 1 hit reliably identifies Romanized Gujarati.
_GUJARATI_STRONG = frozenset({
    "che", "chhe", "nay", "kevi", "rite", "sakay", "joyeye", "ketli", "ketla",
    "levay", "leva", "aapu", "milse", "hase", "malse", "badha", "aave",
    "thay", "thashe", "thase", "karavu", "karvanu", "puchho", "kem",
    # common question/answer words missed before
    "chale", "male", "malshe", "milshe", "levu", "leva", "karva",
    "joi", "joie", "bane", "hatu", "hata", "hoy", "hoye",
    "aapi", "aapse", "aapshe", "jaoy", "chalo", "chalse","ma","aapo",
})
# Common Gujarati words (also appear in Hindi) — 2+ hits = likely Gujarati.
_GUJARATI_WEAK = frozenset({
    "ma", "su", "shu", "thi", "nu", "na", "ni", "no", "ane", "pan",
    "ke", "hoy", "mate", "taro", "tamaro", "maro", "amaro",
    "aa", "em", "evi", "evo", "kya",
})
# Romanized Hindi words distinctive from Gujarati.
_HINDI_STRONG = frozenset({
    # verb forms
    "hai", "hain", "hoga", "hogi", "hoge", "tha", "thi", "the",
    "milega", "milegi", "milenge", "chahiye", "chahta", "chahti",
    # question words
    "kaise", "kese", "kyun", "kyunki", "kya", "kab", "kaun", "kahan",
    # pronouns / postpositions
    "mein", "mujhe", "muje", "humein", "hume", "aapko", "unko",
    "nahi", "nahin", "nhi",
    # common Hinglish connectors
    "aur", "lekin", "toh", "bhi", "sirf", "bas",
    # infinitives (very common in Hinglish questions)
    "lena", "dena", "karna", "milna", "jana", "aana", "padhna", "likhna",
    # counts / quantity
    "kitne", "kitni", "kitna",
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
    Append a brief plain-language reply instruction so the LLM always responds
    in the correct language regardless of context language.
    Appended (not prepended) so the model answers the question first, reducing
    the chance it echoes the instruction back.
    """
    lang = detect_question_language(question)
    if lang in ("gujarati", "gujarati_roman"):
        hint = "ગુજરાતી ભાષામાં જ જવાબ આપો."
    elif lang in ("hindi", "hindi_roman"):
        hint = "कृपया हिंदी में उत्तर दें।"
    else:
        hint = "Please reply in English."
    return f"{question}\n\n({hint})"


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
    "You are GV પ્રવેશ મિત્ર, Gujarat Vidyapith's friendly admission assistant. "
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
            "3. When the exact answer is not available:\n"
            "   • FIRST share the most closely related information that IS in the given context.\n"
            "   • THEN naturally suggest contacting using the details below for the specific detail.\n"
            "   • Use the contact info naturally — pick what's relevant (website, address).\n"
            "   • Do NOT dump the entire contact block verbatim.\n"
            "   • Do NOT say phrases like 'not in the context', 'not mentioned', or 'I don't have information about'.\n"
            "   • The whole reply must be in the user's language (Gujarati/Hindi/English).\n\n"
            "   Contact context (use naturally, do not paste as-is):\n"
            f"   {fallback_contact}\n"
        )
    else:
        rule3 = (
            "3. When the exact answer is not available:\n"
            "   • FIRST share the most closely related information that IS in the context.\n"
            "     Example: asked about B.Sc. Physics → mention which B.Sc. courses ARE listed.\n"
            "   • Keep it brief and helpful — do not over-explain or apologise.\n"
            "   • NEVER say: 'not in the context', 'not mentioned', 'ઉલ્લેખ નથી',\n"
            "     'I don't have that information', or any similar phrase.\n"
        )

    return (
        "STRICT RULES:\n"
        "1. LANGUAGE — CRITICAL: The user's message ends with a language instruction in parentheses. You MUST reply in that language only. The document language is completely irrelevant — if the hint says Hindi, reply fully in Hindi even if the entire document is in Gujarati.\n"
        "   Keep English acronyms/proper nouns (BCA, MCA, Gujarat Vidyapith) as-is in any language reply.\n"
        "2. Answer from the given context. When the exact fact is missing, use related information\n"
        "   from the context to give the most helpful answer possible. Never invent facts not in the\n"
        "   context, but DO use all available related context to address the question.\n"
        + rule3
        + "4. CONVERSATION CONTEXT — Use the conversation history to understand the full meaning of\n"
        "   short or follow-up questions before answering.\n"
        "   Example: if the user previously asked about BCA admission and now asks 'ok for mca?',\n"
        "   interpret this as 'what are the admission requirements for MCA?' and answer from the context.\n"
        "   Never invent facts, but DO resolve what the user is asking using prior turns.\n"
        "5. Reply directly — NEVER reference the source. Banned phrases include any variation of:\n"
        "   'the document/context states/says/mentions', 'according to/based on/from the context', etc.\n"
        "   ✓ Say: '500 rupees.'   ✗ Not: 'The context states the fee is 500 rupees.'\n"
        "6. Cross-language matching — match concepts across scripts:\n"
        "   e.g. 'admission' = 'પ્રવેશ', 'syllabus' = 'અભ્યાસક્રમ' = 'पाठ्यक्रम'.\n"
        "7. BREVITY — By default reply in ONE sentence. Give a longer answer only when the question\n"
        "   inherently requires it (e.g. listing all courses, step-by-step process, fee breakdown).\n"
        "   Never pad a short answer into multiple sentences."
    )


def build_document_prompt(markdown_text: str, fallback_contact: str = "") -> str:
    """
    Full system prompt with document context injected.
    Used by Ollama, Sarvam, and Gemini (non-cached / inline mode).
    """
    return (
        "You are GV પ્રવેશ મિત્ર (GV Pravesh Mitra), Gujarat Vidyapith's admission assistant.\n"
        "Your ONLY source of information is the context provided below.\n\n"
        + _build_rules(fallback_contact)
        + "\n\n"
        "## Context:\n\n"
        f"{markdown_text}\n\n"
        "---\n"
        "REMINDER: Use the context above as your source. When the exact answer is missing, give "
        "the most closely related information that IS present — never say 'not in the context'. "
        "Do NOT mention the context in your answer — just give the answer directly.\n"
        "LANGUAGE OVERRIDE: The user's message ends with a language instruction in parentheses "
        "(e.g. कृपया हिंदी में उत्तर दें। or ગુજરાતી ભાષામાં જ જવાબ આપો. or Please reply in English.). "
        "You MUST reply in that language only — even if the entire document is in a different language."
    )


def build_document_instruction(fallback_contact: str = "") -> str:
    """
    Rules-only prompt used as Gemini system_instruction when the document is
    placed in cached contents (cache stores the document; this stores the rules).
    """
    return (
        "You are GV પ્રવેશ મિત્ર (GV Pravesh Mitra), Gujarat Vidyapith's admission assistant.\n"
        "Your ONLY source of information is the given context provided in this conversation.\n\n"
        + _build_rules(fallback_contact)
    )


# Backward-compatible constants — zero-fallback versions for callers that
# still use the old string form (e.g. agent loop).
DOCUMENT_SYSTEM_PROMPT = build_document_prompt("{markdown_text}")
DOCUMENT_SYSTEM_INSTRUCTION = build_document_instruction()


# ── Agent system prompt ────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """\
You are GV પ્રવેશ મિત્ર (GV Pravesh Mitra), Gujarat Vidyapith's admission assistant with memory and tools.

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
- Answer ONLY from the given context — no external knowledge or assumptions
- If the information is not in the context, say so clearly
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

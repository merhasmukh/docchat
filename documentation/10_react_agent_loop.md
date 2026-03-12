# 10 — ReAct Agent Loop

## What This File Covers

What a ReAct agent is, why it is useful for document Q&A, the complete implementation in `chat/agent/`, how user memory works across sessions, and how to enable and test agent mode.

**Prerequisites:** File 07 (LLM Providers) — you need to understand `ask_raw()` and `ask_streaming()`.

---

## 1. What is an Agent? (Plain English)

**Standard LLM call:** You send one prompt, you get one response. One step.

**Agent:** The LLM can decide to use "tools" — functions that retrieve information. The loop works like this:

```
User asks: "What are the eligibility requirements for MCA?"

Agent thinks:
  "I need to find the MCA requirements. Let me search the document."

Agent calls tool:
  TOOL_CALL: search_document("MCA eligibility requirements")

Tool returns:
  "Page 8: Candidates must have a BCA or BSc (Computer Science)
   with at least 50% marks at graduation..."

Agent thinks:
  "I have enough information. I can answer now."

Agent gives final answer:
  "For MCA admission, you need a BCA or BSc CS with 50% or above..."
```

The agent decides what tools to call, calls them, reads the results, and repeats until it has enough information. **ReAct** stands for **Re**ason + **Act** — the LLM reasons about what to do, then acts (calls a tool).

---

## 2. Why an Agent for Document Q&A?

For small documents (full-context mode), a direct LLM call is sufficient — the whole document is in the prompt. But for large documents in RAG mode, the standard approach retrieves the top-3 pages per query. This can fail when:

- The question requires information from multiple sections
- The user's wording does not match the document's wording closely
- The question is vague and needs to be refined through exploration

An agent solves this by being able to:
1. **List all sections** — build a mental map of the document
2. **Search** — find the most relevant pages for a specific query
3. **Get a specific page** — read any page directly by number

Instead of one RAG lookup, the agent can make several targeted lookups, combining information from multiple pages.

**Additionally:** The agent maintains **persistent memory** — it remembers facts about each user across multiple sessions (name, language preference, what they have asked before). This is stored in `AgentMemory` and compressed by the LLM itself.

---

## 3. The Three Tools

Tools are defined in `chat/agent/tools.py`.

### `search_document(query)`

Calls the RAG retrieval system (see File 06) and returns the top-5 most relevant pages for the query.

```python
def search_document(query: str, doc, cfg) -> str:
    context = retrieve_relevant_context_qdrant(query, doc, cfg, top_k=5)
    if not context:
        return "No relevant content found for this query."
    return context
```

### `get_page(page_number)`

Reads the per-page JSON file (`markdown_cache/<uuid>.json`) and returns the exact text of a specific page.

```python
def get_page(page_number: int, doc) -> str:
    import json
    with open(doc.json_path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    key = str(page_number)
    if key not in pages:
        return f"Page {page_number} does not exist. Document has {len(pages)} pages."
    return pages[key]
```

### `list_sections()`

Returns a preview of every page — useful for the agent to get a "table of contents" view of the document.

```python
def list_sections(doc) -> str:
    with open(doc.json_path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    lines = []
    for page_num, text in sorted(pages.items(), key=lambda x: int(x[0])):
        preview = text[:160].replace("\n", " ")  # first 160 chars
        lines.append(f"Page {page_num}: {preview}...")
    return "\n".join(lines)
```

---

## 4. The ReAct Loop (`chat/agent/loop.py`)

### Entry Point

```python
MAX_ITERATIONS = 4

def run_agent_streaming(question, history, doc, cfg, user_memory, usage_out):
    """
    Generator — yields string tokens for the SSE streaming response.
    Falls back to direct streaming if agent loop errors out.
    """
    # Conversational bypass — greetings skip tools and memory entirely
    if is_conversational(question):
        yield from ask_streaming(question, history, "", usage_out=usage_out)
        return

    try:
        yield from _react_loop(question, history, doc, cfg, user_memory, usage_out)
    except Exception as exc:
        # If the agent loop fails, fall back to a direct streaming call
        markdown_text = Path(doc.markdown_path).read_text()
        yield from ask_streaming(question, history, markdown_text, usage_out=usage_out)
```

The fallback is important for reliability — if the agent loop encounters any unexpected error, the user still gets an answer (via direct LLM call with full context). They never see an error message.

### The Loop Itself

```python
def _react_loop(question, history, doc, cfg, user_memory, usage_out):
    observations = []   # accumulates tool results

    for iteration in range(MAX_ITERATIONS):   # max 4 iterations
        prompt = _build_agent_prompt(
            question, history, doc, cfg, user_memory, observations
        )

        is_last = (iteration == MAX_ITERATIONS - 1)

        if not is_last:
            # Non-streaming call — we need to parse the response for tool calls
            response = ask_raw(prompt)

            # Check if the LLM wants to call a tool
            tool_match = _TOOL_RE.search(response)
            # Regex: TOOL_CALL: function_name("argument")

            if tool_match:
                tool_name = tool_match.group(1)    # e.g., "search_document"
                raw_arg   = tool_match.group(2)    # e.g., "MCA eligibility"

                result = _execute_tool(tool_name, raw_arg, doc, cfg)
                observations.append(f"[{tool_name}({raw_arg!r})]\n{result}")
                continue   # next iteration

            # No tool call — LLM gave a direct final answer
            final_text = _extract_final(response)
            yield from _stream_text(final_text)
            return

        else:
            # Last iteration — force a streaming final answer
            # (gives the LLM one last chance to answer with all observations)
            yield from ask_streaming(prompt, [], "", usage_out=usage_out)
```

### Regex Patterns

```python
# Matches: TOOL_CALL: search_document("some query")
#          TOOL_CALL: get_page(3)
#          TOOL_CALL: list_sections()
_TOOL_RE = re.compile(r'TOOL_CALL\s*:\s*(\w+)\s*\(([^)]*)\)', re.IGNORECASE)

# Matches: FINAL_ANSWER: The fees are...
_FINAL_RE = re.compile(r'FINAL_ANSWER\s*:\s*', re.IGNORECASE)
```

The LLM is instructed in the agent prompt to always use these exact formats. If it does, the agent parses and acts. If it does not (just writes a normal answer), `_extract_final()` strips any `FINAL_ANSWER:` prefix and returns the clean answer.

### Example Agent Trace (4 iterations)

```
Iteration 1:
  LLM output: "I need to find MCA eligibility.
               TOOL_CALL: search_document("MCA admission eligibility")"
  Result: "Page 8: BCA or BSc CS with 50%..."
  observations = ["[search_document('MCA admission eligibility')]\nPage 8: BCA or BSc CS..."]

Iteration 2:
  LLM output: "I should also check the fee structure.
               TOOL_CALL: get_page(12)"
  Result: "Annual fees: ₹45,000..."
  observations = [..., "[get_page(12)]\nAnnual fees: ₹45,000..."]

Iteration 3:
  LLM output: "I have all the information.
               FINAL_ANSWER: For MCA admission, you need a BCA or BSc CS with 50% or above.
               The annual fees are ₹45,000."
  → extracted final answer → streamed to user

(Iteration 4 was not needed)
```

---

## 5. The Agent Prompt

`_build_agent_prompt()` assembles the full prompt:

```python
def _build_agent_prompt(question, history, doc, cfg, user_memory, observations):
    # Build observation block
    obs_block = "\n\n".join(
        f"Observation {i+1}:\n{o}" for i, o in enumerate(observations)
    ) or "None yet."

    # Build history (last 10 turns = 20 messages)
    history_lines = []
    for msg in history[-20:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_lines.append(f"{role}: {msg['content'].strip()}")
    history_text = "\n".join(history_lines) or "(start of conversation)"

    # Document context (full text if full mode; instruction to use tools if rag mode)
    if effective_mode == "full":
        doc_context = Path(doc.markdown_path).read_text()
        context_note = "The full document text is provided below."
    else:
        doc_context = ""
        context_note = "Use tools to search the document — it is NOT provided inline."

    return AGENT_SYSTEM_PROMPT.format(
        user_memory=user_memory or "No prior memory for this user.",
        tool_descriptions=TOOL_DESCRIPTIONS,
        observations=obs_block,
        history_text=history_text,
        doc_name=doc.original_filename,
        context_note=context_note,
        doc_context=doc_context,
        question=question,
    )
```

The prompt includes:
1. **User memory** — what the agent knows about this user from past sessions
2. **Tool descriptions** — how to call each tool
3. **Previous observations** — results from tools called in earlier iterations of this loop
4. **Conversation history** — last 10 Q&A pairs
5. **Document context** — either full text or "use tools" instruction
6. **The current question**

---

## 6. Agent Memory

Memory allows the agent to personalise responses based on past interactions.

### The `AgentMemory` Model

```python
class AgentMemory(models.Model):
    user_email     = models.EmailField(unique=True)
    memory_text    = models.TextField(blank=True)   # max ~500 chars
    total_sessions = models.IntegerField(default=0)
    last_updated   = models.DateTimeField(auto_now=True)
```

One row per user email. `memory_text` is a short plain-text summary — like notes a human assistant would keep about a regular client.

Example memory text:
```
User is interested in MCA admission. Communicates in Gujarati.
Has asked about fees (₹45,000/year), eligibility (BCA required),
and hostel availability. Is from Ahmedabad.
```

### Loading Memory

At the start of every chat request in agent mode:

```python
def load_memory(user_email: str) -> str:
    """Return the memory text for a user, or empty string if none exists."""
    try:
        obj = AgentMemory.objects.get(user_email=user_email)
        return obj.memory_text
    except AgentMemory.DoesNotExist:
        return ""
```

This memory is injected into the agent prompt so the LLM can reference what it knows.

### Saving Memory (Background Thread)

After every 5 messages in agent mode, memory is updated in a background daemon thread:

```python
def save_memory(user_email: str, session_history: list, doc_name: str):
    """
    Compress session history into a short memory text using the LLM.
    Called in a daemon thread — failures are logged but do not affect chat.
    """
    from chat.pipeline import ask_raw

    # Step 1: Summarise the current session
    session_summary_prompt = f"""
    Summarise the key facts about this user based on their conversation:
    {format_history(session_history)}

    Output a 2-3 sentence summary of what you learned about the user.
    Focus on: their goals, language preference, specific topics, location.
    """
    session_summary = ask_raw(session_summary_prompt)

    # Step 2: Merge with existing memory (keeps it under 500 chars)
    existing = load_memory(user_email)
    merge_prompt = f"""
    Existing memory: {existing}
    New session summary: {session_summary}

    Merge into a single paragraph under 500 characters.
    Keep the most recent and useful facts.
    """
    merged_memory = ask_raw(merge_prompt)[:500]  # hard cap at 500 chars

    # Step 3: Save
    AgentMemory.objects.update_or_create(
        user_email=user_email,
        defaults={
            "memory_text": merged_memory,
            "total_sessions": F("total_sessions") + 1,
        }
    )
```

This is called in a **daemon thread** — a background thread that does not block the main request. If it fails (e.g., the LLM is unavailable), the chat is unaffected. The memory just will not be updated for this session.

```python
import threading
t = threading.Thread(target=save_memory, args=(user_email, history, doc_name), daemon=True)
t.start()
```

---

## 7. Enabling Agent Mode

**Step 1:** Make sure email collection is enabled (memory requires an email to use as key):
- Admin → Chat Session Configuration → `collect_email = True` and `verify_email = True`

**Step 2:** Enable agent mode:
- Admin → LLM Configuration → check "Agent mode"
- Save

**Step 3:** Test by asking a question. Look in `app.log` for agent-specific log lines:

```
Agent loop: conversational bypass | q='hello'
Agent tool call | iter=1 | tool=search_document | arg='MCA admission eligibility'
Agent tool call | iter=2 | tool=get_page | arg='12'
Agent done (no tool call) | iter=3 | time=8.72s
Agent memory updated | email=user@example.com
Agent max iterations reached | streaming final answer
```

---

## 8. Log Lines Reference

| Log Line | Meaning |
|----------|---------|
| `Agent loop: conversational bypass` | Greeting detected, tools skipped |
| `Agent tool call \| iter=N \| tool=X \| arg=Y` | Tool being called in iteration N |
| `Agent done (no tool call) \| iter=N` | LLM gave a direct answer |
| `Agent max iterations reached \| streaming final answer` | All 4 iterations used |
| `Agent loop error, falling back to direct ask` | Exception in agent loop, using fallback |
| `Agent memory updated \| email=X` | Memory saved successfully |

---

## What to Do Next

Read [File 11 — Admin Panel Guide](11_admin_panel_guide.md) for a complete walkthrough of every section in the Django admin, including how to upload documents, configure LLM settings, and monitor usage.

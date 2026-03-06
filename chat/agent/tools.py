"""
Agent tool functions.

Each tool takes document/config objects as context and returns a plain string.
Tools are called by the ReAct agent loop when the LLM emits a TOOL_CALL directive.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("chat.pipeline")


def search_document(query: str, doc, cfg) -> str:
    """
    Semantic / BM25 search over the document's RAG chunks.
    Returns the top-3 most relevant pages (same format as RAG context mode).
    """
    if not doc.rag_chunks_path or not Path(doc.rag_chunks_path).exists():
        return "Document search is unavailable (no chunks found). Try get_page() instead."
    from chat.pipeline import retrieve_relevant_context
    result = retrieve_relevant_context(query, doc.rag_chunks_path, cfg.rag_embedding, top_k=3)
    return result or "No relevant content found for that query."


def get_page(page_number: int, doc) -> str:
    """Return the full markdown text of a specific page."""
    if not doc.json_path or not Path(doc.json_path).exists():
        return "Document page data is unavailable."
    try:
        pages_data = json.loads(Path(doc.json_path).read_text(encoding="utf-8"))
        page = next((p for p in pages_data["pages"] if p["page"] == page_number), None)
        if page:
            return page["markdown"]
        total = pages_data.get("total_pages", "?")
        return f"Page {page_number} not found. Document has {total} page(s)."
    except Exception as exc:
        logger.warning("get_page failed: %s", exc)
        return f"Could not retrieve page {page_number}: {exc}"


def list_sections(doc) -> str:
    """
    Return a one-line preview of every page — gives the agent a map of the document
    so it can decide which page to fetch or which search query to use.
    """
    if not doc.json_path or not Path(doc.json_path).exists():
        return "Document page data is unavailable."
    try:
        pages_data = json.loads(Path(doc.json_path).read_text(encoding="utf-8"))
        lines = []
        for p in pages_data["pages"]:
            preview = p["markdown"][:160].replace("\n", " ").strip()
            lines.append(f"Page {p['page']}: {preview}…")
        return "\n".join(lines) if lines else "Document appears to be empty."
    except Exception as exc:
        logger.warning("list_sections failed: %s", exc)
        return f"Could not list sections: {exc}"


# Registry used by the agent loop to dispatch tool calls by name.
TOOLS = {
    "search_document": search_document,
    "get_page":        get_page,
    "list_sections":   list_sections,
}

# Human-readable tool descriptions injected into the agent system prompt.
TOOL_DESCRIPTIONS = """\
search_document("your search query")
  - Searches the document for content relevant to a query.
  - Use this first when you need to find information.
  - Returns the top-3 matching passages.

get_page(page_number)
  - Retrieves the full text of a specific page number (integer).
  - Use when you already know which page has the answer.

list_sections()
  - Returns a one-line preview of every page.
  - Use this when you want an overview before deciding where to search.\
"""

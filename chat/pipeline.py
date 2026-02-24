import os
import tempfile
import time

import ollama
from pathlib import Path
from pdf2image import convert_from_path
from docling.document_converter import DocumentConverter

LLM_MODEL = "llama3.2-vision"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def convert_to_markdown(input_path: str) -> tuple[str, dict]:
    """
    Convert a PDF or image file to a markdown string using docling OCR.

    Returns:
        (combined_markdown, pages_data) where pages_data is a dict:
        {
            "source_file": "<filename>",
            "total_pages": N,
            "pages": [{"page": 1, "markdown": "..."}, ...]
        }
    """
    ext = Path(input_path).suffix.lower()
    source_name = Path(input_path).name
    converter = DocumentConverter()
    page_entries: list[dict] = []

    if ext == ".pdf":
        images = convert_from_path(input_path, dpi=200)
        for page_num, page_img in enumerate(images, start=1):
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                page_img.save(tmp_path, "PNG")
            try:
                result = converter.convert(tmp_path)
                md = result.document.export_to_markdown()
                page_entries.append({"page": page_num, "markdown": md})
            finally:
                os.unlink(tmp_path)

        combined = "\n\n---\n\n".join(
            f"<!-- Page {e['page']} -->\n\n{e['markdown']}" for e in page_entries
        )

    elif ext in IMAGE_EXTENSIONS:
        result = converter.convert(input_path)
        md = result.document.export_to_markdown()
        page_entries.append({"page": 1, "markdown": md})
        combined = md

    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Supported: .pdf, {', '.join(sorted(IMAGE_EXTENSIONS))}"
        )

    pages_data = {
        "source_file": source_name,
        "total_pages": len(page_entries),
        "pages": page_entries,
    }
    return combined, pages_data


def _build_messages(question: str, history: list, markdown_text: str) -> list:
    system_msg = {
        "role": "system",
        "content": (
            "You are a helpful assistant. The following is extracted markdown text from a document.\n"
            "Use ONLY this context to answer the user's questions.\n"
            "If the answer cannot be found in the context, say so clearly.\n\n"
            "## Document Context (Markdown)\n\n"
            f"{markdown_text}"
        ),
    }
    # Trim history to last 20 turns (40 messages) to stay within context window
    trimmed_history = history[-40:]
    return [system_msg] + trimmed_history + [{"role": "user", "content": question}]


def ask_streaming(question: str, history: list, markdown_text: str):
    """
    Generator that yields string tokens from ollama's streaming response.
    Used by the /chat SSE route.
    """
    messages = _build_messages(question, history, markdown_text)
    stream = ollama.chat(model=LLM_MODEL, messages=messages, stream=True)
    for chunk in stream:
        token = chunk.get("message", {}).get("content", "")
        if token:
            yield token


def ask(question: str, history: list, markdown_text: str) -> tuple[str, float]:
    """
    Non-streaming variant — returns (answer, elapsed_seconds).
    Kept for testing/debugging convenience.
    """
    messages = _build_messages(question, history, markdown_text)
    t0 = time.perf_counter()
    response = ollama.chat(model=LLM_MODEL, messages=messages)
    elapsed = time.perf_counter() - t0
    return response["message"]["content"], elapsed

# 05 — OCR and Document Pipeline

## What This File Covers

How a PDF or image file goes from upload to searchable, structured text. This covers all five OCR engines, the `convert_to_markdown()` function step by step, the complete admin upload flow, and the file lifecycle.

**Prerequisites:** File 04 (Database Models) — you need to understand the `Document` model.

---

## 1. What is OCR and Why is it Needed?

OCR stands for **Optical Character Recognition** — the process of looking at an image of text and converting it to machine-readable characters.

There are two types of PDFs:

**Digital PDF** — Created by a word processor or typesetting software (Word, LaTeX, InDesign). The text is already embedded in the file. You can select and copy text in a PDF viewer.

**Scanned PDF** — Created by scanning a physical paper document. Each page is just an image — a photograph of text. There is no selectable text. The computer sees it the same way it sees a photograph of a cat.

LLMs can only read text, not images of text. So before DocChat can answer questions about a scanned document, it must first "read" the images to extract the text. That is what OCR does.

Even for digital PDFs, DocChat benefits from running OCR because tools like Docling produce clean, structured markdown — preserving tables, headings, and layout — rather than raw extracted text which can be jumbled.

---

## 2. The Five OCR Engines

DocChat supports five different engines. The admin chooses which one to use in the LLM Configuration panel.

### Auto (Recommended)
The system detects whether the PDF has a text layer:
- If text can be extracted directly (digital PDF) → use **Docling**
- If no text is found (scanned PDF) → use **Tesseract**

This is the best default for most use cases.

### Docling
A modern Python library that does layout-aware extraction from digital PDFs. It preserves tables, headings, lists, and formatting as clean markdown. Ideal for structured documents like prospectuses, reports, and manuals.

**Weakness:** Does not handle scanned (image-only) PDFs well — it needs an actual text layer.

**First run is slow** (5-10 minutes): Docling downloads AI models on first use. Subsequent runs are fast.

### Tesseract
The classic open-source OCR engine, widely used for decades. DocChat configures it for three languages simultaneously: Hindi (`hin`), Gujarati (`guj`), and English (`eng`).

Before running Tesseract, DocChat preprocesses each image:
1. Convert to grayscale
2. Boost contrast (2×)
3. Apply sharpening

This improves Indic script recognition significantly — Devanagari and Gujarati characters have thin strokes that benefit from contrast enhancement.

**Configuration used:**
- `--oem 3` — use the LSTM neural network engine (most accurate)
- `--psm 6` — assume a uniform block of text

### Gemini Vision
Google Gemini's multimodal capability — it can "see" images and extract text from them. This is the highest-quality option for complex, mixed-script, or poorly scanned documents.

**Requires:** `GEMINI_API_KEY` in `.env`. Each page consumes Gemini API tokens (has cost).

### PDF to Text (pdfplumber)
Direct extraction using `pdfplumber`. No image conversion, no neural networks — just reading the text layer directly. Fastest option.

**Only valid for digital PDFs.** Will return empty or garbage for scanned documents.

---

## 3. The `convert_to_markdown()` Function

This function lives in `chat/pipeline.py` and is the core of the OCR pipeline. It takes a file path and returns the extracted text.

**Input:** path to a PDF or image file (JPG, PNG, TIFF, BMP, WEBP)

**Output:** `(combined_text, pages_data)` where:
- `combined_text` is the full document as a single string
- `pages_data` is a dict mapping page numbers to their markdown text

### Step-by-Step Flow

```
convert_to_markdown(file_path, cfg)
        │
        ├─── Read cfg.ocr_engine
        │
        ├─── Is it a PDF?
        │         │
        │    Yes   ├─── engine == "pdftext" → pdfplumber direct extract → done
        │          │
        │          ├─── engine == "auto" → _has_text_layer(file_path)?
        │          │         ├── Yes → use Docling
        │          │         └── No  → use Tesseract
        │          │
        │          └─── convert_from_path() → one PNG per page
        │                    │
        │                    └─── For each page PNG:
        │                              ├─── Docling: DocumentConverter().convert()
        │                              ├─── Tesseract: pytesseract.image_to_string()
        │                              └─── Gemini Vision: client.models.generate_content()
        │
        └─── Is it an image (JPG, PNG, etc.)?
                    │
                    └─── Single OCR call on the image
                              └─── same engine logic as above

        └─── Combine all page texts
        └─── Return (combined_text, pages_data)
```

### The Text Layer Detection

```python
def _has_text_layer(pdf_path: str) -> bool:
    """Returns True if the PDF has extractable text on at least one page."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:3]:  # check first 3 pages
            if page.extract_text():
                return True
    return False
```

It only checks the first 3 pages for speed. If any page has extractable text, the whole document is treated as digital.

### Tesseract Preprocessing

```python
def _preprocess_for_tesseract(img):
    img = img.convert("L")                      # grayscale
    img = ImageEnhance.Contrast(img).enhance(2.0)  # boost contrast
    img = img.filter(ImageFilter.SHARPEN)       # sharpen
    return img
```

This improves accuracy on Indic scripts significantly.

### OCR Resolution

DocChat uses **200 DPI** for Docling and Gemini Vision, and **300 DPI** for Tesseract. Higher DPI = larger images = more detail for Tesseract's character recognition — important for fine Indic script strokes.

```python
images = convert_from_path(pdf_path, dpi=300)  # for Tesseract
```

---

## 4. The Admin Upload Flow — End to End

When an admin uploads a document in the Django admin panel, here is exactly what happens:

```
Admin submits document form
          │
          ▼
admin.py → DocumentAdmin.save_model()
          │
          ├─── Document record created with status="pending"
          │
          ├─── File saved to: uploads/<uuid>.<extension>
          │
          ├─── convert_to_markdown(file_path, cfg)  ← OCR runs here
          │         │
          │         └─── Returns (combined_text, pages_data)
          │
          ├─── Decide context_mode:
          │         ├─── len(combined_text) < CONTEXT_CHAR_THRESHOLD (12,000)?
          │         │         └─── context_mode = "full"
          │         └─── else → context_mode = "rag"
          │
          ├─── Save markdown to: markdown_cache/<uuid>.md
          │
          ├─── Save page JSON to: markdown_cache/<uuid>.json
          │         └─── { "1": "page 1 text", "2": "page 2 text", ... }
          │
          ├─── Build RAG chunks:
          │         └─── One chunk per page (or split_text_into_pages() for pasted text)
          │
          ├─── Store chunks in Qdrant:
          │         └─── collection named: doc_<uuid>
          │
          ├─── (if provider=="gemini" and use_gemini_cache and context_mode=="full")
          │         └─── create_gemini_cache(combined_text, gemini_model)
          │                   └─── Returns cache_name (stored in Document.gemini_cache_name)
          │
          ├─── Delete temp file: uploads/<uuid>.<extension>
          │
          ├─── Document.status = "ready"
          │         (or "error" if anything above failed)
          │
          └─── Document saved to database
```

### For Pasted Text (no file)

When the admin chooses "Paste Text" instead of uploading a file:

- The text is split into pages using `split_text_into_pages()`, which chunks on paragraph boundaries into ~1000-character sections
- No OCR needed — the text is already readable
- The rest of the flow (context mode, Qdrant, Gemini cache) is identical

---

## 5. File Lifecycle

Understanding where files live helps when debugging or doing cleanup:

| Location | Created When | Deleted When | Content |
|----------|-------------|--------------|---------|
| `uploads/<uuid>.ext` | Admin uploads file | Immediately after OCR completes | Original uploaded file |
| `markdown_cache/<uuid>.md` | OCR completes | Admin deletes the Document | Full extracted markdown text |
| `markdown_cache/<uuid>.json` | OCR completes | Admin deletes the Document | Per-page structure `{"1": "...", "2": "..."}` |
| `qdrant_storage/` | First RAG chunk stored | Admin deletes the Document (collection removed) | Vector embeddings for RAG |

> **The `uploads/` folder is always temporary.** Files there are only needed for the few seconds it takes to run OCR. If a file remains in `uploads/` after OCR, it is either from a failed upload or a bug — it is safe to delete manually.

> **The `markdown_cache/` folder holds the actual data.** Losing these files means the document's text is gone (though the Document record in the database still exists). Always back up `markdown_cache/` in production.

---

## 6. How to Test OCR

**Step 1:** Start the development server:
```bash
python manage.py runserver
```

**Step 2:** Go to `http://127.0.0.1:8000/admin/` and log in.

**Step 3:** Click "Documents" → "Add Document". Upload a PDF.

**Step 4:** Save. The page will reload with the document showing status "Pending".

**Step 5:** Refresh the page after a few seconds. The status should change to "Ready" (green). If it shows "Error" (red), click the document to see the `error_message` field.

**Step 6:** Check `app.log` for detailed information:

```
OCR start | engine=docling | file=mydoc.pdf
Docling OCR: 2.31s, 8452 chars
OCR complete | pages=12 | chars=8452 | mode=full | time=28.4s
```

Key log lines to look for:
- `OCR start | engine=...` — confirms which engine ran
- `OCR complete | pages=... | chars=... | mode=...` — shows results
- `Qdrant store | collection=... | chunks=...` — confirms RAG chunks were stored
- Any `ERROR` lines — indicate what went wrong

---

## 7. Common OCR Issues and Fixes

**"TesseractNotFoundError" or "tesseract is not installed or it's not in your PATH"**
Tesseract binary is not on your system PATH. On macOS: `brew install tesseract tesseract-lang`. On Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-guj`. Restart the terminal after installation.

**"PDFInfoNotInstalledError" or "Unable to get page count"**
Poppler is not installed. `pdf2image` needs the `pdftoppm` command from Poppler. Install it (`brew install poppler` on macOS, `apt install poppler-utils` on Linux) and restart.

**Docling takes 10+ minutes on first run**
This is expected. Docling downloads AI models from HuggingFace on first use. After the download, all subsequent runs are fast (2-5 seconds per page).

**Tesseract produces garbled text for Gujarati**
The Gujarati language pack is not installed. Verify: `tesseract --list-langs` should show `guj`. If not, install it.

**"Error" status with "No text extracted"**
For scanned documents with poor image quality, OCR may fail. Try switching to Gemini Vision in LLM Configuration — it handles poor-quality scans better. Alternatively, try increasing the scan resolution (300+ DPI).

**Gemini Vision OCR is slow**
Gemini Vision sends each page to the Gemini API — each page is a separate API call. A 50-page document makes 50 API calls. For large documents, Docling or Tesseract is faster.

---

## What to Do Next

Read [File 06 — RAG Retrieval System](06_rag_retrieval_system.md) to understand how DocChat finds the right pages to answer each question when documents are too large to send in full.

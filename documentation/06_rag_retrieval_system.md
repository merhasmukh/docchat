# 06 — RAG Retrieval System

## What This File Covers

Why large documents cannot be sent to an LLM in full, what RAG is and how it solves this, how DocChat implements three different retrieval methods, and how Qdrant stores and searches embeddings.

**Prerequisites:** File 05 (OCR & Document Pipeline) — documents must already be processed into chunks.

---

## 1. The Context Window Problem

Every LLM has a **context window** — the maximum amount of text it can process in a single request. Exceeding it causes an error.

Common context window sizes:
- Gemini 2.0 Flash: ~1 million tokens (~750,000 words)
- Llama 3.1 8B (via Ollama): 128,000 tokens (~96,000 words)
- Sarvam AI: ~7,168 tokens (~5,376 words) — quite small

A typical university prospectus might be 200 pages, roughly 150,000 words (200,000 tokens). That fits in Gemini but not in most Ollama models, and definitely not in Sarvam.

Even when it fits, sending the entire document on every single question wastes money. If the user asks "What are the MCA fees?", there is no reason to send all 200 pages — just the fees page.

**DocChat's solution:** For documents above a character threshold, only retrieve and send the most relevant pages for each question. This is RAG.

---

## 2. What is RAG?

**RAG = Retrieval-Augmented Generation**

The three steps:

1. **Retrieve** — Find the pages most relevant to the user's question
2. **Augment** — Add those pages to the LLM prompt as context
3. **Generate** — The LLM answers using only the retrieved context

Library analogy: instead of giving the LLM the entire library to read (full context), you first go to the card catalogue, find the relevant books, and give the LLM only those books (RAG).

---

## 3. Full Context Mode vs RAG Mode

DocChat decides which mode to use at **document upload time**, based on the extracted text length:

```python
# dochat/settings.py
CONTEXT_CHAR_THRESHOLD = 12_000   # ~3,000 tokens
```

| Condition | Mode Set | What Happens at Chat Time |
|-----------|----------|--------------------------|
| `len(text) < 12,000 chars` | `full` | Entire document sent to LLM with every question |
| `len(text) >= 12,000 chars` | `rag` | Top-3 most relevant pages retrieved and sent |

The mode is stored in `Document.context_mode`.

The admin can override this in LLM Configuration → Context Mode:
- **Auto** — use the mode computed at upload time (default)
- **Full** — always send the whole document (useful for small-to-medium docs with Gemini)
- **RAG** — always use retrieval (useful to save tokens even on smaller docs)

---

## 4. Chunking — How Documents are Split

For RAG to work, the document must be split into retrievable pieces called "chunks". DocChat uses **page-level chunking** — each page of the PDF becomes one chunk.

**For uploaded PDFs:**
Each page from the OCR process becomes one chunk. A 50-page PDF → 50 chunks.

**For pasted text (no file):**
DocChat splits the text into ~1000-character chunks at paragraph boundaries using `split_text_into_pages()`. This keeps related sentences together.

Each chunk is stored with:
- Its page number
- Its text content
- Its embedding vector (if using a vector-based retrieval method)

---

## 5. The Three Retrieval Methods

DocChat offers three ways to find relevant chunks. You choose in Admin → LLM Configuration → RAG Embedding.

### Method 1: BM25 (Keyword Search)

BM25 (Best Match 25) is a classic information retrieval algorithm — the same underlying algorithm used by search engines like Elasticsearch. It scores each chunk based on word frequency and document length.

**How it works:**
1. Tokenise each chunk into words
2. When a question arrives, score each chunk by how well its words match the question words
3. Return the top-3 chunks by score

**Pros:** Fast, zero cost, no models to download, works offline.

**Cons:** Only matches exact keywords. A Gujarati question will not match an English-language chunk (no cross-language understanding). "fees" will not match "tuition" unless both words appear together.

**Best for:** Same-language documents where the user's question uses the same words as the document.

### Method 2: Multilingual Local (sentence-transformers)

Uses the `paraphrase-multilingual-MiniLM-L12-v2` model from HuggingFace — a neural network that converts any text into a 384-number vector (an "embedding") such that texts with similar meanings have similar vectors.

**First use:** Downloads ~90 MB model. After that, it is cached locally and works offline.

**How it works:**
1. At upload time: compute an embedding vector for each chunk
2. At query time: compute an embedding for the user's question
3. Find the chunks whose vectors are most similar (cosine similarity)
4. Return top-3

**Cross-language magic:** Because the model was trained on 50+ languages simultaneously, a Gujarati question ("MCA fees ketli chhe?") produces a vector close to an English answer about MCA fees, even though the words are completely different.

**Pros:** Works offline, free, cross-language, ~90 MB model, reasonable quality.

**Cons:** Lower quality than Gemini embeddings; 384-dimension vectors vs 768.

**Best for:** Most production use cases — good balance of quality, cost, and offline operation.

### Method 3: Gemini Embedding (Cloud API)

Uses Google's `text-multilingual-embedding-002` model via the Gemini API. Produces 768-dimension vectors — richer, higher-quality embeddings.

**How it works:** Same as multilingual_local, but calls the Gemini API for every embedding computation. At upload time, each chunk makes one API call. At query time, each question makes one API call.

**Pros:** Highest quality cross-language retrieval.

**Cons:** Requires internet, requires `GEMINI_API_KEY`, has per-token cost, slower than local.

**Best for:** When retrieval quality is the top priority and cost is acceptable.

---

## 6. Comparison Table

| Feature | BM25 | Multilingual Local | Gemini Embedding |
|---------|------|--------------------|-----------------|
| Internet required | No | No | Yes |
| API key required | No | No | Yes (Gemini) |
| Cost | Free | Free | Per token |
| Model download | None | ~90 MB (once) | None |
| Speed (per query) | Very fast | Fast (~0.1s) | Slower (~0.5s) |
| Cross-language | No | Yes | Yes |
| Quality | Good (same-lang) | Good | Best |
| Best for | English-only docs | Most use cases | Highest quality |

---

## 7. Qdrant — The Vector Database

**Qdrant** is the database that stores RAG chunks and their embedding vectors. It is embedded — no separate server process — and stores data on disk in the `qdrant_storage/` folder.

### Creating a Client

```python
from qdrant_client import QdrantClient

client = QdrantClient(path=str(settings.QDRANT_PATH))
```

DocChat uses a module-level singleton — the client is created once and reused across requests.

### Storing Chunks

When a document is processed, its chunks are stored in a Qdrant collection named after the document's UUID (e.g., `doc_f47ac10b`):

```python
def store_rag_chunks_qdrant(doc_uuid, chunks, cfg):
    collection_name = f"doc_{doc_uuid}"

    # Create or recreate the collection
    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )

    # Store each chunk as a Qdrant "point"
    points = [
        PointStruct(
            id=i,
            vector=compute_embedding(chunk["text"], cfg),
            payload={"page": chunk["page"], "text": chunk["text"]},
        )
        for i, chunk in enumerate(chunks)
    ]
    client.upsert(collection_name=collection_name, points=points)
```

For BM25, a dummy `[0.0]` vector is stored (Qdrant requires a vector but BM25 does not use it — the actual scoring is done in Python).

### Retrieving Relevant Chunks

At query time, DocChat retrieves the top-3 most relevant chunks:

```python
def retrieve_relevant_context_qdrant(question, doc, cfg):
    collection_name = doc.qdrant_collection

    if cfg.rag_embedding == "bm25":
        # Scroll all chunks, score with BM25 in Python
        all_points = client.scroll(collection_name=collection_name, limit=1000)
        texts = [p.payload["text"] for p in all_points[0]]
        tokenized = [t.split() for t in texts]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(question.split())
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:3]
        return [texts[i] for i in top_indices]

    else:
        # Vector similarity search
        query_vector = compute_embedding(question, cfg)
        results = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=3,
        )
        return [r.payload["text"] for r in results.points]
```

---

## 8. The Short-Query Enrichment Trick

A common problem: if a user's follow-up question is very short ("ok for mca?", "and fees?"), the RAG search has too little to work with and retrieves irrelevant chunks.

DocChat solves this by prepending the previous question when the current question is 3 words or fewer:

```python
def _rag_query(question: str, history: list) -> str:
    words = question.strip().split()
    if len(words) <= 3 and history:
        # Find the last user message
        for msg in reversed(history):
            if msg.get("role") == "user":
                prev = msg.get("content", "")
                return f"{prev} {question}"
    return question
```

**Example:**
- Previous question: "BCA ma admission leva su joyeye?"
- Current question: "ok for mca?"
- RAG query used: "BCA ma admission leva su joyeye? ok for mca?"

This gives the retrieval system much more context to work with.

---

## 9. How Context is Assembled for the LLM

After retrieval, the top-3 chunks are joined with separators:

```python
context = "\n\n---\n\n".join(top_chunks)
```

This `context` string is then inserted into the LLM prompt (see File 07). The LLM is instructed to answer the question using only the provided context.

The prompt also tells the LLM which pages were retrieved, so the answer can reference specific sections.

---

## 10. Testing the RAG System

**Step 1:** Upload a large PDF (one where extracted text exceeds 12,000 characters).

**Step 2:** Check in the admin that the Document shows `context_mode = rag`.

**Step 3:** Start the dev server and ask a question about the document.

**Step 4:** Check `app.log` for the RAG retrieval log line:

```
RAG retrieval | method=multilingual_local | selected_pages=[3, 7, 12] | time=0.23s
```

**Step 5:** Check that the selected pages are actually relevant to your question. If they are off-topic, try a different embedding method or rephrase your question.

**Common log lines:**

```
RAG retrieval | method=bm25 | selected_pages=[2, 5] | time=0.01s
RAG retrieval | method=multilingual_local | selected_pages=[4, 8, 11] | time=0.18s
Qdrant store | collection=doc_abc123 | chunks=42 | method=multilingual_local | time=12.3s
```

---

## What to Do Next

Read [File 07 — LLM Providers](07_llm_providers.md) to understand how the retrieved context is used to build prompts and how each LLM provider streams back its answer.

# The Core Pipeline: Extraction, Chunking, and Embedding

In the previous step, you built the Django foundation (the database and admin panel). Now, we need to build the "Brain Interface"—the pipeline that turns a dumb PDF into smart, search-ready data.

This is the hardest but most important part of a RAG Chatbot.

---

## 🏭 1. The Pipeline Concept

Imagine a factory assembly line. A raw, unreadable PDF enters the factory. By the time it leaves the factory, it needs to be organized into a highly optimized, searchable index.

In a Django app, developers typically create a dedicated script (e.g., `pipeline.py`) to handle this entire process the moment a user hits "Upload Document".

---

## 📖 2. Stage One: Text Extraction (OCR)

AI cannot read an image or a raw PDF file format. AI only reads pure text (Strings).

### Digital PDFs vs. Scanned Images
- **Digital PDFs:** These are documents where you can highlight the text with your cursor. They are easy. You can use standard Python libraries like `pdfplumber` or `PyMuPDF` to yank the text out instantly.
- **Scanned PDFs:** These are just photographs of paper (like an old textbook). You cannot highlight the text. To read these, you need **OCR** (Optical Character Recognition).
- **OCR Tools:** Tools like `Tesseract` or `Docling` look at the pixels in the image and guess what the letters are.

**Your Goal:** Your Django `pipeline.py` script needs to open the uploaded file, determine if it needs OCR, extract every single word, and output one massive, clean String of text.

---

## 🔪 3. Stage Two: Chunking (Splitting the Text)

If you hand an AI a 500-page book and ask a question, it will likely crash or cost $10 in API fees per question. You must break the massive string of text into "Chunks."

### How to Chunk Properly:
- **Size:** A chunk is typically 500 to 1,000 characters long (about 2-3 paragraphs).
- **Overlap:** You don't want to cut a sentence in half! So, chunkers use "overlap." Chunk A might end with "The return policy is...", and Chunk B will start with "...The return policy is 30 days." That way, no context is lost.
- **Tools:** Libraries like `LangChain` have built-in "Recursive Character Text Splitters" that do this math for you automatically.

**Your Goal:** Your script takes the massive String from Stage One and outputs a Python List of 1,000 smaller Strings (chunks).

---

## 🔢 4. Stage Three: Embedding (Turning Text into Math)

This is the magic of modern AI. Computers don't understand the word "Dog." But they *do* understand coordinates on a graph.

### What is an Embedding?
An embedding model (provided by Google Gemini, OpenAI, or a local tool like Sentence-Transformers) reads your chunk of text and translates it into a list of numbers (a vector).
- Example: `[0.142, -0.923, ..., 0.551]`
- These numbers represent the *meaning* of the text. Because "Dog" and "Puppy" have similar meanings, their coordinates will be placed right next to each other on the graph.

**Your Goal:** Your script loops through all 1,000 chunks, sends each one to the Embedder, and receives 1,000 lists of coordinates.

---

## 🗄️ 5. Stage Four: The Vector Database

A standard SQL database (like SQLite or PostgreSQL) is terrible at finding "ideas that mean roughly the same thing." SQL looks for exact keyword matches.

We need a **Vector Database** (like ChromaDB, Qdrant, or Pinecone).

### Storing the Data
Your script will save three things into the Vector Database:
1. The original text of the chunk (`"The company was founded in 1992."`)
2. The coordinates/embedding for that chunk (`[0.8, -0.2, 0.4...]`)
3. Metadata (Which page number did this come from? What is the Document ID?)

**Your Goal:** When the pipeline finishes, the massive String of text is fully securely stored as mathematical coordinates in your Vector Database.

---

## 🎯 6. Summary of the Pipeline

When an admin uploads a document in Django, your `pipeline.py` fires off:
1. Extract text (`pdfplumber` / `Tesseract`)
2. Cut into paragraphs (LangChain Chunking)
3. Convert paragraphs into numbers (Embedding API)
4. Save numbers to a fast search engine (Vector Database)

*The document is now ready to be queried! In the final step, we will connect the UI and actually chat with the Vector Database.*

# Next Steps: Laying the Django Foundation

Once you understand the blueprint of an AI RAG Chatbot, the very next step is to set up your Django project and build the critical database models.

These notes will guide you conceptually through building the scaffolding of your app.

---

## 🛠️ 1. Project Initialization

Before writing AI logic, you must start the Django engine.

- **Create a Virtual Environment:** Always isolate your project's Python packages so they don't interfere with other projects on your computer.
- **Install Django & AI Tools:** Use `pip` to install `django`, `djangorestframework`, `pdfplumber` (for OCR), and your AI tool (like `google-generativeai`).
- **Start the Project:** Run the `django-admin startproject` command to generate the core folder structure.
- **Start the App:** Run `python manage.py startapp chat` to create a dedicated app folder where all your chatbot logic will live.

**Goal:** You should have a running web server that you can access at `127.0.0.1:8000` (even if it just says "The install worked successfully!").

---

## 🗄️ 2. Designing the Database Models

The database is the memory of your app. For a basic RAG Chatbot, you really only need two tables in your database (defined in Django's `models.py`):

### 1. The `Document` Model
You need a place to securely store the PDFs that users upload.
- **File Field:** To hold the actual `.pdf` file.
- **Text Cache:** A text field to store the extracted OCR text so you don't have to re-read the PDF every time a user asks a question.
- **Status:** A status field (e.g., "Pending", "Processing", "Ready") so the frontend knows when the AI is finished reading the document.

### 2. The `ChatMessage` Model
You need to record the conversation history so the AI can remember what was said 5 minutes ago.
- **Session ID:** To link a sequence of messages together.
- **Role:** A tag indicating who is speaking (e.g., "User" or "AI").
- **Content:** The text of the message (the user's question, or the AI's answer).
- **Timestamp:** When the message was sent, to keep the chat in the correct order.

**Goal:** After defining these classes in `models.py`, you run Django's `makemigrations` and `migrate` commands to build the actual tables in your SQLite database.

---

## 🔐 3. The Django Admin Panel

One of the main reasons to use Django is its free, secure Admin Panel. 

- **Register Models:** You will register both `Document` and `ChatMessage` in `admin.py`.
- **Create Superuser:** Run `python manage.py createsuperuser` to make an admin account.
- **Upload Without Code:** Before you even build a front-end UI for users, you can log into the Admin panel and upload your first PDF directly into the database to verify it works.

---

## 🧩 4. Preparing for the Pipeline

With the foundation built, your next conceptual step will be building the "Pipeline" (usually inside a file called `pipeline.py` or similar).

The Pipeline is the factory line where the magic happens:
1. It listens for a new `Document` upload.
2. It sends the PDF to the OCR tool (like `pdfplumber`).
3. It chops the text into chunks.
4. It sends the chunks to an "Embedder" to get coordinates.
5. It saves those coordinates into a Vector Database.

**Next Guide:** *We will dive deeper into writing the Pipeline logic and handling text extraction.*

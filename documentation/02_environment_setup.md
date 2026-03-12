# 02 — Environment Setup

## What This File Covers

Getting your machine ready to run DocChat from scratch. This covers Python installation, creating an isolated environment, installing all dependencies, setting up system tools (Poppler, Tesseract, Ollama), configuring secrets in a `.env` file, and running the development server for the first time.

**Prerequisites:** File 01 (Introduction) — no code yet.

---

## 1. Install Python 3.10 or Higher

DocChat requires Python 3.10 or newer. Check if you already have it:

```bash
python --version
```

If you see `Python 3.10.x` or higher, you can skip to Section 2. If not, install it:

### macOS

The easiest way is with Homebrew (a macOS package manager):

```bash
# Install Homebrew if you do not have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.11
brew install python@3.11
```

Verify the installation:

```bash
python3 --version
# Expected output: Python 3.11.x
```

### Ubuntu / Debian Linux

```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip -y
```

### Windows

1. Go to [python.org/downloads](https://python.org/downloads)
2. Download Python 3.11 (or newer)
3. Run the installer. **Important:** Check the box that says "Add Python to PATH" before clicking Install.
4. Open a new Command Prompt and verify:

```cmd
python --version
```

---

## 2. Create and Activate a Virtual Environment

A virtual environment is an isolated Python sandbox. It keeps DocChat's dependencies separate from any other Python projects on your machine, preventing version conflicts.

Think of it like a clean room: you install exactly what DocChat needs there, and it does not interfere with anything else.

### macOS / Linux

```bash
# Navigate to where you want to create the project folder
cd ~/projects   # or wherever you keep projects

# Clone the repository
git clone <your-repo-url> docchat
cd docchat

# Create the virtual environment inside the project folder
python3 -m venv venv

# Activate it
source venv/bin/activate
```

You will know it is activated when your terminal prompt changes to show `(venv)` at the start:

```
(venv) your-name@machine docchat %
```

### Windows

```cmd
cd C:\projects
git clone <your-repo-url> docchat
cd docchat

python -m venv venv
venv\Scripts\activate
```

Your prompt will show `(venv)` when activated.

> **Important:** You must activate the virtual environment every time you open a new terminal and want to work on DocChat. If you see errors about missing packages, the first thing to check is whether `(venv)` is showing in your prompt.

---

## 3. Install Python Dependencies

With the virtual environment activated, install all Python packages:

```bash
pip install -r requirements.txt
```

This will take a few minutes on first run. Here is what each major package does:

| Package | Purpose |
|---------|---------|
| `django` | The web framework — URL routing, views, admin, ORM |
| `djangorestframework` | Adds REST API tools (`@api_view`, `Response`) |
| `drf-spectacular` | Auto-generates OpenAPI docs at `/api/docs/` |
| `python-dotenv` | Loads `.env` file into environment variables |
| `ollama` | Python client for the Ollama local LLM server |
| `google-genai` | Google Gemini API client (LLM + embeddings + vision) |
| `docling` | Layout-aware OCR for digital PDFs → clean markdown |
| `pdf2image` | Converts PDF pages to PNG images for OCR |
| `pytesseract` | Python wrapper for Tesseract OCR engine |
| `Pillow` | Image processing (grayscale, contrast, sharpen for OCR) |
| `pdfplumber` | Direct text extraction from digital PDFs |
| `rank_bm25` | BM25 keyword search algorithm for RAG retrieval |
| `sentence-transformers` | Multilingual sentence embedding model (offline) |
| `qdrant-client` | Embedded vector database client |
| `mysqlclient` | MySQL database driver (needed for production) |

> **If a package fails to install:** Read the error message carefully. Most failures are due to missing system-level tools. The most common ones are covered in the next sections.

---

## 4. Install Poppler (PDF to Image Conversion)

`pdf2image` (used to convert PDF pages to PNG images for OCR) requires Poppler, a system-level PDF library. Python's `pip` cannot install this — it must be installed separately.

### macOS

```bash
brew install poppler
```

### Ubuntu / Debian Linux

```bash
sudo apt install poppler-utils -y
```

### Windows

1. Download a prebuilt Poppler binary from [github.com/oschwartz10612/poppler-windows/releases](https://github.com/oschwartz10612/poppler-windows/releases)
2. Extract it to a folder like `C:\poppler`
3. Add `C:\poppler\Library\bin` to your System PATH (Search → "Environment Variables" → Path → Edit → New)
4. Restart your terminal

**Verify Poppler is working:**

```bash
pdftoppm -v
# Expected output: pdftoppm version 24.x.x (or similar)
```

---

## 5. Install Tesseract OCR + Language Packs

Tesseract is the open-source OCR engine used for scanned documents. DocChat uses it with Hindi (`hin`) and Gujarati (`guj`) language packs.

### macOS

```bash
brew install tesseract tesseract-lang
```

`tesseract-lang` installs all language packs including Hindi and Gujarati.

### Ubuntu / Debian Linux

```bash
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-guj tesseract-ocr-eng -y
```

### Windows

1. Download the installer from [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)
2. During installation, under "Additional language data", select Hindi and Gujarati
3. Note the installation path (e.g., `C:\Program Files\Tesseract-OCR`)
4. Add that path to your System PATH

**Verify Tesseract and language packs:**

```bash
tesseract --version
# Expected: tesseract 5.x.x

tesseract --list-langs
# Expected to include: guj, hin, eng
```

> **If Hindi or Gujarati are not listed:** The language data files are missing. On Linux, install the `tesseract-ocr-hin` and `tesseract-ocr-guj` packages. On Windows, re-run the installer and select the languages.

---

## 6. Install Ollama (Optional — for Local LLM)

Ollama lets you run open-source LLMs locally on your machine — no API key, no internet required, completely free and private. Skip this section if you plan to use Gemini or Sarvam AI exclusively.

**System requirement:** At least 8 GB RAM for small models (8B parameters). GPU is not required but makes it significantly faster.

### macOS / Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Windows

Download the Ollama installer from [ollama.com](https://ollama.com) and run it.

**Start Ollama (must be running when DocChat uses it):**

```bash
ollama serve
```

Leave this running in a separate terminal window.

**Pull a model to use:**

```bash
# A good general-purpose model with vision capabilities
ollama pull llama3.2-vision

# Or a lighter text-only model
ollama pull llama3.1:8b
```

**Verify:**

```bash
ollama list
# Should show the model you just pulled
```

> **Note:** The first `ollama pull` downloads the model (several GB). This is a one-time download.

---

## 7. Create the `.env` File

The `.env` file stores secret configuration values — API keys, database credentials, email passwords. **Never commit this file to git.** (The `.gitignore` already excludes it.)

Create the file in the project root (same folder as `manage.py`):

```bash
# macOS / Linux
touch .env
```

Open it in your text editor and add the following. Fill in only what you need:

```ini
# Django secret key — generate a random one (see below)
SECRET_KEY=your-random-secret-key-here

# Database engine: sqlite (default), mysql, or postgres
DB_ENGINE=sqlite

# Required only if DB_ENGINE=mysql or postgres
DB_NAME=docchat
DB_USER=root
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=3306

# Google Gemini API key (required if using Gemini as LLM or OCR)
GEMINI_API_KEY=

# Sarvam AI API key (required if using Sarvam AI)
SARVAM_API_KEY=

# Gmail SMTP (required for email OTP verification)
EMAIL_HOST_USER=your-gmail@gmail.com
EMAIL_APP_PASSWORD=your-16-char-app-password
```

### How to Generate a SECRET_KEY

Run this in your terminal (with the virtualenv activated):

```bash
python -c "import secrets; print(secrets.token_hex(50))"
```

Copy the output and paste it as the value of `SECRET_KEY`.

### How to Get a Gemini API Key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Sign in with your Google account
3. Click "Get API key" → "Create API key"
4. Copy the key and paste it as `GEMINI_API_KEY`

### How to Create a Gmail App Password

Gmail does not allow regular password login for apps. You need an App Password:

1. Go to your Google Account → **Security**
2. Make sure **2-Step Verification** is turned on (required for App Passwords)
3. Search for "App Passwords" in the Security section
4. Select "Mail" as the app, then click Generate
5. Copy the 16-character password (no spaces)
6. Paste it as `EMAIL_APP_PASSWORD`

> **Note:** For development, you can skip the email setup entirely and use Django's console email backend (see File 09). The OTP will print in your terminal instead of being emailed.

---

## 8. Run Migrations and Create a Superuser

Django uses "migrations" to create database tables. Think of a migration as a blueprint that Django uses to build the database structure.

**Create all tables:**

```bash
python manage.py migrate
```

Expected output (truncated):

```
Operations to perform:
  Apply all migrations: admin, auth, chat, contenttypes, sessions
Running migrations:
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  ...
  Applying chat.0001_initial... OK
```

**Create an admin user:**

```bash
python manage.py createsuperuser
```

You will be prompted for a username, email, and password. Remember these — you need them to log into the admin panel.

**Start the development server:**

```bash
python manage.py runserver
```

Expected output:

```
Django version 5.x.x, using settings 'dochat.settings'
Starting development server at http://127.0.0.1:8000/
Quit the server with CONTROL-C.
```

Open your browser and go to `http://127.0.0.1:8000/`. You should see the DocChat chat UI (it will say "No document loaded" until you upload one via the admin panel).

Go to `http://127.0.0.1:8000/admin/` and log in with the superuser credentials you just created.

---

## 9. Verification Checklist

Go through this table to confirm everything is working:

| Check | How to Verify | Expected Result |
|-------|--------------|-----------------|
| Python version | `python --version` | `Python 3.10+` |
| Virtual env active | Check terminal prompt | `(venv)` prefix shown |
| Django installed | `python -c "import django; print(django.__version__)"` | `5.x.x` |
| Poppler installed | `pdftoppm -v` | Shows version number |
| Tesseract installed | `tesseract --version` | `tesseract 5.x.x` |
| Hindi/Gujarati langs | `tesseract --list-langs` | `hin` and `guj` listed |
| Migrations applied | `python manage.py migrate` | "No migrations to apply" |
| Dev server running | Visit `http://127.0.0.1:8000/` | DocChat UI loads |
| Admin panel accessible | Visit `http://127.0.0.1:8000/admin/` | Login page appears |

---

## Common Mistakes and Fixes

**"ModuleNotFoundError: No module named 'django'"**
You forgot to activate the virtual environment. Run `source venv/bin/activate` (macOS/Linux) or `venv\Scripts\activate` (Windows).

**"pdf2image.exceptions.PDFInfoNotInstalledError"**
Poppler is not installed or not on PATH. Reinstall Poppler and restart your terminal.

**"TesseractNotFoundError"**
Tesseract binary is not on PATH. Check that `tesseract --version` works in a fresh terminal. On Windows, ensure the Tesseract install directory is in your System PATH.

**"Error: No such file or directory: 'db.sqlite3'"**
You have not run `python manage.py migrate` yet. Run it now.

**"django.core.exceptions.ImproperlyConfigured: The SECRET_KEY setting must not be empty"**
Your `.env` file is missing or the `SECRET_KEY` line is blank. Check the file exists and has a value.

---

## What to Do Next

Read [File 03 — Django Project Structure](03_django_project_structure.md) to understand how every file in the project is organised and what each one does.

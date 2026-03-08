import datetime as _dt
import secrets as _secrets

from django.db import models
from django.utils import timezone as _tz


# ── Pricing ────────────────────────────────────────────────────────────────────

class ModelPricing(models.Model):
    PROVIDER_CHOICES = [("ollama", "Ollama"), ("gemini", "Gemini"), ("sarvam", "Sarvam AI")]

    provider                 = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    model_name               = models.CharField(max_length=100, help_text="e.g. gemini-2.0-flash")
    input_price_per_million  = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="INR per 1 million input tokens",
    )
    output_price_per_million = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="INR per 1 million output tokens",
    )
    cache_read_price_per_million = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="INR per 1M tokens read from cache (cheaper than standard input). Gemini only.",
    )
    cache_storage_price_per_million_per_hour = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="INR per 1M cached tokens per hour of storage. Gemini only.",
    )
    is_active  = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("provider", "model_name")]
        verbose_name        = "Model Pricing"
        verbose_name_plural = "Model Pricing"

    def __str__(self):
        return f"{self.provider}/{self.model_name} (₹{self.input_price_per_million} in / ₹{self.output_price_per_million} out)"


# ── Cost tracking ──────────────────────────────────────────────────────────────

class ChatSession(models.Model):
    session_key         = models.CharField(max_length=40, unique=True)
    user_name           = models.CharField(max_length=200, blank=True, default="")
    user_email          = models.CharField(max_length=254, blank=True, default="")
    document_name       = models.CharField(max_length=500, blank=True)
    started_at          = models.DateTimeField(auto_now_add=True)
    last_activity       = models.DateTimeField(auto_now=True)
    message_count           = models.IntegerField(default=0)
    total_input_tokens      = models.BigIntegerField(default=0)
    total_output_tokens     = models.BigIntegerField(default=0)
    total_tokens            = models.BigIntegerField(default=0)
    avg_tokens_per_message           = models.FloatField(default=0)
    total_cached_input_tokens        = models.BigIntegerField(default=0)
    total_cost                       = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cache_read_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cache_storage_cost         = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    avg_cost_per_message             = models.DecimalField(max_digits=14, decimal_places=6, default=0)

    class Meta:
        verbose_name = "Chat Session"
        ordering     = ["-last_activity"]

    def __str__(self):
        return f"{self.session_key[:12]}… | {self.document_name or 'no doc'} | ₹{self.total_cost}"


class ChatMessage(models.Model):
    session               = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="messages"
    )
    created_at            = models.DateTimeField(auto_now_add=True)
    provider              = models.CharField(max_length=20)
    model_name            = models.CharField(max_length=100)
    question              = models.TextField()
    answer                = models.TextField()
    input_tokens          = models.IntegerField(default=0)
    output_tokens         = models.IntegerField(default=0)
    total_tokens          = models.IntegerField(default=0)
    tokens_estimated      = models.BooleanField(
        default=False,
        help_text="True when Ollama returned 0 tokens and chars÷4 estimation was used",
    )
    cached_input_tokens   = models.IntegerField(default=0,
        help_text="Input tokens served from Gemini context cache.")
    input_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    output_cost           = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    cache_read_cost       = models.DecimalField(max_digits=14, decimal_places=6, default=0,
        help_text="Cost for cached input tokens at cache-read rate.")
    cache_storage_cost    = models.DecimalField(max_digits=14, decimal_places=6, default=0,
        help_text="Pro-rated cache storage cost for this message (1 hour approximation).")
    total_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    response_time_seconds = models.FloatField(default=0)

    class Meta:
        verbose_name = "Chat Message"
        ordering     = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} | {self.provider}/{self.model_name} | ₹{self.total_cost}"


# ── Document (admin-managed) ───────────────────────────────────────────────────

class Document(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ready",   "Ready"),
        ("error",   "Error"),
    ]

    SOURCE_CHOICES = [
        ("file", "File Upload"),
        ("text", "Pasted Text"),
    ]

    original_filename = models.CharField(max_length=500)
    source_type       = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="file")
    markdown_path     = models.CharField(max_length=500, blank=True)
    json_path         = models.CharField(max_length=500, blank=True)
    rag_chunks_path   = models.CharField(max_length=500, blank=True)   # legacy — no longer written
    qdrant_collection = models.CharField(max_length=100, blank=True)
    gemini_cache_name = models.CharField(max_length=200, blank=True)
    total_pages       = models.IntegerField(default=0)
    char_count        = models.IntegerField(default=0)
    context_mode      = models.CharField(max_length=20, default="full")
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error_message     = models.TextField(blank=True)
    is_active         = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Document"
        ordering     = ["-created_at"]

    def __str__(self):
        flag = "● ACTIVE" if self.is_active else self.get_status_display()
        return f"{self.original_filename} [{flag}]"

    @classmethod
    def get_active(cls):
        """Return the currently active, ready document, or None."""
        return cls.objects.filter(is_active=True, status="ready").first()


# ── LLM Config ─────────────────────────────────────────────────────────────────

class LLMConfig(models.Model):
    PROVIDER_CHOICES = [
        ("ollama", "Ollama (Local)"),
        ("gemini", "Gemini (Google)"),
        ("sarvam", "Sarvam AI"),
    ]
    OCR_ENGINE_CHOICES = [
        ("auto",          "Auto (Docling for digital PDFs · Tesseract for scanned)"),
        ("docling",       "Docling (digital PDFs)"),
        ("tesseract",     "Tesseract (scanned · Hindi + Gujarati + English)"),
        ("gemini_vision", "Gemini Vision (cloud · best quality for complex scans)"),
        ("pdftext",       "PDF to Text (direct extraction · no OCR · fastest)"),
    ]
    RAG_EMBEDDING_CHOICES = [
        ("bm25",               "BM25 (keyword — English only, no cross-language)"),
        ("multilingual_local", "Multilingual Local (sentence-transformers, offline)"),
        ("gemini_embedding",   "Gemini Multilingual Embeddings (API)"),
    ]

    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        default="ollama",
    )
    ollama_model = models.CharField(
        max_length=100,
        default="llama3.2-vision",
        help_text="Ollama model name, e.g. llama3.2-vision",
    )
    gemini_model = models.CharField(
        max_length=100,
        default="gemini-2.0-flash",
        help_text="Gemini model ID, e.g. gemini-2.0-flash",
    )
    sarvam_model = models.CharField(
        max_length=100,
        default="sarvam-m",
        help_text="Sarvam AI model ID, e.g. sarvam-m",
    )
    ocr_engine = models.CharField(
        max_length=20,
        choices=OCR_ENGINE_CHOICES,
        default="docling",
        help_text="OCR engine used when processing uploaded documents",
    )
    rag_embedding = models.CharField(
        max_length=30,
        choices=RAG_EMBEDDING_CHOICES,
        default="multilingual_local",
        help_text=(
            "Embedding method for RAG mode (docs exceeding the context threshold). "
            "Multilingual options support Gujarati + English cross-language queries."
        ),
    )
    CONTEXT_MODE_CHOICES = [
        ("auto", "Auto (use document's computed mode)"),
        ("full", "Full context (send entire document)"),
        ("rag",  "RAG (retrieve relevant pages only)"),
    ]
    context_mode = models.CharField(
        max_length=10,
        choices=CONTEXT_MODE_CHOICES,
        default="auto",
        help_text=(
            "Override the context strategy for all providers. "
            "'Auto' uses the mode computed at document upload time. "
            "'Full' sends the entire document (Gemini will use context caching). "
            "'RAG' retrieves the most relevant pages only."
        ),
    )
    use_gemini_cache = models.BooleanField(
        default=True,
        help_text=(
            "Enable Gemini context caching in full-context mode. "
            "Disable to send the full document with every request (no cache storage cost)."
        ),
    )
    agent_mode = models.BooleanField(
        default=False,
        help_text=(
            "Enable agentic loop with tool-use and cross-session memory. "
            "Agent can search the document, fetch specific pages, and remember users across sessions."
        ),
    )

    class Meta:
        verbose_name = "LLM Configuration"
        verbose_name_plural = "LLM Configuration"

    def __str__(self):
        if self.provider == "gemini":
            llm = f"Gemini/{self.gemini_model}"
        elif self.provider == "sarvam":
            llm = f"Sarvam/{self.sarvam_model}"
        else:
            llm = f"Ollama/{self.ollama_model}"
        return f"{llm} | OCR: {self.get_ocr_engine_display()}"

    @classmethod
    def get_active(cls):
        """Return the singleton config, creating defaults if none exists."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ── Chat Session Configuration ─────────────────────────────────────────────────

class ChatSessionConfig(models.Model):
    """
    Singleton admin config controlling what user info is collected before chat.
    Defaults preserve existing behaviour (name + email + OTP).
    """
    collect_name = models.BooleanField(
        default=True,
        help_text="Ask users for their name before starting a chat.",
    )
    collect_email = models.BooleanField(
        default=True,
        help_text="Ask users for their email address before starting a chat.",
    )
    verify_email = models.BooleanField(
        default=True,
        help_text=(
            "Require email OTP verification. Only applies when 'Collect email' is enabled. "
            "Disable to collect email without sending a verification code."
        ),
    )

    class Meta:
        verbose_name        = "Chat Session Configuration"
        verbose_name_plural = "Chat Session Configuration"

    def __str__(self):
        parts = []
        if self.collect_name:  parts.append("name")
        if self.collect_email: parts.append("email")
        if self.collect_email and self.verify_email: parts.append("+OTP")
        return "Collect: " + (", ".join(parts) if parts else "none (anonymous)")

    @classmethod
    def get_active(cls):
        """Return the singleton config, creating defaults if none exists."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ── Email OTP Verification ─────────────────────────────────────────────────────

class EmailVerification(models.Model):
    """
    Temporary record holding a one-time 6-digit code sent to the user's email.
    Created by /request-otp/, consumed by /verify-otp/.
    A single resend is allowed (resend_count max 1).
    """
    email        = models.EmailField(db_index=True)
    name         = models.CharField(max_length=200)
    code         = models.CharField(max_length=6)
    created_at   = models.DateTimeField(auto_now_add=True)
    expires_at   = models.DateTimeField()
    is_verified  = models.BooleanField(default=False)
    resend_count = models.IntegerField(default=0)

    class Meta:
        verbose_name        = "Email Verification"
        verbose_name_plural = "Email Verifications"
        ordering            = ["-created_at"]

    def __str__(self):
        return f"{self.email} | {self.code} | verified={self.is_verified}"

    @classmethod
    def generate_code(cls):
        """Return a cryptographically random 6-digit string (100000–999999)."""
        return str(_secrets.randbelow(900_000) + 100_000)

    @property
    def is_expired(self):
        return _tz.now() >= self.expires_at

    def refresh_code(self):
        """Generate a new code and reset the 1-minute expiry. Call save() after."""
        self.code       = self.generate_code()
        self.expires_at = _tz.now() + _dt.timedelta(minutes=1)


# ── Agent Memory ───────────────────────────────────────────────────────────────

class AgentMemory(models.Model):
    """
    Persistent cross-session memory for a user, compressed and maintained by the agent.
    Stores a short plain-text block of facts about the user (name, language, topics, etc.).
    Updated in a background thread every N messages; capped at 500 chars.
    """
    user_email     = models.EmailField(unique=True, db_index=True)
    memory_text    = models.TextField(blank=True)
    total_sessions = models.IntegerField(default=0)
    last_updated   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Agent Memory"
        verbose_name_plural = "Agent Memories"
        ordering            = ["-last_updated"]

    def __str__(self):
        return f"{self.user_email} ({self.total_sessions} sessions)"

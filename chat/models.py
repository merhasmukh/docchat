from django.db import models


# ── Pricing ────────────────────────────────────────────────────────────────────

class ModelPricing(models.Model):
    PROVIDER_CHOICES = [("ollama", "Ollama"), ("gemini", "Gemini")]

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
    document_name       = models.CharField(max_length=500, blank=True)
    started_at          = models.DateTimeField(auto_now_add=True)
    last_activity       = models.DateTimeField(auto_now=True)
    message_count       = models.IntegerField(default=0)
    total_input_tokens  = models.BigIntegerField(default=0)
    total_output_tokens = models.BigIntegerField(default=0)
    total_tokens        = models.BigIntegerField(default=0)
    total_cost          = models.DecimalField(max_digits=14, decimal_places=6, default=0)

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
    input_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    output_cost           = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    total_cost            = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    response_time_seconds = models.FloatField(default=0)

    class Meta:
        verbose_name = "Chat Message"
        ordering     = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} | {self.provider}/{self.model_name} | ₹{self.total_cost}"


class LLMConfig(models.Model):
    PROVIDER_CHOICES = [
        ("ollama", "Ollama (Local)"),
        ("gemini", "Gemini (Google)"),
    ]
    OCR_ENGINE_CHOICES = [
        ("docling",       "Docling (default)"),
        ("tesseract",     "Tesseract (local, guj+eng)"),
        ("gemini_vision", "Gemini Vision (cloud)"),
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

    class Meta:
        verbose_name = "LLM Configuration"
        verbose_name_plural = "LLM Configuration"

    def __str__(self):
        llm = f"Ollama/{self.ollama_model}" if self.provider == "ollama" else f"Gemini/{self.gemini_model}"
        return f"{llm} | OCR: {self.get_ocr_engine_display()}"

    @classmethod
    def get_active(cls):
        """Return the singleton config, creating defaults if none exists."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

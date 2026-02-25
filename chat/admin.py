import json
import os
import time
import uuid

from django import forms
from django.contrib import admin
from django.conf import settings

from .models import Document, LLMConfig, ModelPricing, ChatSession, ChatMessage


# ── Document (admin-managed) ───────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


class DocumentUploadForm(forms.ModelForm):
    upload_file = forms.FileField(
        label="Document file",
        required=False,
        widget=forms.FileInput(attrs={"accept": ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp"}),
        help_text="PDF, PNG, JPG, TIFF, BMP, or WEBP — max 50 MB",
    )

    class Meta:
        model  = Document
        fields = ["is_active"]

    def clean_upload_file(self):
        f = self.cleaned_data.get("upload_file")
        if f:
            from pathlib import Path
            ext = Path(f.name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f"File type '{ext}' not supported. Allowed: PDF, PNG, JPG, TIFF, BMP, WEBP"
                )
        return f


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form         = DocumentUploadForm
    list_display = (
        "original_filename", "status_badge", "is_active",
        "total_pages", "char_count", "context_mode", "created_at",
    )
    list_display_links = ("original_filename",)
    list_filter  = ("status", "is_active", "context_mode")
    ordering     = ["-created_at"]
    actions      = ["make_active"]

    # ── Field layout ──────────────────────────────────────────────────────────

    def get_fields(self, request, obj=None):
        if obj:
            return (
                "original_filename", "status", "error_message",
                "total_pages", "char_count", "context_mode",
                "is_active",
                "markdown_path", "json_path", "rag_chunks_path", "gemini_cache_name",
                "created_at",
            )
        return ("upload_file",)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "original_filename", "status", "error_message",
                "total_pages", "char_count", "context_mode",
                "markdown_path", "json_path", "rag_chunks_path", "gemini_cache_name",
                "created_at",
            )
        return ()

    # ── Save: run OCR pipeline on new upload ──────────────────────────────────

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        uploaded_file = form.cleaned_data.get("upload_file")
        if not uploaded_file:
            self.message_user(request, "No file was provided.", level="error")
            return

        from pathlib import Path
        ext       = Path(uploaded_file.name).suffix.lower()
        safe_name = str(uuid.uuid4()) + ext
        upload_path = os.path.join(settings.UPLOAD_FOLDER, safe_name)

        # Save temp file to disk
        with open(upload_path, "wb") as fh:
            for chunk in uploaded_file.chunks():
                fh.write(chunk)

        obj.original_filename = uploaded_file.name
        obj.status            = "pending"
        super().save_model(request, obj, form, change)  # persist with pending status

        # Run the OCR + RAG pipeline (blocking — same logic as the old upload_view)
        t0 = time.perf_counter()
        try:
            from .pipeline import convert_to_markdown, build_rag_chunks
            from .models import LLMConfig
            from .providers.gemini import create_gemini_cache

            markdown_text, pages_data = convert_to_markdown(str(upload_path))

            base_id   = str(uuid.uuid4())
            md_path   = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".md")
            json_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".json")

            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(markdown_text)

            pages_data["source_file"] = uploaded_file.name
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(pages_data, fh, ensure_ascii=False, indent=2)

            threshold    = getattr(settings, "CONTEXT_CHAR_THRESHOLD", 100_000)
            doc_chars    = len(markdown_text)
            context_mode = "rag" if doc_chars > threshold else "full"

            cfg    = LLMConfig.get_active()
            chunks = build_rag_chunks(pages_data, cfg.rag_embedding)
            rag_chunks_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + "_chunks.json")
            with open(rag_chunks_path, "w", encoding="utf-8") as fh:
                json.dump(chunks, fh, ensure_ascii=False)

            gemini_cache_name = None
            if context_mode == "full" and cfg.provider == "gemini" and settings.GEMINI_API_KEY:
                gemini_cache_name = create_gemini_cache(markdown_text, cfg.gemini_model)

            obj.markdown_path     = md_path
            obj.json_path         = json_path
            obj.rag_chunks_path   = rag_chunks_path
            obj.gemini_cache_name = gemini_cache_name or ""
            obj.total_pages       = pages_data["total_pages"]
            obj.char_count        = doc_chars
            obj.context_mode      = context_mode
            obj.status            = "ready"

            elapsed = time.perf_counter() - t0
            self.message_user(
                request,
                f"'{uploaded_file.name}' processed successfully — "
                f"{pages_data['total_pages']} pages, {doc_chars:,} chars, "
                f"{context_mode} mode ({elapsed:.1f}s).",
            )

        except Exception as exc:
            obj.status        = "error"
            obj.error_message = str(exc)
            self.message_user(request, f"OCR failed: {exc}", level="error")

        finally:
            if os.path.exists(upload_path):
                os.remove(upload_path)

        obj.save()

    # ── Actions ───────────────────────────────────────────────────────────────

    @admin.action(description="Set as active document")
    def make_active(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one document to activate.", level="error")
            return
        doc = queryset.first()
        if doc.status != "ready":
            self.message_user(request, "Only 'ready' documents can be activated.", level="error")
            return
        Document.objects.update(is_active=False)
        doc.is_active = True
        doc.save()
        self.message_user(request, f"'{doc.original_filename}' is now the active document.")

    # ── Delete: clean up disk files and Gemini cache ──────────────────────────

    def _cleanup_document(self, doc):
        from .providers.gemini import delete_gemini_cache
        for path_field in ("markdown_path", "json_path", "rag_chunks_path"):
            path = getattr(doc, path_field, "")
            if path and os.path.exists(path):
                os.remove(path)
        if doc.gemini_cache_name:
            delete_gemini_cache(doc.gemini_cache_name)

    def delete_model(self, request, obj):
        self._cleanup_document(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for doc in queryset:
            self._cleanup_document(doc)
        super().delete_queryset(request, queryset)

    # ── Display helpers ───────────────────────────────────────────────────────

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {"ready": "green", "error": "red", "pending": "orange"}
        colour  = colours.get(obj.status, "gray")
        label   = obj.get_status_display()
        return f'<span style="color:{colour};font-weight:bold">{label}</span>'

    status_badge.allow_tags = True  # Django < 4.0 compat; harmless in 5.x


# ── LLM Config ─────────────────────────────────────────────────────────────────

@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    list_display = ("provider", "ollama_model", "gemini_model", "ocr_engine", "rag_embedding")

    def has_add_permission(self, request):
        return not LLMConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ── Model Pricing ──────────────────────────────────────────────────────────────

@admin.register(ModelPricing)
class ModelPricingAdmin(admin.ModelAdmin):
    list_display  = ("provider", "model_name", "input_price_per_million",
                     "output_price_per_million", "is_active", "updated_at")
    list_editable = ("is_active",)
    list_filter   = ("provider", "is_active")
    ordering      = ("provider", "model_name")


# ── Chat Session & Messages ────────────────────────────────────────────────────

class ChatMessageInline(admin.TabularInline):
    model  = ChatMessage
    extra  = 0
    can_delete      = False
    show_change_link = True
    readonly_fields = (
        "created_at", "provider", "model_name",
        "question", "answer",
        "input_tokens", "output_tokens", "total_tokens", "tokens_estimated",
        "input_cost", "output_cost", "total_cost",
        "response_time_seconds",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display   = (
        "user_name", "user_email", "document_name", "message_count",
        "total_input_tokens", "total_output_tokens",
        "total_tokens", "total_cost_inr", "started_at", "last_activity",
    )
    search_fields  = ("user_name", "user_email", "document_name")
    readonly_fields = (
        "session_key", "user_name", "user_email", "document_name",
        "started_at", "last_activity",
        "message_count",
        "total_input_tokens", "total_output_tokens", "total_tokens",
        "total_cost",
    )
    inlines  = [ChatMessageInline]
    ordering = ["-last_activity"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Total Cost (₹)")
    def total_cost_inr(self, obj):
        return f"₹{obj.total_cost:.4f}"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display  = ("created_at", "session_short", "provider", "model_name",
                     "input_tokens", "output_tokens", "total_tokens",
                     "question","answer",
                     "tokens_estimated", "total_cost_inr", "response_time_seconds")
    list_filter   = ("provider", "model_name", "tokens_estimated")
    search_fields = ("question", "session__session_key", "model_name")
    ordering      = ["-created_at"]
    readonly_fields = (
        "session", "created_at", "provider", "model_name",
        "question", "answer",
        "input_tokens", "output_tokens", "total_tokens", "tokens_estimated",
        "input_cost", "output_cost", "total_cost",
        "response_time_seconds",
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description="Session")
    def session_short(self, obj):
        return f"{obj.session.session_key[:12]}…"

    @admin.display(description="Cost (₹)")
    def total_cost_inr(self, obj):
        return f"₹{obj.total_cost:.4f}"

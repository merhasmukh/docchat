import json
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger("chat.admin")

from django import forms
from django.contrib import admin
from django.conf import settings

from .models import Document, LLMConfig, ChatSessionConfig, DocumentConfig, ModelPricing, ChatSession, ChatMessage, EmailVerification, AgentMemory


# ── Document (admin-managed) ───────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


class DocumentUploadForm(forms.ModelForm):
    # Use a different name ("source_choice") so Django admin's modelform_factory
    # does NOT confuse this with the model's source_type CharField and replace the
    # RadioSelect widget with an auto-generated Select dropdown.
    source_choice = forms.ChoiceField(
        label="Source",
        choices=[("file", "Upload File"), ("text", "Paste Text")],
        initial="file",
        widget=forms.RadioSelect,
        help_text="Choose how to provide the document content.",
    )
    upload_file = forms.FileField(
        label="Document file",
        required=False,
        widget=forms.FileInput(attrs={"accept": ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp"}),
        help_text="PDF, PNG, JPG, TIFF, BMP, or WEBP — max 50 MB",
    )
    doc_label = forms.CharField(
        label="Document name",
        required=False,
        max_length=500,
        help_text="A short label to identify this content (e.g. 'Company FAQ v2').",
    )
    pasted_text = forms.CharField(
        label="Text content",
        required=False,
        widget=forms.Textarea(attrs={"rows": 18, "style": "font-family:monospace;font-size:13px;"}),
        help_text="Paste or type the full text you want to use as the document context.",
    )
    text_context_mode = forms.ChoiceField(
        label="Context mode",
        required=False,
        initial="full",   # pre-select "Full Context" so the field is never blank
        choices=[
            ("full", "Full Context — send all text to the LLM in every request"),
            ("rag",  "Chunked / RAG — split into ~1000-character chunks and retrieve only relevant ones"),
        ],
        widget=forms.RadioSelect,
    )

    class Meta:
        model  = Document
        fields = ["is_active"]

    def clean(self):
        data = super().clean()
        src = data.get("source_choice")

        # Edit mode (existing document) — no source_choice submitted, skip upload validation
        if not src:
            return data

        if src == "file":
            f = data.get("upload_file")
            if not f:
                self.add_error("upload_file", "Please choose a file to upload.")
            else:
                ext = Path(f.name).suffix.lower()
                if ext not in ALLOWED_EXTENSIONS:
                    self.add_error(
                        "upload_file",
                        f"File type '{ext}' not supported. Allowed: PDF, PNG, JPG, TIFF, BMP, WEBP",
                    )
        else:  # text
            if not data.get("pasted_text", "").strip():
                self.add_error("pasted_text", "Please paste some text content.")
            if not data.get("doc_label", "").strip():
                self.add_error("doc_label", "Please provide a name for this document.")
            if not data.get("text_context_mode"):
                self.add_error("text_context_mode", "Please choose a context mode.")

        return data

    class Media:
        js = ("admin/js/paste_text_toggle.js",)


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
    actions      = ["make_active", "retry_rag_indexing"]

    # ── Field layout ──────────────────────────────────────────────────────────

    def get_form(self, request, obj=None, **kwargs):
        if obj:
            # Change view — only is_active is editable; use a plain ModelForm
            # to avoid validating the upload-specific fields on DocumentUploadForm.
            class _ChangeForm(forms.ModelForm):
                class Meta:
                    model  = Document
                    fields = ["is_active"]

            return _ChangeForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj:
            return (
                "source_type", "original_filename", "status", "error_message",
                "total_pages", "char_count", "context_mode",
                "is_active",
                "markdown_path", "json_path", "qdrant_collection", "gemini_cache_name",
                "created_at",
            )
        # Add form — show all source-selection fields
        return ("source_choice", "upload_file", "doc_label", "pasted_text", "text_context_mode")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "source_type", "original_filename", "status", "error_message",
                "total_pages", "char_count", "context_mode",
                "markdown_path", "json_path", "qdrant_collection", "gemini_cache_name",
                "created_at",
            )
        return ()

    # ── Save: OCR pipeline for file uploads / direct write for pasted text ───

    def save_model(self, request, obj, form, change):
        if change:
            if obj.is_active:
                Document.objects.exclude(pk=obj.pk).filter(is_active=True).update(is_active=False)
            super().save_model(request, obj, form, change)
            return

        src = form.cleaned_data.get("source_choice", "file")

        if src == "text":
            self._save_pasted_text(request, obj, form, change)
        else:
            self._save_uploaded_file(request, obj, form, change)

    def _save_pasted_text(self, request, obj, form, change):
        """Save pasted text directly, bypassing OCR."""
        from .pipeline import build_rag_chunks, split_text_into_pages
        from .models import LLMConfig
        from .providers.gemini import create_gemini_cache

        markdown_text = form.cleaned_data["pasted_text"].strip()
        mode_choice   = form.cleaned_data["text_context_mode"]   # "full" or "rag"
        base_id       = str(uuid.uuid4())
        md_path       = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".md")
        json_path     = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".json")
        obj.original_filename = form.cleaned_data["doc_label"].strip()
        obj.source_type       = "text"
        obj.status            = "pending"
        super().save_model(request, obj, form, change)   # persist → get pk

        try:
            os.makedirs(settings.MARKDOWN_FOLDER, exist_ok=True)
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(markdown_text)

            cfg = LLMConfig.get_active()
            if mode_choice == "rag":
                pages_data = split_text_into_pages(
                    markdown_text,
                    chunk_size=cfg.rag_chunk_size,
                    chunk_overlap=cfg.rag_chunk_overlap,
                )
            else:
                pages_data = {
                    "total_pages": 1,
                    "pages": [{"page": 1, "markdown": markdown_text}],
                }
            pages_data["source_label"] = obj.original_filename

            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(pages_data, fh, ensure_ascii=False, indent=2)

            chunks = build_rag_chunks(pages_data, cfg.rag_embedding)
            from .pipeline import store_rag_chunks_qdrant
            collection_name = f"doc_{obj.pk}"
            store_rag_chunks_qdrant(chunks, collection_name, cfg.rag_embedding)

            gemini_cache_name = None
            if mode_choice == "full" and cfg.provider == "gemini" and settings.GEMINI_API_KEY:
                gemini_cache_name = create_gemini_cache(markdown_text, cfg.gemini_model)

            obj.markdown_path     = md_path
            obj.json_path         = json_path
            obj.qdrant_collection = collection_name
            obj.gemini_cache_name = gemini_cache_name or ""
            obj.char_count        = len(markdown_text)
            obj.total_pages       = pages_data["total_pages"]
            obj.context_mode      = mode_choice
            obj.status            = "ready"

            self.message_user(
                request,
                f"'{obj.original_filename}' saved — "
                f"{pages_data['total_pages']} chunk(s), {len(markdown_text):,} chars, "
                f"{mode_choice} mode.",
            )

        except Exception as exc:
            obj.status        = "error"
            obj.error_message = str(exc)
            self.message_user(request, f"Failed to process pasted text: {exc}", level="error")

        obj.save()

    def _save_uploaded_file(self, request, obj, form, change):
        """Run the OCR + RAG pipeline on an uploaded file."""
        uploaded_file = form.cleaned_data.get("upload_file")
        if not uploaded_file:
            self.message_user(request, "No file was provided.", level="error")
            return

        ext         = Path(uploaded_file.name).suffix.lower()
        safe_name   = str(uuid.uuid4()) + ext
        os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(settings.MARKDOWN_FOLDER, exist_ok=True)
        upload_path = os.path.join(settings.UPLOAD_FOLDER, safe_name)

        with open(upload_path, "wb") as fh:
            for chunk in uploaded_file.chunks():
                fh.write(chunk)

        obj.original_filename = uploaded_file.name
        obj.source_type       = "file"
        obj.status            = "pending"
        super().save_model(request, obj, form, change)   # persist → get pk

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

            threshold    = getattr(settings, "CONTEXT_CHAR_THRESHOLD", 12_000)
            doc_chars    = len(markdown_text)
            context_mode = "rag" if doc_chars > threshold else "full"

            # ── CHECKPOINT: persist OCR output before any embedding API calls ──
            # If embedding or Qdrant fails below, markdown_path/json_path are
            # already in the DB so "Retry RAG indexing" can resume without re-OCR.
            obj.markdown_path = md_path
            obj.json_path     = json_path
            obj.char_count    = doc_chars
            obj.total_pages   = pages_data["total_pages"]
            obj.context_mode  = context_mode
            obj.status        = "ocr_done"
            obj.save(update_fields=[
                "markdown_path", "json_path", "char_count",
                "total_pages", "context_mode", "status",
            ])
            # ────────────────────────────────────────────────────────────────────

            cfg    = LLMConfig.get_active()
            chunks = build_rag_chunks(pages_data, cfg.rag_embedding)
            from .pipeline import store_rag_chunks_qdrant
            collection_name = f"doc_{obj.pk}"
            store_rag_chunks_qdrant(chunks, collection_name, cfg.rag_embedding)

            gemini_cache_name = None
            if context_mode == "full" and cfg.provider == "gemini" and settings.GEMINI_API_KEY:
                gemini_cache_name = create_gemini_cache(markdown_text, cfg.gemini_model)

            obj.qdrant_collection = collection_name
            obj.gemini_cache_name = gemini_cache_name or ""
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

    @admin.action(description="Retry RAG indexing (skip OCR, use saved OCR output)")
    def retry_rag_indexing(self, request, queryset):
        """
        Re-run only the embedding + Qdrant step for documents that already have
        a json_path on disk (OCR succeeded but indexing failed).
        Skips full OCR — no Gemini Vision API calls made.
        """
        import json as _json
        from .pipeline import build_rag_chunks, store_rag_chunks_qdrant, get_qdrant_client
        from .models import LLMConfig
        from .providers.gemini import create_gemini_cache, delete_gemini_cache

        cfg = LLMConfig.get_active()
        ok = err = 0

        for doc in queryset:
            if not doc.json_path or not os.path.exists(doc.json_path):
                self.message_user(
                    request,
                    f"'{doc.original_filename}': no OCR output found on disk — re-upload required.",
                    level="warning",
                )
                continue

            try:
                with open(doc.json_path, encoding="utf-8") as fh:
                    pages_data = _json.load(fh)

                collection_name = f"doc_{doc.pk}"

                # Clean up old Qdrant collection and Gemini cache if they exist
                if doc.qdrant_collection:
                    try:
                        get_qdrant_client().delete_collection(doc.qdrant_collection)
                    except Exception:
                        pass
                if doc.gemini_cache_name:
                    try:
                        delete_gemini_cache(doc.gemini_cache_name)
                    except Exception:
                        pass

                chunks = build_rag_chunks(pages_data, cfg.rag_embedding)
                store_rag_chunks_qdrant(chunks, collection_name, cfg.rag_embedding)

                gemini_cache_name = None
                if doc.context_mode == "full" and cfg.provider == "gemini" and settings.GEMINI_API_KEY:
                    with open(doc.markdown_path, encoding="utf-8") as fh:
                        markdown_text = fh.read()
                    gemini_cache_name = create_gemini_cache(markdown_text, cfg.gemini_model)

                doc.qdrant_collection = collection_name
                doc.gemini_cache_name = gemini_cache_name or ""
                doc.status            = "ready"
                doc.error_message     = ""
                doc.save(update_fields=["qdrant_collection", "gemini_cache_name", "status", "error_message"])

                self.message_user(
                    request,
                    f"'{doc.original_filename}' re-indexed successfully ({len(chunks)} chunks).",
                )
                ok += 1

            except Exception as exc:
                doc.status        = "error"
                doc.error_message = str(exc)
                doc.save(update_fields=["status", "error_message"])
                self.message_user(request, f"'{doc.original_filename}' indexing failed: {exc}", level="error")
                err += 1

        if ok:
            self.message_user(request, f"{ok} document(s) re-indexed successfully.")

    # ── Delete: clean up disk files and Gemini cache ──────────────────────────

    def _cleanup_document(self, doc):
        from .providers.gemini import delete_gemini_cache
        from .pipeline import get_qdrant_client, delete_liked_collection
        for path_field in ("markdown_path", "json_path", "rag_chunks_path"):
            path = getattr(doc, path_field, "")
            if path and os.path.exists(path):
                os.remove(path)
        if doc.qdrant_collection:
            try:
                get_qdrant_client().delete_collection(doc.qdrant_collection)
            except Exception as exc:
                logger.warning("Failed to delete Qdrant collection %s: %s", doc.qdrant_collection, exc)
            try:
                delete_liked_collection(doc.qdrant_collection)
            except Exception as exc:
                logger.warning("Failed to delete liked-QA collection for %s: %s", doc.qdrant_collection, exc)
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
        colours = {"ready": "green", "error": "red", "pending": "orange", "ocr_done": "blue"}
        colour  = colours.get(obj.status, "gray")
        label   = obj.get_status_display()
        return f'<span style="color:{colour};font-weight:bold">{label}</span>'

    status_badge.allow_tags = True  # Django < 4.0 compat; harmless in 5.x


# ── LLM Config ─────────────────────────────────────────────────────────────────

@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    list_display = ("provider", "ollama_model", "gemini_model", "ocr_engine", "rag_embedding",
                    "context_mode", "rag_chunk_size", "rag_chunk_overlap", "rag_top_k",
                    "use_session_cache", "use_gemini_cache", "embed_script_link")

    def has_add_permission(self, request):
        return not LLMConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    # ── Widget embed script page ───────────────────────────────────────────
    def get_urls(self):
        from django.urls import path
        return [
            path("widget-script/",
                 self.admin_site.admin_view(self.widget_script_view),
                 name="chat_llmconfig_widget_script"),
        ] + super().get_urls()

    def widget_script_view(self, request):
        from django.template.response import TemplateResponse
        server_url = request.build_absolute_uri("/").rstrip("/")
        context = {
            "title": "Widget Embed Script",
            "server_url": server_url,
            **self.admin_site.each_context(request),
        }
        return TemplateResponse(request, "admin/widget_script.html", context)

    @admin.display(description="Embed Script")
    def embed_script_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse("admin:chat_llmconfig_widget_script")
        return format_html('<a href="{}">📋 Get embed script</a>', url)


# ── Chat Session Config ────────────────────────────────────────────────────────

@admin.register(ChatSessionConfig)
class ChatSessionConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        ("User Info Collection", {
            "fields": ("collect_name", "collect_email", "verify_email", "collect_mobile"),
            "description": (
                "Control what information users must provide before starting a chat. "
                "Disabling collection reduces friction and improves user retention."
            ),
        }),
        ("Greeting Message", {
            "fields": ("welcome_greeting",),
            "description": (
                "First message shown to the user when chat starts. "
                "Use {name} to insert the user's name."
            ),
        }),
    ]

    def has_add_permission(self, request):
        return not ChatSessionConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ── Document Configuration ─────────────────────────────────────────────────────

@admin.register(DocumentConfig)
class DocumentConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        ("Fallback Contact Details", {
            "fields": ("fallback_contact",),
            "description": (
                "When the document doesn't contain the answer to a user's question, "
                "the bot will suggest these contact details. Leave blank to use the "
                "default 'I don't know' reply."
            ),
        }),
    ]

    def has_add_permission(self, request):
        return not DocumentConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ── Model Pricing ──────────────────────────────────────────────────────────────

@admin.register(ModelPricing)
class ModelPricingAdmin(admin.ModelAdmin):
    list_display  = ("provider", "model_name", "input_price_per_million",
                     "output_price_per_million",
                     "cache_read_price_per_million",
                     "cache_storage_price_per_million_per_hour",
                     "is_active", "updated_at")
    list_editable = ("is_active",)
    list_filter   = ("provider", "is_active")
    ordering      = ("provider", "model_name")


# ── Email OTP Verification ─────────────────────────────────────────────────────

@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display    = ("email", "name", "course", "code", "created_at", "expires_at", "is_verified", "resend_count")
    list_filter     = ("is_verified",)
    search_fields   = ("email", "name", "course")
    readonly_fields = ("email", "name", "course", "code", "created_at", "expires_at", "is_verified", "resend_count")
    ordering        = ["-created_at"]

    def has_add_permission(self, request):
        return False


# ── Chat Session & Messages ────────────────────────────────────────────────────

class ChatMessageInline(admin.TabularInline):
    model  = ChatMessage
    extra  = 0
    can_delete      = False
    show_change_link = True
    readonly_fields = (
        "created_at", "provider", "model_name",
        "question", "answer", "answer_source", "liked",
        "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "tokens_estimated",
        "input_cost", "output_cost", "cache_read_cost", "cache_storage_cost", "total_cost",
        "response_time_seconds",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    change_list_template = "admin/chat_session_change_list.html"

    list_display   = (
        "user_name", "user_email", "user_mobile", "user_course", "document_name", "message_count",
        "total_input_tokens", "total_output_tokens", "total_cached_input_tokens",
        "total_tokens", "avg_tokens_per_message",
        "total_cost_inr", "total_cache_read_cost_inr", "total_cache_storage_cost_inr",
        "avg_cost_per_message_inr", "started_at", "last_activity",
    )
    search_fields  = ("user_name", "user_email", "user_mobile", "user_course", "document_name")
    list_filter    = ("user_course",)
    readonly_fields = (
        "session_key", "user_name", "user_email", "user_mobile", "user_course", "document_name",
        "started_at", "last_activity",
        "message_count",
        "total_input_tokens", "total_output_tokens", "total_tokens",
        "total_cached_input_tokens",
        "avg_tokens_per_message",
        "total_cost", "total_cache_read_cost", "total_cache_storage_cost",
        "avg_cost_per_message",
    )
    inlines  = [ChatMessageInline]
    ordering = ["-last_activity"]
    actions  = ["export_as_excel"]

    def has_add_permission(self, request):
        return False

    # ── Excel export action ────────────────────────────────────────────────

    @admin.action(description="Export selected sessions to Excel")
    def export_as_excel(self, request, queryset):
        import io
        import openpyxl
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Chat Sessions"
        ws.append(["Name", "Email", "Mobile", "Course", "Messages", "Last Activity", "Q&A…"])

        for session in queryset.order_by("-last_activity"):
            msgs = (
                session.messages
                .filter(answer_source="llm")
                .order_by("created_at")
            )
            row = [
                session.user_name,
                session.user_email,
                session.user_mobile,
                session.user_course,
                session.message_count,
                (
                    session.last_activity.strftime("%B %d, %Y, %I:%M %p")
                    if session.last_activity else ""
                ),
            ]
            for msg in msgs:
                row += [f"Q: {msg.question}", f"A: {msg.answer}"]
            ws.append(row)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(
            buf.read(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            'attachment; filename="chat_sessions.xlsx"'
        )
        return response

    # ── Analytics custom URL + view ────────────────────────────────────────

    def get_urls(self):
        from django.urls import path
        return [
            path(
                "analytics/",
                self.admin_site.admin_view(self.analytics_view),
                name="chat_chatsession_analytics",
            ),
            path(
                "analytics/export/",
                self.admin_site.admin_view(self.analytics_export_view),
                name="chat_chatsession_analytics_export",
            ),
        ] + super().get_urls()

    def _get_analytics_sessions(self, request):
        from datetime import timedelta

        from django.utils import timezone

        days_param = request.GET.get("days", "all")
        if days_param in ("7", "30"):
            since = timezone.now() - timedelta(days=int(days_param))
            sessions = ChatSession.objects.filter(last_activity__gte=since)
        else:
            days_param = "all"
            sessions = ChatSession.objects.all()

        course_param = (request.GET.get("course") or "").strip()
        if course_param == "__not_specified__":
            sessions = sessions.filter(user_course="")
        elif course_param:
            sessions = sessions.filter(user_course__iexact=course_param)

        return sessions, days_param, course_param

    def analytics_export_view(self, request):
        import io

        import openpyxl
        from django.http import HttpResponse

        sessions, days_param, course_param = self._get_analytics_sessions(request)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Analytics Sessions"
        ws.append(["Name", "Email", "Mobile", "Course", "Messages", "Last Activity", "Q&A..."])

        for session in sessions.order_by("-last_activity"):
            msgs = (
                session.messages
                .filter(answer_source="llm")
                .order_by("created_at")
            )
            row = [
                session.user_name,
                session.user_email,
                session.user_mobile,
                session.user_course or "Not Specified",
                session.message_count,
                (
                    session.last_activity.strftime("%B %d, %Y, %I:%M %p")
                    if session.last_activity else ""
                ),
            ]
            for msg in msgs:
                row += [f"Q: {msg.question}", f"A: {msg.answer}"]
            ws.append(row)

        filename_parts = ["chat_analytics", f"{days_param}_days"]
        if course_param == "__not_specified__":
            filename_parts.append("not_specified")
        elif course_param:
            filename_parts.append(course_param.lower().replace(" ", "_"))

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(
            buf.read(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{"_".join(filename_parts)}.xlsx"'
        )
        return response

    def analytics_view(self, request):
        from collections import Counter

        from django.db.models import Avg, Sum
        from django.template.response import TemplateResponse
        from django.urls import reverse

        # ── Date and course filters ────────────────────────────────────────
        sessions, days_param, course_param = self._get_analytics_sessions(request)

        base_courses = (
            ChatSession.objects
            .values_list("user_course", flat=True)
            .distinct()
            .order_by("user_course")
        )
        course_options = [
            {"value": c.strip(), "label": c.strip()}
            for c in base_courses
            if c and c.strip()
        ]
        if ChatSession.objects.filter(user_course="").exists():
            course_options.insert(0, {"value": "__not_specified__", "label": "Not Specified"})

        def analytics_query(days=None, course=None):
            params = {}
            params["days"] = days if days is not None else days_param
            selected_course = course if course is not None else course_param
            if selected_course:
                params["course"] = selected_course
            return params

        from urllib.parse import urlencode

        analytics_url = reverse("admin:chat_chatsession_analytics")
        export_url = reverse("admin:chat_chatsession_analytics_export")
        day_links = {
            d: f"{analytics_url}?{urlencode(analytics_query(days=d))}"
            for d in ("7", "30", "all")
        }
        download_url = f"{export_url}?{urlencode(analytics_query())}"

        # ── Key metrics ────────────────────────────────────────────────────
        total_users = sessions.count()
        agg = sessions.aggregate(
            total_msgs=Sum("message_count"),
            avg_msgs=Avg("message_count"),
        )
        total_msgs = agg["total_msgs"] or 0
        avg_msgs   = round(agg["avg_msgs"] or 0, 1)

        # ── Daily activity (Python-side grouping, avoids TruncDate/USE_TZ issues) ──
        import zoneinfo
        from collections import defaultdict
        from django.conf import settings as _settings

        local_tz  = zoneinfo.ZoneInfo(getattr(_settings, "TIME_ZONE", "UTC"))
        raw_rows  = (
            sessions
            .filter(last_activity__isnull=False)
            .values("last_activity", "message_count")
        )
        daily_dict = defaultdict(lambda: {"users": 0, "msgs": 0})
        for row in raw_rows:
            date_str = row["last_activity"].astimezone(local_tz).strftime("%Y-%m-%d")
            daily_dict[date_str]["users"] += 1
            daily_dict[date_str]["msgs"]  += row["message_count"] or 0

        daily_labels = sorted(daily_dict.keys())
        daily_users  = [daily_dict[d]["users"] for d in daily_labels]
        daily_msgs   = [daily_dict[d]["msgs"]  for d in daily_labels]

        if daily_msgs:
            peak_idx = daily_msgs.index(max(daily_msgs))
            peak_day = f"{daily_labels[peak_idx]} ({daily_msgs[peak_idx]} msgs)"
        else:
            peak_day = "—"

        # ── Engagement buckets ─────────────────────────────────────────────
        eng_labels = ["0 messages", "1–5", "6–10", "11+"]
        eng_vals = [
            sessions.filter(message_count=0).count(),
            sessions.filter(message_count__gte=1, message_count__lte=5).count(),
            sessions.filter(message_count__gte=6, message_count__lte=10).count(),
            sessions.filter(message_count__gte=11).count(),
        ]

        # ── Topic distribution ─────────────────────────────────────────────
        TOPICS = {
            "Courses & Programs":  [
                "course", "ba", "bca", "bsc", "bcom", "mca", "mba",
                "ma ", "msc", "phd", "degree", "digree",
            ],
            "Admission Process":   ["admission", "pravesh", "form", "apply", "addmission", "link"],
            "GEETA Exam":          ["geeta", "gdpi", "exam", "hall ticket", "neet"],
            "Fees & Costs":        ["fees", "fee", "fess", "cost", "hostel"],
            "Jobs & Placement":    ["job", "salary", "placement", "corporate", "government"],
            "Medium / Language":   ["english", "gujarati", "medium", "hindi"],
            "Campus & Location":   ["city", "campus", "address", "ahmedabad"],
            "Greetings / Misc":    ["hello", "hi", "hy", "thanks", "thank", "ok"],
        }

        questions_with_users = (
            ChatMessage.objects
            .filter(session__in=sessions, answer_source="llm")
            .exclude(question="")
            .values("question", "session__user_name", "session__user_mobile")
        )
        topic_counts = Counter()
        topic_users  = defaultdict(set)   # topic → {(name, mobile)}

        for row in questions_with_users:
            ql     = row["question"].lower()
            name   = row["session__user_name"]  or "—"
            mobile = row["session__user_mobile"] or "—"
            matched = False
            for topic, kws in TOPICS.items():
                if any(kw in ql for kw in kws):
                    topic_counts[topic] += 1
                    topic_users[topic].add((name, mobile))
                    matched = True
                    break
            if not matched:
                topic_counts["Other"] += 1
                topic_users["Other"].add((name, mobile))

        sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
        topic_labels  = [t[0] for t in sorted_topics]
        topic_vals    = [t[1] for t in sorted_topics]

        # ── Word cloud ─────────────────────────────────────────────────────
        import re as _re

        _WC_STOPWORDS = {
            # English
            "the","a","an","is","in","it","of","to","for","and","or","are",
            "was","were","be","been","have","has","had","do","does","did",
            "will","would","could","should","may","might","can","this","that",
            "these","those","i","you","he","she","we","they","my","your","his",
            "her","our","their","what","which","who","how","when","where","why",
            "not","no","yes","at","by","from","with","about","as","on","into",
            "through","after","also","but","if","than","so","up","out","please",
            "get","any","its","am","just","me","im","s","tell","know",
            # Romanized Gujarati / Hindi stop words
            "ma","che","na","nu","ni","to","hu","ky","aa","su","aave","rite",
            "ke","hai","me","ki","ka","kya","bhi","aur","ho","ok","ohke","hy",
            "aaor","thi","thai","maru","mare","gvp","ane","pan","em","ne",
            "thi","nathi","chhe","hoy","tame","kem","shu","pan","puchhu",
        }

        word_freq: Counter = Counter()
        for row in questions_with_users:
            # Roman-script words only (wordcloud2.js renders Unicode but
            # Gujarati/Devanagari glyphs need custom fonts; roman is reliable)
            words = _re.sub(r"[^a-z\s]", " ", row["question"].lower()).split()
            for w in words:
                if len(w) >= 3 and w not in _WC_STOPWORDS:
                    word_freq[w] += 1

        # Top 80 words as [[word, freq], ...] for wordcloud2.js
        wc_words = [[w, c] for w, c in word_freq.most_common(80)]

        # Build ordered breakdown list for the template table
        topic_breakdown = [
            {
                "topic": t,
                "count": topic_counts[t],
                "users": sorted(
                    [{"name": u[0], "mobile": u[1]} for u in topic_users[t]],
                    key=lambda u: u["name"].lower(),
                ),
            }
            for t in topic_labels
        ]

        # ── Course distribution ────────────────────────────────────────────
        course_counts = Counter()
        for c in sessions.values_list("user_course", flat=True):
            cleaned_course = c.strip() if c else "Not Specified"
            course_counts[cleaned_course] += 1

        sorted_courses = course_counts.most_common(12)
        course_labels = [c[0] for c in sorted_courses]
        course_vals   = [c[1] for c in sorted_courses]

        context = {
            "title":          "Chat Analytics Dashboard",
            "days_param":     days_param,
            "course_param":   course_param,
            "course_options": course_options,
            "day_link_7":     day_links["7"],
            "day_link_30":    day_links["30"],
            "day_link_all":   day_links["all"],
            "download_url":   download_url,
            "total_users":    total_users,
            "total_msgs":     total_msgs,
            "avg_msgs":       avg_msgs,
            "peak_day":       peak_day,
            "topic_breakdown": topic_breakdown,
            "wc_words":        wc_words,
            "daily_labels": daily_labels,
            "daily_users":  daily_users,
            "daily_msgs":   daily_msgs,
            "eng_labels":   eng_labels,
            "eng_vals":     eng_vals,
            "topic_labels": topic_labels,
            "topic_vals":   topic_vals,
            "course_labels": course_labels,
            "course_vals":   course_vals,
            **self.admin_site.each_context(request),
        }
        return TemplateResponse(request, "admin/analytics_dashboard.html", context)

    # ── Cost display helpers ───────────────────────────────────────────────

    @admin.display(description="Total Cost (₹)")
    def total_cost_inr(self, obj):
        return f"₹{obj.total_cost:.4f}"

    @admin.display(description="Cache Read Cost (₹)")
    def total_cache_read_cost_inr(self, obj):
        return f"₹{obj.total_cache_read_cost:.6f}"

    @admin.display(description="Cache Storage Cost (₹)")
    def total_cache_storage_cost_inr(self, obj):
        return f"₹{obj.total_cache_storage_cost:.6f}"

    @admin.display(description="Avg Cost/Msg (₹)")
    def avg_cost_per_message_inr(self, obj):
        return f"₹{obj.avg_cost_per_message:.6f}"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display  = ("created_at", "session_short", "provider", "model_name",
                     "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens",
                     "question", "answer", "answer_source", "liked",
                     "tokens_estimated", "total_cost_inr", "response_time_seconds")
    list_filter   = ("provider", "model_name", "tokens_estimated", "answer_source", "liked")
    search_fields = ("question", "session__session_key", "model_name")
    ordering      = ["-created_at"]
    readonly_fields = (
        "session", "created_at", "provider", "model_name",
        "question", "answer", "answer_source", "liked", "liked_qa_qdrant_id",
        "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "tokens_estimated",
        "input_cost", "output_cost", "cache_read_cost", "cache_storage_cost", "total_cost",
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


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display  = ("user_email", "total_sessions", "memory_preview", "last_updated")
    search_fields = ("user_email",)
    ordering      = ["-last_updated"]
    readonly_fields = ("user_email", "total_sessions", "last_updated")

    @admin.display(description="Memory")
    def memory_preview(self, obj):
        return obj.memory_text[:120] + "…" if len(obj.memory_text) > 120 else obj.memory_text

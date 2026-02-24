from django.contrib import admin

from .models import LLMConfig, ModelPricing, ChatSession, ChatMessage


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
    list_display   = ("session_key_short", "document_name", "message_count",
                      "total_input_tokens", "total_output_tokens",
                      "total_tokens", "total_cost_inr", "started_at", "last_activity")
    readonly_fields = (
        "session_key", "document_name",
        "started_at", "last_activity",
        "message_count",
        "total_input_tokens", "total_output_tokens", "total_tokens",
        "total_cost",
    )
    inlines  = [ChatMessageInline]
    ordering = ["-last_activity"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Session")
    def session_key_short(self, obj):
        return f"{obj.session_key[:16]}…"

    @admin.display(description="Total Cost (₹)")
    def total_cost_inr(self, obj):
        return f"₹{obj.total_cost:.4f}"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display  = ("created_at", "session_short", "provider", "model_name",
                     "input_tokens", "output_tokens", "total_tokens",
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

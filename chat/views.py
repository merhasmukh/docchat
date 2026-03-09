import json
import logging
import os
import time
import uuid

from django.conf import settings
from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.clickjacking import xframe_options_exempt

from rest_framework.decorators import api_view
from rest_framework.response import Response

from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiParameter, OpenApiResponse, OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
import rest_framework.serializers as s

from .pipeline import ask_streaming

logger = logging.getLogger("chat.views")


# ── Token helper ───────────────────────────────────────────────────────────────
def _get_chat_session(request):
    """
    Look up ChatSession by the token sent in the X-Chat-Token header.
    Returns the ChatSession instance or None.
    No Django session reads — the token is stored in the browser's localStorage.
    """
    from .models import ChatSession
    token = (request.META.get("HTTP_X_CHAT_TOKEN") or "").strip()
    if not token:
        return None
    try:
        return ChatSession.objects.get(session_key=token)
    except ChatSession.DoesNotExist:
        return None


# ── Index ──────────────────────────────────────────────────────────────────────
@extend_schema(exclude=True)
@ensure_csrf_cookie
def index(request):
    """Serve the single-page chat UI and set the CSRF cookie."""
    return render(request, "index.html")


# ── Status ─────────────────────────────────────────────────────────────────────
_CHAT_TOKEN_HEADER = OpenApiParameter(
    name="X-Chat-Token",
    location=OpenApiParameter.HEADER,
    description="Session token obtained from `POST /verify-otp/`.",
    required=False,
    type=OpenApiTypes.UUID,
)

@extend_schema(
    tags=["session"],
    summary="Document and session status",
    description="Returns the active document info and whether the X-Chat-Token is valid.",
    parameters=[_CHAT_TOKEN_HEADER],
    responses={200: inline_serializer("StatusResponse", fields={
        "document_loaded": s.BooleanField(),
        "filename":        s.CharField(allow_null=True),
        "total_pages":     s.IntegerField(allow_null=True),
        "session_active":  s.BooleanField(),
    })},
)
@api_view(["GET"])
def status_view(request):
    """Return active document info and whether the caller has a valid session token."""
    from .models import Document

    doc = Document.get_active()
    if not doc:
        return Response({
            "document_loaded": False,
            "filename": None,
            "total_pages": None,
            "session_active": False,
        })

    session_obj    = _get_chat_session(request)
    session_active = session_obj is not None

    return Response({
        "document_loaded": True,
        "filename":        doc.original_filename,
        "total_pages":     doc.total_pages,
        "session_active":  session_active,
    })


# ── History ────────────────────────────────────────────────────────────────────
@extend_schema(
    tags=["session"],
    summary="Conversation history",
    description="Returns all messages for the session identified by `X-Chat-Token`. Returns an empty list if the token is missing or invalid.",
    parameters=[_CHAT_TOKEN_HEADER],
    responses={200: inline_serializer("HistoryResponse", fields={
        "messages": s.ListField(child=inline_serializer("Message", fields={
            "role":    s.ChoiceField(choices=["user", "assistant"]),
            "content": s.CharField(),
        })),
    })},
)
@api_view(["GET"])
def history_view(request):
    """Return the full conversation history for the session identified by X-Chat-Token."""
    from .models import ChatMessage

    session_obj = _get_chat_session(request)
    if not session_obj:
        return Response({"messages": []})

    msgs = ChatMessage.objects.filter(session=session_obj).order_by("created_at")
    messages = []
    for msg in msgs:
        messages.append({"role": "user",      "content": msg.question})
        messages.append({"role": "assistant", "content": msg.answer})

    return Response({"messages": messages})


# ── Email OTP helpers ──────────────────────────────────────────────────────────

def _send_verification_email(email, name, code):
    """Send the 6-digit OTP to the user via Gmail SMTP."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import get_template

    html_body = get_template("emails/verification_code.html").render({"name": name, "code": code})
    text_body = (
        f"Hi {name},\n\n"
        f"Your DocChat verification code is: {code}\n\n"
        f"This code expires in 1 minute.\n\n"
        f"If you did not request this, please ignore this email."
    )
    msg = EmailMultiAlternatives(
        subject="Your DocChat verification code",
        body=text_body,
        to=[email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


# ── Request OTP ────────────────────────────────────────────────────────────────
@extend_schema(
    tags=["auth"],
    summary="Request OTP (step 1)",
    description=(
        "Send a 6-digit verification code to the provided email address. "
        "Returns a `verification_id` to be used in `POST /verify-otp/`."
    ),
    request=inline_serializer("RequestOtpRequest", fields={
        "name":  s.CharField(),
        "email": s.EmailField(),
    }),
    responses={
        200: inline_serializer("RequestOtpResponse", fields={
            "status":          s.ChoiceField(choices=["ok", "error"]),
            "verification_id": s.IntegerField(),
            "email_hint":      s.CharField(help_text="Masked email, e.g. j***@gmail.com"),
        }),
        400: OpenApiResponse(description="Missing or invalid name / email."),
        500: OpenApiResponse(description="Failed to send the verification email."),
    },
)
@api_view(["POST"])
def request_otp_view(request):
    """
    Step 1: accept {name, email}, send a 6-digit code, return {status, verification_id, email_hint}.
    Cleans up stale records for the same email before creating a new one.
    """
    import datetime
    from django.utils import timezone
    from .models import EmailVerification

    name   = (request.data.get("name")   or "").strip()
    email  = (request.data.get("email")  or "").strip().lower()
    mobile = (request.data.get("mobile") or "").strip()

    if not name:
        return Response({"status": "error", "message": "Name is required."}, status=400)
    if not email:
        return Response({"status": "error", "message": "Email is required."}, status=400)

    # Clean up expired and already-verified records for this email
    EmailVerification.objects.filter(email=email, expires_at__lt=timezone.now()).delete()
    EmailVerification.objects.filter(email=email, is_verified=True).delete()

    # Reuse an existing valid pending record (e.g. user resubmits the form)
    existing = EmailVerification.objects.filter(
        email=email, is_verified=False, expires_at__gt=timezone.now()
    ).first()

    if existing:
        existing.name   = name
        existing.mobile = mobile
        existing.save(update_fields=["name", "mobile"])
        verification = existing
    else:
        verification = EmailVerification.objects.create(
            email      = email,
            name       = name,
            mobile     = mobile,
            code       = EmailVerification.generate_code(),
            expires_at = timezone.now() + datetime.timedelta(minutes=1),
        )

    try:
        _send_verification_email(email, name, verification.code)
    except Exception as exc:
        logger.error("OTP email send failed | email=%s | error=%s", email, exc, exc_info=True)
        verification.delete()
        return Response(
            {"status": "error", "message": "Failed to send verification email. Please try again."},
            status=500,
        )

    # Mask the email for display: j***@gmail.com
    local, _, domain = email.partition("@")
    email_hint = local[0] + "***@" + domain

    logger.info("OTP sent | email=%s | pk=%d", email, verification.pk)
    return Response({"status": "ok", "verification_id": verification.pk, "email_hint": email_hint})


# ── Verify OTP ─────────────────────────────────────────────────────────────────
@extend_schema(
    tags=["auth"],
    summary="Verify OTP and get session token (step 2)",
    description=(
        "Submit the 6-digit code received by email. "
        "On success returns a `token` — include it as `X-Chat-Token` in subsequent requests."
    ),
    request=inline_serializer("VerifyOtpRequest", fields={
        "verification_id": s.IntegerField(),
        "code":            s.CharField(max_length=6),
    }),
    responses={
        200: inline_serializer("VerifyOtpResponse", fields={
            "status": s.ChoiceField(choices=["ok", "error"]),
            "token":  s.UUIDField(help_text="Session token for X-Chat-Token header"),
        }),
        400: OpenApiResponse(description="Invalid, expired or already-used code."),
    },
)
@api_view(["POST"])
def verify_otp_view(request):
    """
    Step 2: accept {verification_id, code}. On success, create ChatSession and return token.
    """
    from .models import EmailVerification, ChatSession, Document

    verification_id = request.data.get("verification_id")
    code            = (request.data.get("code") or "").strip()

    if not verification_id:
        return Response({"status": "error", "message": "Missing verification ID."}, status=400)
    if not code:
        return Response({"status": "error", "message": "Please enter the verification code."}, status=400)

    try:
        verification = EmailVerification.objects.get(pk=verification_id, is_verified=False)
    except EmailVerification.DoesNotExist:
        return Response(
            {"status": "error", "message": "Invalid or already used verification. Please start again."},
            status=400,
        )

    if verification.is_expired:
        verification.delete()
        return Response(
            {"status": "error", "code": "expired", "message": "Code has expired. Please request a new one."},
            status=400,
        )

    if verification.code != code:
        return Response(
            {"status": "error", "message": "Incorrect code. Please check and try again."},
            status=400,
        )

    # Mark verified
    verification.is_verified = True
    verification.save(update_fields=["is_verified"])

    # Create ChatSession (same logic as the old start_session_view)
    doc   = Document.get_active()
    token = str(uuid.uuid4())
    session_obj = ChatSession.objects.create(
        session_key   = token,
        user_name     = verification.name,
        user_email    = verification.email,
        user_mobile   = verification.mobile,
        document_name = doc.original_filename if doc else "",
    )

    logger.info(
        "OTP verified, session created | pk=%d | name=%s | email=%s | doc=%s",
        session_obj.pk, verification.name, verification.email,
        doc.original_filename if doc else "—",
    )
    return Response({"status": "ok", "token": token})


# ── Resend OTP ─────────────────────────────────────────────────────────────────
@extend_schema(
    tags=["auth"],
    summary="Resend OTP code",
    description="Regenerate and resend the verification code. Only one resend is allowed per verification session.",
    request=inline_serializer("ResendOtpRequest", fields={
        "verification_id": s.IntegerField(),
    }),
    responses={
        200: inline_serializer("ResendOtpResponse", fields={
            "status": s.ChoiceField(choices=["ok", "error"]),
        }),
        400: OpenApiResponse(description="Invalid session or resend limit already reached."),
        500: OpenApiResponse(description="Failed to resend the email."),
    },
)
@api_view(["POST"])
def resend_otp_view(request):
    """
    Accept {verification_id}. Regenerate code + reset 1-minute expiry. Only one resend allowed.
    """
    from .models import EmailVerification

    verification_id = request.data.get("verification_id")
    if not verification_id:
        return Response({"status": "error", "message": "Missing verification ID."}, status=400)

    try:
        verification = EmailVerification.objects.get(pk=verification_id, is_verified=False)
    except EmailVerification.DoesNotExist:
        return Response(
            {"status": "error", "message": "Invalid verification session. Please start again."},
            status=400,
        )

    if verification.resend_count >= 1:
        return Response(
            {"status": "error", "message": "You have already used your one resend. Please start again."},
            status=400,
        )

    verification.refresh_code()
    verification.resend_count += 1
    verification.save(update_fields=["code", "expires_at", "resend_count"])

    try:
        _send_verification_email(verification.email, verification.name, verification.code)
    except Exception as exc:
        logger.error("OTP resend failed | pk=%d | error=%s", verification.pk, exc, exc_info=True)
        return Response(
            {"status": "error", "message": "Failed to resend email. Please try again."},
            status=500,
        )

    logger.info("OTP resent | pk=%d | email=%s", verification.pk, verification.email)
    return Response({"status": "ok"})


# ── Session Config ─────────────────────────────────────────────────────────────

@api_view(["GET"])
def session_config_view(request):
    """Return admin-controlled settings for the user info collection modal."""
    from .models import ChatSessionConfig
    cfg = ChatSessionConfig.get_active()
    return Response({
        "collect_name":   cfg.collect_name,
        "collect_email":  cfg.collect_email,
        "verify_email":   cfg.verify_email,
        "collect_mobile": cfg.collect_mobile,
    })


@api_view(["POST"])
def start_session_view(request):
    """
    Create a ChatSession directly without email OTP verification.
    Used when verify_email is disabled or no user info is collected at all.
    """
    from .models import ChatSessionConfig, ChatSession
    cfg = ChatSessionConfig.get_active()

    # Guard: if OTP verification is required, this endpoint must not be used
    if cfg.collect_email and cfg.verify_email:
        return Response(
            {"status": "error", "message": "Use /request-otp/ for email verification."},
            status=400,
        )

    name   = (request.data.get("name")   or "").strip()
    email  = (request.data.get("email")  or "").strip().lower()
    mobile = (request.data.get("mobile") or "").strip()

    if cfg.collect_name and not name:
        return Response({"status": "error", "message": "Name is required."}, status=400)
    if cfg.collect_email and not email:
        return Response({"status": "error", "message": "Email is required."}, status=400)

    session = ChatSession.objects.create(
        session_key=str(uuid.uuid4()),
        user_name=name     if cfg.collect_name   else "",
        user_email=email   if cfg.collect_email  else "",
        user_mobile=mobile if cfg.collect_mobile else "",
    )
    return Response({"status": "ok", "token": session.session_key})


# ── Chat (SSE streaming) ───────────────────────────────────────────────────────
@extend_schema(
    tags=["chat"],
    summary="Stream LLM response (SSE)",
    description=(
        "Send a question and receive the LLM answer as a **Server-Sent Events** stream.\n\n"
        "Each event carries a single text token:\n"
        "```\ndata: Hello\\n\\n\ndata:  world\\n\\n\n...\ndata: [DONE]\\n\\n\n```\n\n"
        "Newlines inside a token are escaped as `\\\\n`. "
        "The stream ends with the sentinel event `[DONE]`. "
        "On error the stream ends with `[ERROR: <message>]`.\n\n"
        "Requires a valid `X-Chat-Token` header and an active document."
    ),
    parameters=[OpenApiParameter(
        name="X-Chat-Token",
        location=OpenApiParameter.HEADER,
        description="Session token from `POST /verify-otp/`.",
        required=True,
        type=OpenApiTypes.UUID,
    )],
    request=inline_serializer("ChatRequest", fields={
        "question": s.CharField(),
    }),
    responses={
        200: OpenApiResponse(description="text/event-stream — SSE token stream ending with `[DONE]`."),
        400: OpenApiResponse(description="Empty question or no active document."),
        403: OpenApiResponse(description="Missing or invalid X-Chat-Token."),
        405: OpenApiResponse(description="Method not allowed (must be POST)."),
    },
)
@csrf_exempt
def chat_view(request):
    """
    Accept a JSON POST with {question}, stream LLM tokens back as Server-Sent Events.
    Session is identified by the X-Chat-Token header (stored in browser localStorage).
    Conversation history is loaded from and saved to DB — no Django session state.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    question = (data.get("question") or "").strip()
    if not question:
        return JsonResponse({"status": "error", "message": "Empty question"}, status=400)

    from .models import Document, LLMConfig, ChatMessage, ChatSession

    # Require an active document
    doc = Document.get_active()
    if not doc:
        return JsonResponse(
            {"status": "error", "message": "No document is currently available. Please contact the administrator."},
            status=400,
        )

    # Require a valid session token (name/email gate)
    session_obj = _get_chat_session(request)
    if not session_obj:
        return JsonResponse(
            {"status": "error", "message": "Please provide your name and email to start chatting."},
            status=403,
        )

    # Load conversation history from DB
    db_messages = ChatMessage.objects.filter(session=session_obj).order_by("created_at")
    history = []
    for msg in db_messages:
        history.append({"role": "user",      "content": msg.question})
        history.append({"role": "assistant", "content": msg.answer})

    logger.info(
        "Chat request | session_pk=%d | user=%s | q_chars=%d | history_turns=%d | doc=%s | mode=%s",
        session_obj.pk, session_obj.user_name, len(question),
        len(history) // 2, doc.original_filename, doc.context_mode,
    )

    qdrant_collection = doc.qdrant_collection
    md_path           = doc.markdown_path

    # Resolve effective context for the LLM
    from .providers.utils import is_conversational as _is_conversational
    cfg_active    = LLMConfig.get_active()
    rag_embedding = cfg_active.rag_embedding
    # "auto" defers to the mode computed at document upload time
    context_mode = doc.context_mode if cfg_active.context_mode == "auto" else cfg_active.context_mode
    # Respect cache toggle: treat as uncached when disabled
    gemini_cache_name = (doc.gemini_cache_name or None) if cfg_active.use_gemini_cache else None

    # ── Lazy Gemini cache creation ──────────────────────────────────────────────
    # If provider is now Gemini, document is in full-context mode, but no cache
    # exists yet (e.g. document was uploaded while a different provider was active),
    # create the cache now and persist it so every subsequent request reuses it.
    if (
        cfg_active.provider == "gemini"
        and context_mode == "full"
        and cfg_active.use_gemini_cache
        and not gemini_cache_name
        and md_path
        and os.path.exists(md_path)
        and settings.GEMINI_API_KEY
    ):
        try:
            from .pipeline import create_gemini_cache as _create_cache
            with open(md_path, "r", encoding="utf-8") as _f:
                _md_text = _f.read()
            _cache_name = _create_cache(_md_text, cfg_active.gemini_model)
            if _cache_name:
                Document.objects.filter(pk=doc.pk).update(gemini_cache_name=_cache_name)
                gemini_cache_name = _cache_name
                logger.info(
                    "Lazy Gemini cache created | doc_pk=%d | name=%s | model=%s",
                    doc.pk, _cache_name, cfg_active.gemini_model,
                )
        except Exception as _exc:
            logger.warning("Lazy Gemini cache creation failed | doc_pk=%d | error=%s", doc.pk, _exc)
    # ───────────────────────────────────────────────────────────────────────────

    has_chunks = bool(qdrant_collection)

    def _rag_query(q: str, hist: list) -> str:
        """
        For short follow-up questions (≤ 8 words) prepend the last user turn so
        the RAG retriever has enough context to find the right chunks.
        e.g. "ok for mca?" → "BCA ma admission leva su joyeye ok for mca?"
        """
        if len(q.split()) <= 3 and hist:
            last_user = next(
                (m["content"] for m in reversed(hist) if m["role"] == "user"), None
            )
            if last_user:
                return f"{last_user} {q}"
        return q

    # ── Agent mode: load memory; context is resolved inside the agent loop ──────
    user_memory = ""
    if cfg_active.agent_mode:
        from .agent.memory import load_memory
        user_memory = load_memory(session_obj.user_email)
        markdown_text = ""  # agent loop handles context retrieval via tools
    elif context_mode == "rag" and has_chunks:
        from .pipeline import retrieve_relevant_context_qdrant
        markdown_text = retrieve_relevant_context_qdrant(
            _rag_query(question, history), qdrant_collection, rag_embedding
        )

    elif cfg_active.provider == "sarvam":
        if _is_conversational(question):
            markdown_text = ""
        else:
            with open(md_path, "r", encoding="utf-8") as f:
                full_text = f.read()
            # Gujarati/Indic script is ~2–3 chars/token — keep in sync with sarvam.py
            _SARVAM_BUDGET = 9_000
            if len(full_text) <= _SARVAM_BUDGET:
                markdown_text = full_text
            elif has_chunks:
                from .pipeline import retrieve_relevant_context_qdrant
                markdown_text = retrieve_relevant_context_qdrant(
                    _rag_query(question, history), qdrant_collection, rag_embedding, top_k=3
                )
            else:
                markdown_text = full_text[:_SARVAM_BUDGET]

    else:
        with open(md_path, "r", encoding="utf-8") as f:
            markdown_text = f.read()

    def generate():
        from .providers.gemini import GeminiCacheExpiredError

        full_response: list[str] = []
        usage_out: dict = {}
        t0 = time.perf_counter()
        try:
            if cfg_active.agent_mode:
                from .agent.loop import run_agent_streaming
                _stream = run_agent_streaming(question, history, doc, cfg_active, user_memory, usage_out)
            else:
                _stream = ask_streaming(question, history, markdown_text, usage_out=usage_out,
                                        gemini_cache_name=gemini_cache_name)
            for token in _stream:
                full_response.append(token)
                safe_token = token.replace("\n", "\\n")
                yield f"data: {safe_token}\n\n"
        except GeminiCacheExpiredError:
            # Cache invalid (expired or model mismatch) — clear stale name, recache, then retry.
            # This is fully transparent to the user: no error is shown.
            logger.warning(
                "Gemini cache invalid | doc_pk=%d | stale_cache=%s | recaching with current model=%s",
                doc.pk, gemini_cache_name, cfg_active.gemini_model,
            )
            Document.objects.filter(pk=doc.pk).update(gemini_cache_name="")
            full_response.clear()
            usage_out.clear()
            # Attempt to create a fresh cache with the currently configured model.
            new_cache_name = None
            if md_path and os.path.exists(md_path) and settings.GEMINI_API_KEY:
                try:
                    from .pipeline import create_gemini_cache as _create_cache
                    new_cache_name = _create_cache(markdown_text, cfg_active.gemini_model)
                    if new_cache_name:
                        Document.objects.filter(pk=doc.pk).update(gemini_cache_name=new_cache_name)
                        logger.info(
                            "Gemini recache succeeded | doc_pk=%d | new_cache=%s | model=%s",
                            doc.pk, new_cache_name, cfg_active.gemini_model,
                        )
                except Exception as _cache_exc:
                    logger.warning("Gemini recache failed | doc_pk=%d | error=%s", doc.pk, _cache_exc)
            try:
                for token in ask_streaming(question, history, markdown_text, usage_out=usage_out,
                                           gemini_cache_name=new_cache_name):
                    full_response.append(token)
                    safe_token = token.replace("\n", "\\n")
                    yield f"data: {safe_token}\n\n"
            except Exception as retry_exc:
                logger.error(
                    "Chat stream retry error | session_pk=%d | error=%s",
                    session_obj.pk, retry_exc, exc_info=True,
                )
                yield f"data: [ERROR: {str(retry_exc)}]\n\n"
                return
        except Exception as e:
            logger.error(
                "Chat stream error | session_pk=%d | error=%s",
                session_obj.pk, e, exc_info=True,
            )
            yield f"data: [ERROR: {str(e)}]\n\n"
            return

        elapsed = time.perf_counter() - t0
        logger.info(
            "Chat complete | session_pk=%d | in_tokens=%d out_tokens=%d | ctx_chars=%d | time=%.2fs",
            session_obj.pk,
            usage_out.get("input_tokens", 0),
            usage_out.get("output_tokens", 0),
            len(markdown_text),
            elapsed,
        )

        # ── Save cost record and message to DB ────────────────────────────────
        try:
            from decimal import Decimal
            from django.db.models import DecimalField, ExpressionWrapper, F, FloatField
            from .models import ModelPricing

            cfg      = LLMConfig.get_active()
            provider = cfg.provider
            if provider == "gemini":
                model = cfg.gemini_model
            elif provider == "sarvam":
                model = cfg.sarvam_model
            else:
                model = cfg.ollama_model

            input_tokens         = usage_out.get("input_tokens", 0)
            output_tokens        = usage_out.get("output_tokens", 0)
            cached_input_tokens  = usage_out.get("cached_input_tokens", 0)
            non_cached_input     = input_tokens - cached_input_tokens
            total_tokens         = input_tokens + output_tokens
            estimated            = usage_out.get("estimated", False)

            try:
                pricing = ModelPricing.objects.get(provider=provider, model_name=model, is_active=True)
                # Non-cached input at standard rate
                input_cost  = Decimal(non_cached_input) * pricing.input_price_per_million  / Decimal(1_000_000)
                output_cost = Decimal(output_tokens)    * pricing.output_price_per_million / Decimal(1_000_000)
                # Cached tokens: read rate (cheaper)
                cache_read_cost = (
                    Decimal(cached_input_tokens) * pricing.cache_read_price_per_million / Decimal(1_000_000)
                    if cached_input_tokens and pricing.cache_read_price_per_million
                    else Decimal(0)
                )
                # Storage: only billed when WE created an explicit Gemini cache.
                # Gemini's automatic/implicit caching gives a read-rate discount but
                # does not incur a separate storage charge.
                gemini_explicit_cache = usage_out.get("gemini_explicit_cache", False)
                cache_storage_cost = (
                    Decimal(cached_input_tokens) * pricing.cache_storage_price_per_million_per_hour / Decimal(1_000_000)
                    if gemini_explicit_cache and cached_input_tokens and pricing.cache_storage_price_per_million_per_hour
                    else Decimal(0)
                )
            except ModelPricing.DoesNotExist:
                input_cost = output_cost = cache_read_cost = cache_storage_cost = Decimal(0)
            total_cost = input_cost + output_cost + cache_read_cost + cache_storage_cost

            cache_label = "explicit_cached" if gemini_explicit_cache else "auto_cached"
            logger.info(
                "Cost | session_pk=%d | provider=%s | model=%s | in=%d %s=%d out=%d est=%s | cost=₹%.6f (cache_read=₹%.6f storage=₹%.6f)",
                session_obj.pk, provider, model,
                input_tokens, cache_label, cached_input_tokens, output_tokens, estimated,
                total_cost, cache_read_cost, cache_storage_cost,
            )

            ChatMessage.objects.create(
                session=session_obj,
                provider=provider,
                model_name=model,
                question=question,
                answer="".join(full_response),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                tokens_estimated=estimated,
                cached_input_tokens=cached_input_tokens,
                input_cost=input_cost,
                output_cost=output_cost,
                cache_read_cost=cache_read_cost,
                cache_storage_cost=cache_storage_cost,
                total_cost=total_cost,
                response_time_seconds=elapsed,
            )

            ChatSession.objects.filter(pk=session_obj.pk).update(
                total_input_tokens         =F("total_input_tokens")          + input_tokens,
                total_output_tokens        =F("total_output_tokens")         + output_tokens,
                total_tokens               =F("total_tokens")                + total_tokens,
                total_cached_input_tokens  =F("total_cached_input_tokens")   + cached_input_tokens,
                total_cost                 =F("total_cost")                  + total_cost,
                total_cache_read_cost      =F("total_cache_read_cost")       + cache_read_cost,
                total_cache_storage_cost   =F("total_cache_storage_cost")    + cache_storage_cost,
                message_count              =F("message_count")               + 1,
                document_name             =doc.original_filename,
                avg_tokens_per_message=ExpressionWrapper(
                    (F("total_tokens") + total_tokens) / (F("message_count") + 1),
                    output_field=FloatField(),
                ),
                avg_cost_per_message  =ExpressionWrapper(
                    (F("total_cost") + total_cost) / (F("message_count") + 1),
                    output_field=DecimalField(max_digits=14, decimal_places=6),
                ),
            )
            logger.info("DB save OK | session_pk=%d | q_chars=%d", session_obj.pk, len(question))
        except Exception as db_exc:
            logger.error(
                "DB save FAILED | session_pk=%d | error=%s",
                session_obj.pk, db_exc, exc_info=True,
            )

        # ── Agent memory update (every 5 messages, non-blocking) ─────────────
        if cfg_active.agent_mode:
            new_count = session_obj.message_count + 1
            if new_count % 5 == 0:
                import threading
                updated_history = history + [
                    {"role": "user",      "content": question},
                    {"role": "assistant", "content": "".join(full_response)},
                ]
                from .agent.memory import save_memory
                threading.Thread(
                    target=save_memory,
                    args=(session_obj.user_email, updated_history, doc.original_filename),
                    daemon=True,
                ).start()
                logger.info("Agent memory update scheduled | email=%s", session_obj.user_email)

        # ── [DONE] always sent regardless of DB save result ───────────────────

        yield "data: [DONE]\n\n"

    response = StreamingHttpResponse(generate(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ── Embeddable widget iframe page ─────────────────────────────────────────────
@extend_schema(exclude=True)
@xframe_options_exempt
@ensure_csrf_cookie
def widget_view(request):
    """
    Serve the embeddable chatbot widget as a standalone iframe page.
    @xframe_options_exempt allows any external site to embed this URL in an iframe.
    All API calls made inside the iframe target the same origin, so no CORS is needed.
    """
    return render(request, "widget.html")


# ── Reset (end current named session) ─────────────────────────────────────────
@extend_schema(
    tags=["session"],
    summary="End session",
    description=(
        "Log the end of the session. "
        "The frontend removes the token from localStorage; DB records are preserved for admin reporting."
    ),
    parameters=[_CHAT_TOKEN_HEADER],
    request=None,
    responses={200: inline_serializer("ResetResponse", fields={
        "status": s.ChoiceField(choices=["ok"]),
    })},
)
@api_view(["POST"])
def reset_view(request):
    """
    Log the end of a session. The actual reset is done on the frontend by clearing
    the localStorage token. DB records are preserved for admin reporting.
    """
    session_obj = _get_chat_session(request)
    logger.info("Reset | session_pk=%s", session_obj.pk if session_obj else "—")
    return Response({"status": "ok"})

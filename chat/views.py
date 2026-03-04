import json
import logging
import os
import time
import uuid

from django.conf import settings
from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .pipeline import ask_streaming, retrieve_relevant_context

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
@ensure_csrf_cookie
def index(request):
    """Serve the single-page chat UI and set the CSRF cookie."""
    return render(request, "index.html")


# ── Status ─────────────────────────────────────────────────────────────────────
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
@api_view(["POST"])
def request_otp_view(request):
    """
    Step 1: accept {name, email}, send a 6-digit code, return {status, verification_id, email_hint}.
    Cleans up stale records for the same email before creating a new one.
    """
    import datetime
    from django.utils import timezone
    from .models import EmailVerification

    name  = (request.data.get("name")  or "").strip()
    email = (request.data.get("email") or "").strip().lower()

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
        existing.name = name
        existing.save(update_fields=["name"])
        verification = existing
    else:
        verification = EmailVerification.objects.create(
            email      = email,
            name       = name,
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
        document_name = doc.original_filename if doc else "",
    )

    logger.info(
        "OTP verified, session created | pk=%d | name=%s | email=%s | doc=%s",
        session_obj.pk, verification.name, verification.email,
        doc.original_filename if doc else "—",
    )
    return Response({"status": "ok", "token": token})


# ── Resend OTP ─────────────────────────────────────────────────────────────────
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


# ── Chat (SSE streaming) ───────────────────────────────────────────────────────
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

    rag_chunks_path   = doc.rag_chunks_path
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

    has_chunks = bool(rag_chunks_path and os.path.exists(rag_chunks_path))

    if context_mode == "rag" and has_chunks:
        markdown_text = retrieve_relevant_context(question, rag_chunks_path, rag_embedding)

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
                markdown_text = retrieve_relevant_context(
                    question, rag_chunks_path, rag_embedding, top_k=3
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
            for token in ask_streaming(question, history, markdown_text, usage_out=usage_out,
                                       gemini_cache_name=gemini_cache_name):
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
        response_chars = sum(len(t) for t in full_response)
        logger.info(
            "Chat complete | session_pk=%d | response_chars=%d | time=%.2fs",
            session_obj.pk, response_chars, elapsed,
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
                # Storage: 1-hour approximation per message that uses the cache
                cache_storage_cost = (
                    Decimal(cached_input_tokens) * pricing.cache_storage_price_per_million_per_hour / Decimal(1_000_000)
                    if cached_input_tokens and pricing.cache_storage_price_per_million_per_hour
                    else Decimal(0)
                )
            except ModelPricing.DoesNotExist:
                input_cost = output_cost = cache_read_cost = cache_storage_cost = Decimal(0)
            total_cost = input_cost + output_cost + cache_read_cost + cache_storage_cost

            logger.info(
                "Cost | session_pk=%d | provider=%s | model=%s | in=%d cached=%d out=%d est=%s | cost=₹%.6f (cache_read=₹%.6f storage=₹%.6f)",
                session_obj.pk, provider, model,
                input_tokens, cached_input_tokens, output_tokens, estimated,
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
        # ── [DONE] always sent regardless of DB save result ───────────────────

        yield "data: [DONE]\n\n"

    response = StreamingHttpResponse(generate(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ── Reset (end current named session) ─────────────────────────────────────────
@api_view(["POST"])
def reset_view(request):
    """
    Log the end of a session. The actual reset is done on the frontend by clearing
    the localStorage token. DB records are preserved for admin reporting.
    """
    session_obj = _get_chat_session(request)
    logger.info("Reset | session_pk=%s", session_obj.pk if session_obj else "—")
    return Response({"status": "ok"})

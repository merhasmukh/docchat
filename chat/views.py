import json
import logging
import os
import time
import uuid

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


# ── Start session (name + email gate) ─────────────────────────────────────────
@api_view(["POST"])
def start_session_view(request):
    """
    Create a new ChatSession tied to the user's name and email.
    Returns a session token the frontend stores in localStorage.
    No Django session writes.
    """
    name  = (request.data.get("name") or "").strip()
    email = (request.data.get("email") or "").strip()

    if not name:
        return Response({"status": "error", "message": "Name is required."}, status=400)
    if not email:
        return Response({"status": "error", "message": "Email is required."}, status=400)

    from .models import Document, ChatSession

    doc         = Document.get_active()
    token       = str(uuid.uuid4())
    session_obj = ChatSession.objects.create(
        session_key=token,
        user_name=name,
        user_email=email,
        document_name=doc.original_filename if doc else "",
    )

    logger.info(
        "New session | pk=%d | name=%s | email=%s | doc=%s",
        session_obj.pk, name, email, doc.original_filename if doc else "—",
    )
    # Return the token — frontend saves it to localStorage
    return Response({"status": "ok", "token": token})


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

    context_mode      = doc.context_mode
    gemini_cache_name = doc.gemini_cache_name or None
    rag_chunks_path   = doc.rag_chunks_path
    md_path           = doc.markdown_path

    # Resolve effective context for the LLM
    from .providers.utils import is_conversational as _is_conversational
    cfg_active    = LLMConfig.get_active()
    rag_embedding = cfg_active.rag_embedding

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
                    question, rag_chunks_path, rag_embedding, top_k=5
                )
            else:
                markdown_text = full_text[:_SARVAM_BUDGET]

    else:
        with open(md_path, "r", encoding="utf-8") as f:
            markdown_text = f.read()

    def generate():
        full_response: list[str] = []
        usage_out: dict = {}
        t0 = time.perf_counter()
        try:
            for token in ask_streaming(question, history, markdown_text, usage_out=usage_out,
                                       gemini_cache_name=gemini_cache_name):
                full_response.append(token)
                safe_token = token.replace("\n", "\\n")
                yield f"data: {safe_token}\n\n"
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
            from django.db.models import F
            from .models import ModelPricing

            cfg      = LLMConfig.get_active()
            provider = cfg.provider
            if provider == "gemini":
                model = cfg.gemini_model
            elif provider == "sarvam":
                model = cfg.sarvam_model
            else:
                model = cfg.ollama_model

            input_tokens  = usage_out.get("input_tokens", 0)
            output_tokens = usage_out.get("output_tokens", 0)
            total_tokens  = input_tokens + output_tokens
            estimated     = usage_out.get("estimated", False)

            try:
                pricing     = ModelPricing.objects.get(provider=provider, model_name=model, is_active=True)
                input_cost  = Decimal(input_tokens)  * pricing.input_price_per_million  / Decimal(1_000_000)
                output_cost = Decimal(output_tokens) * pricing.output_price_per_million / Decimal(1_000_000)
            except ModelPricing.DoesNotExist:
                input_cost = output_cost = Decimal(0)
            total_cost = input_cost + output_cost

            logger.info(
                "Cost | session_pk=%d | provider=%s | model=%s | in=%d out=%d est=%s | cost=₹%.6f",
                session_obj.pk, provider, model,
                input_tokens, output_tokens, estimated, total_cost,
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
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                response_time_seconds=elapsed,
            )

            ChatSession.objects.filter(pk=session_obj.pk).update(
                total_input_tokens =F("total_input_tokens")  + input_tokens,
                total_output_tokens=F("total_output_tokens") + output_tokens,
                total_tokens       =F("total_tokens")        + total_tokens,
                total_cost         =F("total_cost")          + total_cost,
                message_count      =F("message_count")       + 1,
                document_name      =doc.original_filename,
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

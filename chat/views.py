import json
import logging
import os
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import JsonResponse

from .pipeline import (
    convert_to_markdown, ask_streaming,
    create_gemini_cache, delete_gemini_cache,
    build_rag_chunks, retrieve_relevant_context,
)

logger = logging.getLogger("chat.views")

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


# ── Index ──────────────────────────────────────────────────────────────────────
@ensure_csrf_cookie
def index(request):
    """Serve the single-page chat UI and set the CSRF cookie."""
    return render(request, "index.html")


# ── Status ─────────────────────────────────────────────────────────────────────
@api_view(["GET"])
def status_view(request):
    """Return whether a document is currently loaded for this session."""
    md_path = request.session.get("markdown_path")
    loaded = bool(md_path and os.path.exists(md_path))
    return Response({
        "document_loaded": loaded,
        "filename": request.session.get("original_filename") if loaded else None,
        "total_pages": request.session.get("total_pages") if loaded else None,
    })


# ── Upload ─────────────────────────────────────────────────────────────────────
@api_view(["POST"])
def upload_view(request):
    """
    Receive a file upload, run the OCR pipeline, persist markdown + JSON to disk,
    and store references in the Django session.
    """
    if "file" not in request.FILES:
        logger.warning("Upload rejected: no file in request")
        return Response({"status": "error", "message": "No file in request"}, status=400)

    file = request.FILES["file"]
    if not file.name:
        logger.warning("Upload rejected: empty filename")
        return Response({"status": "error", "message": "No file selected"}, status=400)

    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning("Upload rejected: unsupported extension '%s' for file '%s'", ext, file.name)
        return Response({
            "status": "error",
            "message": f"File type '{ext}' not supported. Allowed: PDF, PNG, JPG, TIFF, BMP, WEBP",
        }, status=400)

    file_size = file.size
    logger.info("Upload received | file=%s | size=%d bytes | ext=%s", file.name, file_size, ext)

    # Save upload to a temporary path
    safe_name = str(uuid.uuid4()) + ext
    upload_path = os.path.join(settings.UPLOAD_FOLDER, safe_name)
    with open(upload_path, "wb") as f:
        for chunk in file.chunks():
            f.write(chunk)

    # Run OCR pipeline (blocking — 10-60 s for large PDFs)
    t0 = time.perf_counter()
    try:
        markdown_text, pages_data = convert_to_markdown(str(upload_path))
    except Exception as e:
        logger.error("OCR failed | file=%s | error=%s", file.name, e, exc_info=True)
        return Response({"status": "error", "message": f"OCR failed: {str(e)}"}, status=500)
    finally:
        if os.path.exists(upload_path):
            os.remove(upload_path)

    ocr_elapsed = time.perf_counter() - t0
    logger.info(
        "OCR pipeline done | file=%s | pages=%d | chars=%d | time=%.2fs",
        file.name, pages_data["total_pages"], len(markdown_text), ocr_elapsed,
    )

    # Shared UUID for .md and .json pair
    base_id = str(uuid.uuid4())
    md_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".md")
    json_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    pages_data["source_file"] = file.name
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(pages_data, f, ensure_ascii=False, indent=2)

    # ── Determine context mode ────────────────────────────────────────────────
    from .models import LLMConfig
    threshold    = getattr(settings, "CONTEXT_CHAR_THRESHOLD", 100_000)
    doc_chars    = len(markdown_text)
    context_mode = "rag" if doc_chars > threshold else "full"
    logger.info(
        "Context mode | doc_chars=%d | threshold=%d | mode=%s",
        doc_chars, threshold, context_mode,
    )

    # ── Build Gemini cache or BM25 index ─────────────────────────────────────
    gemini_cache_name = None
    rag_chunks_path   = None

    cfg = LLMConfig.get_active()
    if context_mode == "full":
        if cfg.provider == "gemini" and settings.GEMINI_API_KEY:
            gemini_cache_name = create_gemini_cache(markdown_text, cfg.gemini_model)
    else:
        chunks          = build_rag_chunks(pages_data, cfg.rag_embedding)
        rag_chunks_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + "_chunks.json")
        with open(rag_chunks_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)

    # ── Remove old session files / Gemini cache ───────────────────────────────
    for key in ("markdown_path", "json_path", "rag_chunks_path"):
        old_file = request.session.get(key)
        if old_file and os.path.exists(old_file):
            os.remove(old_file)
            logger.debug("Removed old session file: %s", old_file)
    old_cache = request.session.get("gemini_cache_name")
    if old_cache:
        delete_gemini_cache(old_cache)

    # ── Store references and reset conversation history in session ────────────
    request.session["markdown_path"]     = md_path
    request.session["json_path"]         = json_path
    request.session["original_filename"] = file.name
    request.session["total_pages"]       = pages_data["total_pages"]
    request.session["context_mode"]      = context_mode
    request.session["gemini_cache_name"] = gemini_cache_name
    request.session["rag_chunks_path"]   = rag_chunks_path
    request.session["history"]           = []

    logger.info(
        "Upload complete | file=%s | session_id=%s | mode=%s | cache=%s",
        file.name, request.session.session_key, context_mode,
        "yes" if gemini_cache_name else "no",
    )
    return Response({
        "status": "ok",
        "filename": file.name,
        "total_pages": pages_data["total_pages"],
        "message": "Document processed. You can now ask questions.",
        "char_count": doc_chars,
        "context_mode": context_mode,
    })


# ── Chat (SSE streaming) ───────────────────────────────────────────────────────
@csrf_exempt
def chat_view(request):
    """
    Accept a JSON POST with {question}, stream LLM tokens back as Server-Sent Events.

    Uses a plain Django view (not DRF) because StreamingHttpResponse cannot be
    wrapped by DRF's Response. CSRF is exempt because DRF API views with no
    authentication are already exempt, and this local app is not internet-facing.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    question = (data.get("question") or "").strip()
    if not question:
        logger.warning("Chat rejected: empty question | session=%s", request.session.session_key)
        return JsonResponse({"status": "error", "message": "Empty question"}, status=400)

    md_path = request.session.get("markdown_path")
    if not md_path or not os.path.exists(md_path):
        logger.warning("Chat rejected: no document loaded | session=%s", request.session.session_key)
        return JsonResponse(
            {"status": "error", "message": "No document loaded. Please upload a file first."},
            status=400,
        )

    context_mode      = request.session.get("context_mode", "full")
    gemini_cache_name = request.session.get("gemini_cache_name")
    rag_chunks_path   = request.session.get("rag_chunks_path")

    history = list(request.session.get("history", []))
    logger.info(
        "Chat request | session=%s | q_chars=%d | history_turns=%d | doc=%s | mode=%s",
        request.session.session_key, len(question), len(history) // 2,
        request.session.get("original_filename", "unknown"), context_mode,
    )

    # Resolve effective context: retrieved chunks (RAG) or full markdown (full-context)
    from .models import LLMConfig
    rag_embedding = LLMConfig.get_active().rag_embedding
    if context_mode == "rag" and rag_chunks_path and os.path.exists(rag_chunks_path):
        markdown_text = retrieve_relevant_context(question, rag_chunks_path, rag_embedding)
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
            logger.error("Chat stream error | session=%s | error=%s", request.session.session_key, e, exc_info=True)
            yield f"data: [ERROR: {str(e)}]\n\n"
            return

        elapsed = time.perf_counter() - t0
        response_chars = sum(len(t) for t in full_response)
        logger.info(
            "Chat complete | session=%s | response_chars=%d | time=%.2fs",
            request.session.session_key, response_chars, elapsed,
        )

        # ── Save cost record ──────────────────────────────────────────────────
        from decimal import Decimal
        from django.db.models import F
        from .models import ChatSession, ChatMessage, ModelPricing, LLMConfig

        cfg      = LLMConfig.get_active()
        provider = cfg.provider
        model    = cfg.gemini_model if provider == "gemini" else cfg.ollama_model

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
            "Cost | session=%s | provider=%s | model=%s | in=%d out=%d est=%s | cost=₹%.6f",
            request.session.session_key, provider, model,
            input_tokens, output_tokens, estimated, total_cost,
        )

        session_obj, _ = ChatSession.objects.get_or_create(
            session_key=request.session.session_key,
            defaults={"document_name": request.session.get("original_filename", "")},
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
            document_name      =request.session.get("original_filename", ""),
        )
        # ─────────────────────────────────────────────────────────────────────

        # Persist the completed exchange to the DB-backed session
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": "".join(full_response)})
        request.session["history"] = history
        request.session.modified = True
        request.session.save()   # Explicit save — middleware process_response already ran

        yield "data: [DONE]\n\n"

    response = StreamingHttpResponse(generate(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ── Reset ──────────────────────────────────────────────────────────────────────
@api_view(["POST"])
def reset_view(request):
    """Delete session files and clear session data for the current user."""
    logger.info("Reset | session=%s | doc=%s", request.session.session_key, request.session.get("original_filename"))

    # Delete disk files
    for key in ("markdown_path", "json_path", "rag_chunks_path"):
        path = request.session.get(key)
        if path and os.path.exists(path):
            os.remove(path)
            logger.debug("Deleted file: %s", path)

    # Delete Gemini cache if one was created
    cache_name = request.session.get("gemini_cache_name")
    if cache_name:
        delete_gemini_cache(cache_name)

    for key in (
        "markdown_path", "json_path", "original_filename", "total_pages", "history",
        "context_mode", "gemini_cache_name", "rag_chunks_path",
    ):
        request.session.pop(key, None)

    return Response({"status": "ok"})

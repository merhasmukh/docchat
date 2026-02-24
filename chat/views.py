import json
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .pipeline import convert_to_markdown, ask_streaming

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
        return Response({"status": "error", "message": "No file in request"}, status=400)

    file = request.FILES["file"]
    if not file.name:
        return Response({"status": "error", "message": "No file selected"}, status=400)

    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return Response({
            "status": "error",
            "message": f"File type '{ext}' not supported. Allowed: PDF, PNG, JPG, TIFF, BMP, WEBP",
        }, status=400)

    # Save upload to a temporary path
    safe_name = str(uuid.uuid4()) + ext
    upload_path = os.path.join(settings.UPLOAD_FOLDER, safe_name)
    with open(upload_path, "wb") as f:
        for chunk in file.chunks():
            f.write(chunk)

    # Run OCR pipeline (blocking — 10-60 s for large PDFs)
    try:
        markdown_text, pages_data = convert_to_markdown(str(upload_path))
    except Exception as e:
        return Response({"status": "error", "message": f"OCR failed: {str(e)}"}, status=500)
    finally:
        if os.path.exists(upload_path):
            os.remove(upload_path)

    # Shared UUID for .md and .json pair
    base_id = str(uuid.uuid4())
    md_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".md")
    json_path = os.path.join(settings.MARKDOWN_FOLDER, base_id + ".json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    pages_data["source_file"] = file.name
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(pages_data, f, ensure_ascii=False, indent=2)

    # Remove old session files if they exist
    for key in ("markdown_path", "json_path"):
        old_file = request.session.get(key)
        if old_file and os.path.exists(old_file):
            os.remove(old_file)

    # Store references and reset conversation history in session
    request.session["markdown_path"] = md_path
    request.session["json_path"] = json_path
    request.session["original_filename"] = file.name
    request.session["total_pages"] = pages_data["total_pages"]
    request.session["history"] = []

    return Response({
        "status": "ok",
        "filename": file.name,
        "total_pages": pages_data["total_pages"],
        "message": "Document processed. You can now ask questions.",
        "char_count": len(markdown_text),
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
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    question = (data.get("question") or "").strip()
    if not question:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "message": "Empty question"}, status=400)

    md_path = request.session.get("markdown_path")
    if not md_path or not os.path.exists(md_path):
        from django.http import JsonResponse
        return JsonResponse(
            {"status": "error", "message": "No document loaded. Please upload a file first."},
            status=400,
        )

    history = list(request.session.get("history", []))

    with open(md_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    def generate():
        full_response: list[str] = []
        try:
            for token in ask_streaming(question, history, markdown_text):
                full_response.append(token)
                safe_token = token.replace("\n", "\\n")
                yield f"data: {safe_token}\n\n"
        except Exception as e:
            yield f"data: [ERROR: {str(e)}]\n\n"
            return

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
    for key in ("markdown_path", "json_path"):
        path = request.session.get(key)
        if path and os.path.exists(path):
            os.remove(path)

    for key in ("markdown_path", "json_path", "original_filename", "total_pages", "history"):
        request.session.pop(key, None)

    return Response({"status": "ok"})

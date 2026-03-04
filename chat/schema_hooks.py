"""
drf-spectacular postprocessing hook to manually inject endpoints that cannot be
auto-discovered (i.e. plain Django views that don't use DRF's APIView/api_view).

Currently this adds the /chat/ SSE streaming endpoint.
"""


def inject_chat_endpoint(result, generator, request, public):
    """Inject the /chat/ Server-Sent Events endpoint into the OpenAPI schema."""
    result["paths"]["/chat/"] = {
        "post": {
            "operationId": "chat_create",
            "summary": "Stream LLM response (SSE)",
            "description": (
                "Send a question and receive the LLM answer as a **Server-Sent Events** stream.\n\n"
                "Each SSE event carries a single text token:\n"
                "```\n"
                "data: Hello\\n\\n\n"
                "data:  world\\n\\n\n"
                "...\n"
                "data: [DONE]\\n\\n\n"
                "```\n\n"
                "Newlines inside a token are escaped as `\\\\n`.\n"
                "The stream ends with the sentinel event `[DONE]`.\n"
                "On error the stream ends with `[ERROR: <message>]`.\n\n"
                "Requires a valid `X-Chat-Token` header and an active document."
            ),
            "tags": ["chat"],
            "parameters": [
                {
                    "in": "header",
                    "name": "X-Chat-Token",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                    "description": "Session token from `POST /verify-otp/`.",
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["question"],
                            "properties": {
                                "question": {"type": "string", "example": "What is this document about?"}
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": (
                        "text/event-stream — SSE token stream ending with `[DONE]`."
                    ),
                    "content": {
                        "text/event-stream": {
                            "schema": {"type": "string"},
                            "example": "data: Hello\\n\\ndata:  world\\n\\ndata: [DONE]\\n\\n",
                        }
                    },
                },
                "400": {"description": "Empty question or no active document."},
                "403": {"description": "Missing or invalid X-Chat-Token."},
                "405": {"description": "Method not allowed (must be POST)."},
            },
            "security": [],
        }
    }
    return result

"""OpenAPI polish: ordered tags, the API-key security scheme, and one error model.

The served schema is the generated one plus three deterministic rewrites, applied once and cached:
- `securitySchemes.ApiKey` (HTTP bearer) is declared and applied to every operation except health;
- every 4xx/5xx response is `application/problem+json` referencing `ErrorResponse` (FastAPI's default
  `HTTPValidationError` is replaced: the app renders every error as problem+json, see `app/api/errors.py`);
- operations get the errors every authenticated route can return (401, 429), and 422 when they take input.
"""

from typing import Any

from fastapi import FastAPI

from app.api.errors import PROBLEM_JSON
from app.api.schemas import ErrorResponse

TAGS: list[dict[str, str]] = [
    {
        "name": "Documents",
        "description": "Upload, versions, processing status, briefs, summaries and sources.",
    },
    {"name": "Intelligence", "description": "Cross-document analysis: matrices and vault-wide extraction."},
    {"name": "Chat", "description": "Sessions, cited answers, history and answer feedback."},
    {"name": "Runs", "description": "Inspect and replay a single answer run."},
    {
        "name": "Metrics",
        "description": "Documents, processing and chat metrics (owner scope; admin may use global).",
    },
    {
        "name": "Session",
        "description": "Exchange an API key for an httpOnly session cookie (browser UI); curl uses Bearer.",
    },
    {"name": "Health", "description": "Liveness and readiness. No authentication."},
]
SECURITY_SCHEME = "ApiKey"
COOKIE_SCHEME = "SessionCookie"
HEALTH_TAG = "Health"

_ERROR_DESCRIPTIONS = {
    "400": "Bad request",
    "401": "Missing or invalid API key",
    "403": "Forbidden",
    "404": "Not found, or not yours",
    "409": "Conflict",
    "413": "File too large",
    "415": "Unsupported file type",
    "422": "Request validation failed",
    "429": "Rate limited (see `RateLimit-*` headers)",
    "503": "A dependency is unavailable",
}
_ERROR_EXAMPLE = {
    "type": "https://vault.local/problems/not_found",
    "title": "Not found",
    "status": 404,
    "code": "not_found",
    "detail": None,
    "retryable": False,
}


def _is_error(code: str) -> bool:
    return code.isdigit() and int(code) >= 400


def _error_response(code: str, existing: dict[str, Any] | None) -> dict[str, Any]:
    description = (existing or {}).get("description")
    if not description or description == "Validation Error":
        description = _ERROR_DESCRIPTIONS.get(code, "Error")
    example = {**_ERROR_EXAMPLE, "status": int(code)}
    return {
        "description": description,
        "content": {
            PROBLEM_JSON: {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": example,
            }
        },
    }


def polish(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    err = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas["ErrorResponse"] = err
    components["securitySchemes"] = {
        SECURITY_SCHEME: {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "vk_<token>",
            "description": "Your API key: `Authorization: Bearer vk_...`",
        },
        COOKIE_SCHEME: {
            "type": "apiKey",
            "in": "cookie",
            "name": "vault_session",
            "description": (
                "Set by `POST /v1/session` (httpOnly, SameSite=Strict). State-changing requests "
                "authenticated by this cookie must also send `X-Requested-With: vault-ui`; "
                "Bearer requests need no such header."
            ),
        },
    }
    schema["tags"] = TAGS
    for ops in schema["paths"].values():
        for op in ops.values():
            if not isinstance(op, dict) or "responses" not in op:
                continue
            health = HEALTH_TAG in op.get("tags", [])
            responses: dict[str, Any] = op["responses"]
            wanted = set() if health else {"401", "429"}
            if not health and (op.get("parameters") or op.get("requestBody")):
                wanted.add("422")
            for code in wanted:
                responses.setdefault(code, {})
            for code in list(responses):
                if _is_error(code):
                    responses[code] = _error_response(code, responses[code])
            op["responses"] = dict(sorted(responses.items()))
            login = op.get("operationId", "").startswith("login_session")
            op["security"] = [] if health or login else [{SECURITY_SCHEME: []}, {COOKIE_SCHEME: []}]
    return schema


def install_openapi(app: FastAPI) -> None:
    generate = app.openapi

    def custom() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = polish(generate())
        return app.openapi_schema

    app.openapi = custom  # type: ignore[method-assign]

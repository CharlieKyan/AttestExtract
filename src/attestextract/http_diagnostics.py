"""Bounded, allowlisted HTTP error details; never retain a raw response body."""

from __future__ import annotations

from collections.abc import Iterable
import math
import re
from typing import Any

import httpx

from .diagnostics import redact_text


def describe_http_error(
    response: httpx.Response, *, chunk_id: str, attempt: int,
    secrets: Iterable[str] = (), compatibility_hint: str | None = None,
) -> dict[str, Any]:
    """Keep what the server reported, without asserting an internal root cause."""
    secrets = tuple({part for value in secrets if isinstance(value, str)
                     for part in (value, value.strip()) if part})
    truncated_fields: list[str] = []

    def clean(value: Any, name: str, limit: int) -> str | int | float | None:
        if type(value) is int:
            return value if value.bit_length() <= 63 else None
        if type(value) is float:
            return value if math.isfinite(value) else None
        if not isinstance(value, str) or not value.strip():
            return None
        # Redact before truncating: a key crossing a length limit must not leak.
        text = redact_text(value, secrets)
        text = re.sub(r"https?:(?:\\*/){2}[^\s<>\"']+", "[URL REDACTED]", text, flags=re.IGNORECASE)
        text = re.sub(r"\bBearer\s+[^\s,;\"'<>]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
        text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        if not text:
            return None
        if len(text) > limit:
            truncated_fields.append(name)
            text = text[:limit] + "…"
        return text

    request_ids = {}
    for header in ("x-request-id", "request-id", "x-correlation-id", "x-goog-request-id"):
        value = clean(response.headers.get(header), f"request_ids.{header}", 256)
        if value is not None:
            request_ids[header] = value

    provider_error = {}
    if len(response.content) > 65536:
        body_format = "too_large"
        truncated_fields.append("response_body_not_inspected")
    else:
        try:
            body = response.json()
            body_format = "json"
        except (ValueError, UnicodeError, RecursionError):
            body = None
            body_format = "invalid_json" if "json" in response.headers.get("content-type", "").lower() else "non_json"
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, str):
                error = {"message": error}
            elif not isinstance(error, dict):
                error = {}
            for field in ("code", "type", "message", "status", "param"):
                value = clean(error.get(field), f"provider_error.{field}", 1000 if field == "message" else 256)
                if value is not None:
                    provider_error[field] = value
            if "message" not in provider_error:
                value = clean(body.get("detail"), "provider_error.message", 1000)
                if value is not None:
                    provider_error["message"] = value

    return {
        "schema_version": "0.1", "layer": "http", "chunk_id": chunk_id,
        "attempt": attempt, "status_code": response.status_code, "body_format": body_format,
        "provider_error": provider_error, "request_ids": request_ids,
        "diagnostic_status": "provider_detail_available" if provider_error else "provider_detail_unavailable",
        "compatibility_hint": compatibility_hint, "root_cause_verified": False,
        "truncated": bool(truncated_fields), "truncated_fields": truncated_fields,
        "notice": "Provider-reported details are not proof of an internal root cause; no raw body or full headers are saved.",
    }

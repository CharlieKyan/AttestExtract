"""Observe completion compliance without claiming server-side enforcement."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .json_validation import JSONValidationError, load_json
from .validation import details, issue


_KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "items", "anyOf", "description"}
_TYPES = {"object", "array", "string", "boolean", "null", "integer", "number"}


def schema_request_info(schema: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    encoded = json.dumps(schema, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {
        "schema_version": "0.1", "requested_mode": mode,
        "schema_sent": mode == "json_schema" and schema is not None,
        "schema_sha256": hashlib.sha256(encoded).hexdigest() if schema is not None else None,
        "server_enforcement": "not_verified",
    }


def _schema_keys(schema: dict[str, Any]) -> set[str]:
    """Reject unsupported constraints instead of reporting incomplete checks as passed."""
    if set(schema) - _KEYWORDS:
        raise ValueError("unsupported schema keyword")
    kinds = schema.get("type", [])
    if not set(kinds if isinstance(kinds, list) else [kinds]) <= _TYPES:
        raise ValueError("unsupported schema type")
    names = set(schema.get("properties", {}))
    children = list(schema.get("properties", {}).values()) + schema.get("anyOf", [])
    if "items" in schema:
        children.append(schema["items"])
    if schema.get("additionalProperties") not in (None, True, False):
        raise ValueError("unsupported additionalProperties schema")
    for child in children:
        names.update(_schema_keys(child))
    return names


def _matches_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return type(value) is int or (type(value) is float and value.is_integer())
    if kind == "number":
        return type(value) in (int, float)
    return type(value) is {"object": dict, "array": list, "string": str,
                           "boolean": bool, "null": type(None)}.get(kind)


def _check(value: Any, schema: dict[str, Any], path: tuple = ()) -> list[dict[str, Any]]:
    errors = []
    if "anyOf" in schema:
        branches = [_check(value, branch, path) for branch in schema["anyOf"]]
        if not any(not branch for branch in branches):
            # Use the closest branch for useful paths without repeating every alternative.
            errors.extend(min(branches, key=len) if branches else [issue(
                path, "any_of", "one declared schema branch", value, "No schema branch matched.",
            )])
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_matches_type(value, kind) for kind in kinds):
            return errors + [issue(path, "type", " or ".join(kinds), value,
                                   "The output type differs from the requested schema.")]
    if "enum" in schema and not any(
        value == item and (type(value) is type(item) or
                          (type(value) in (int, float) and type(item) in (int, float)))
        for item in schema["enum"]
    ):
        errors.append(issue(path, "enum", "declared enum value", value,
                            "The value is outside the declared enum."))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(issue((*path, name), "required", "required property", None,
                                    "The requested schema requires this property."))
        for name, child in value.items():
            if name in properties:
                errors.extend(_check(child, properties[name], (*path, name)))
            elif schema.get("additionalProperties") is False:
                errors.append(issue((*path, "<undeclared>"), "additional_property", "declared properties only",
                                    child, "An undeclared property is present; its name is withheld."))
    elif isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            errors.extend(_check(child, schema["items"], (*path, index)))
    return errors


def assess_schema_compliance(text: str, schema: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """Check the generated schema subset against raw text, without coercion or repair.

    This diagnostic does not replace candidate validation or trigger retries.
    A compliant response alone cannot prove that a gateway enforced its schema.
    """
    result = schema_request_info(schema, mode)
    if schema is None:
        return {**result, "status": "not_evaluated", "reason": "no_output_schema"}
    try:
        allowed_keys = _schema_keys(schema)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return {**result, "status": "not_evaluated", "reason": "unsupported_schema"}
    try:
        payload = load_json(text, allowed_keys=allowed_keys)
    except JSONValidationError as exc:
        return {**result, "status": "failed", **exc.diagnostic}
    except (ValueError, TypeError) as exc:
        extra = {"line": exc.lineno, "column": exc.colno} if isinstance(exc, json.JSONDecodeError) else {}
        errors = [issue((), "invalid_json", "raw JSON without Markdown fences", text,
                        "The raw completion is not valid JSON.", **extra)]
        return {**result, "status": "failed", **details("json_syntax", errors)}
    try:
        errors = _check(payload, schema)
    except (TypeError, AttributeError, RecursionError):
        return {**result, "status": "not_evaluated", "reason": "unsupported_schema"}
    return {**result, "status": "failed" if errors else "passed", **details("requested_schema", errors)}

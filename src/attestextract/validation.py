"""Bounded response diagnostics containing paths and types, never input values."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError


ERROR_LIMIT = 50
_MISSING = object()


def actual_type(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    return {str: "string", bool: "boolean", int: "integer", float: "number",
            dict: "object", list: "array"}.get(type(value), type(value).__name__)


def error_path(location: tuple | list) -> str:
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            name = str(part)[:128]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                path += ("." if path else "") + name
            else:
                path += "[" + json.dumps(name, ensure_ascii=False) + "]"
    return path or "$"


def issue(location: tuple | list, code: str, expected: str, value: Any,
          message: str, **extra: Any) -> dict[str, Any]:
    return {"path": error_path(location), "code": code, "expected": expected,
            "actual_type": actual_type(value), "message": message, **extra}


def details(phase: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"phase": phase, "error_count": len(errors), "error_limit": ERROR_LIMIT,
            "truncated": len(errors) > ERROR_LIMIT, "errors": errors[:ERROR_LIMIT]}


def contract_details(error: ValidationError, payload: Any) -> dict[str, Any]:
    expected_types = {
        "model_type": "object with value and quote (for a task field)",
        "model_attributes_type": "object", "dict_type": "object", "list_type": "array",
        "string_type": "string", "int_type": "integer", "float_type": "number",
        "bool_type": "boolean", "missing": "required property",
        "extra_forbidden": "declared properties only", "finite_number": "finite number",
    }
    errors = []
    for entry in error.errors(include_input=False, include_url=False):
        location = entry["loc"]
        if (len(location) >= 2 and location[-2] == "value"
                and location[-1] in {"str", "int", "float", "bool"}):
            # These labels describe Pydantic union branches, not input keys.
            location = location[:-1]
        value = payload
        for part in location:
            if isinstance(value, dict):
                value = value.get(part, _MISSING)
            elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
                value = value[part]
            else:
                # Pydantic union branch names are not input object properties.
                break
        code = entry["type"]
        expected = expected_types.get(code, "valid value for the response contract")
        if code == "model_type":
            class_name = entry.get("ctx", {}).get("class_name")
            expected = {
                "_RawResponse": "object with a records array",
                "_RawRecord": "record object with record_type and fields",
                "_RawField": "field object with value and quote",
            }.get(class_name, "object")
        errors.append(issue(location, code, expected, value,
                            f"Expected {expected}; received {actual_type(value)}."))
    return details("records_contract", errors)

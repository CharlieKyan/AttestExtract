"""Strict JSON decoding with duplicate and non-finite number locations."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from .validation import details, issue


class JSONValidationError(ValueError):
    """A decoded JSON tree violates the extraction format contract."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        super().__init__("JSON contains duplicate keys, non-finite numbers, or excessive nesting.")
        self.diagnostic = diagnostic


class _ObjectPairs(list):
    """Distinguish JSON objects from arrays without losing repeated keys."""


def load_json(text: str, *, allowed_keys: set[str] | None = None) -> Any:
    """Decode without silently choosing among repeated object members.

    Syntax errors retain the standard decoder's line and column information.
    Semantic diagnostics contain paths and types, never input values; unknown
    property names are masked because model-generated keys can contain input.
    """
    visible_keys = {"records", "record_type", "fields", "value", "quote"} | (allowed_keys or set())
    errors = []

    def visit(value: Any, path: tuple) -> Any:
        if isinstance(value, _ObjectPairs):
            result = {}
            occurrences = Counter(key for key, _ in value)
            for member_index, (key, child) in enumerate(value):
                location = (*path, key if key in visible_keys else "<undeclared>")
                converted = visit(child, location)
                if key in result:
                    errors.append(issue(
                        location, "duplicate_key", "unique keys within each JSON object", converted,
                        "The same key occurs more than once in this object; no value was selected.",
                        member_index=member_index, occurrences=occurrences[key],
                    ))
                else:
                    result[key] = converted
            return result
        if isinstance(value, list):
            return [visit(child, (*path, index)) for index, child in enumerate(value)]
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(issue(
                path, "non_finite_number", "finite JSON number", value,
                "The number is non-finite or exceeds the supported finite numeric range.",
            ))
        return value

    try:
        # The standard decoder handles JSON grammar. Walking its pair tree
        # adds paths before converting objects into dictionaries.
        payload = json.loads(text, object_pairs_hook=_ObjectPairs)
        result = visit(payload, ())
    except RecursionError:
        raise JSONValidationError(details("json_syntax", [issue(
            (), "excessive_nesting", "JSON within the supported nesting depth", None,
            "JSON nesting exceeds the supported parser depth.",
        )])) from None
    if errors:
        raise JSONValidationError(details("json_syntax", errors))
    return result

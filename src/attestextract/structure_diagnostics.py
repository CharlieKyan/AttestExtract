"""Bounded, value-free hints for a small class of malformed JSON structures.

This scanner never repairs JSON or supplies records. A normal JSON decoder is
still authoritative: callers use its error offset to request an optional hint.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .validation import error_path


_MAX_CHARS = 131_072
_MAX_DEPTH = 32
_MAX_TOKENS = 8192
_SPACE = " \t\r\n"
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


@dataclass
class _Frame:
    kind: str
    path: tuple
    state: str
    key: str | None = None
    previous_key: str | None = None
    index: int = 0


def _safe_key(value: str, allowed_keys: set[str]) -> str:
    if value in allowed_keys and len(value) <= 64 and value.isprintable():
        return value
    return "<undeclared>"


def _value_done(frame: _Frame) -> None:
    if frame.kind == "object":
        frame.previous_key, frame.key = frame.key, None
        frame.state = "comma_or_end"
    elif frame.kind == "array":
        frame.index += 1
        frame.state = "comma_or_end"
    else:
        frame.state = "done"


def _object_hint(text: str, offset: int, frame: _Frame, allowed_keys: set[str]) -> dict | None:
    cursor = offset + 1
    while cursor < len(text) and text[cursor] in _SPACE:
        cursor += 1
    if cursor >= len(text) or text[cursor] != '"':
        return None
    try:
        key, cursor = json.decoder.scanstring(text, cursor + 1)
    except (ValueError, UnicodeError):
        return None
    while cursor < len(text) and text[cursor] in _SPACE:
        cursor += 1
    if cursor >= len(text) or text[cursor] != ":":
        return None
    next_key = _safe_key(key, allowed_keys)
    quoted = json.dumps(next_key, ensure_ascii=False)
    prefix = ""
    if frame.previous_key is not None:
        prefix = json.dumps(frame.previous_key, ensure_ascii=False) + ": <value>, "
    path = error_path(frame.path)
    return {
        "code": "unexpected_object_in_object",
        "path": path if len(path) <= 256 else "$",
        "actual_token": "{",
        "previous_key": frame.previous_key,
        "next_key": next_key,
        "wrong_shape": "{" + prefix + "{" + quoted + ": <value>}}",
        "correct_shape": "{" + prefix + quoted + ": <value>}",
        "message": (
            "An object expects a quoted property name here, not an anonymous object. "
            "Keep named properties as siblings in the same object. "
            "Shapes use placeholders, not extracted values; regenerate from the original source."
        ),
    }


def diagnose_structure(text: str, error_offset: int, *, allowed_keys: set[str]) -> dict | None:
    """Explain an anonymous object where a property name was expected.

    Offsets are zero-based Unicode positions in the exact string passed to the
    JSON decoder (the caller removes any permitted outer fence). Only grammar
    before that offset is scanned. Unknown keys and all values are withheld;
    malformed earlier syntax, ambiguous shapes or resource limits return None.
    """
    if (not isinstance(text, str) or len(text) > _MAX_CHARS or type(error_offset) is not int
            or not 0 <= error_offset < len(text) or text[error_offset] != "{"):
        return None
    stack = [_Frame("root", (), "value")]
    cursor, tokens = 0, 0
    while cursor <= error_offset:
        while cursor < len(text) and text[cursor] in _SPACE:
            cursor += 1
        if cursor > error_offset or tokens >= _MAX_TOKENS:
            return None
        frame = stack[-1]
        if cursor == error_offset:
            if frame.kind == "object" and frame.state in {"key", "key_or_end"}:
                return _object_hint(text, cursor, frame, allowed_keys)
            return None
        char = text[cursor]
        tokens += 1
        if frame.kind == "object" and frame.state in {"key", "key_or_end"}:
            if char == "}" and frame.state == "key_or_end":
                stack.pop()
                _value_done(stack[-1])
                cursor += 1
                continue
            if char != '"':
                return None
            try:
                key, cursor = json.decoder.scanstring(text, cursor + 1)
            except (ValueError, UnicodeError):
                return None
            frame.key = _safe_key(key, allowed_keys)
            frame.state = "colon"
            continue
        if frame.state == "colon":
            if char != ":":
                return None
            frame.state = "value"
            cursor += 1
            continue
        if frame.state == "comma_or_end":
            if char == ",":
                frame.state = "key" if frame.kind == "object" else "value"
            elif char == ("}" if frame.kind == "object" else "]"):
                stack.pop()
                _value_done(stack[-1])
            else:
                return None
            cursor += 1
            continue
        if frame.kind == "array" and frame.state == "value_or_end" and char == "]":
            stack.pop()
            _value_done(stack[-1])
            cursor += 1
            continue
        if frame.state not in {"value", "value_or_end"}:
            return None
        if char in "{[":
            if len(stack) > _MAX_DEPTH:
                return None
            path = frame.path
            if frame.kind == "object":
                path = (*path, frame.key)
            elif frame.kind == "array":
                path = (*path, frame.index)
            stack.append(_Frame("object", path, "key_or_end") if char == "{"
                         else _Frame("array", path, "value_or_end"))
            cursor += 1
            continue
        if char == '"':
            try:
                _, cursor = json.decoder.scanstring(text, cursor + 1)
            except (ValueError, UnicodeError):
                return None
        elif text.startswith("true", cursor) or text.startswith("null", cursor):
            cursor += 4
        elif text.startswith("false", cursor):
            cursor += 5
        else:
            number = _NUMBER.match(text, cursor)
            if number is None:
                return None
            cursor = number.end()
        _value_done(frame)
    return None

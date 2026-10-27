"""Read an explicitly selected dotenv file as data, without shell execution.

This deliberately small syntax supports one KEY=value assignment per line,
an optional ``export`` prefix, quotes, and comments. Values are never expanded:
``$NAME``, ``${NAME}``, and command-substitution text remain literal strings.
PowerShell assignments, interactive key prompts, and Markdown links are rejected
with line-number diagnostics that do not reveal keys or values.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
from typing import NoReturn

from .errors import ConfigurationError


_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MARKDOWN_LINK = re.compile(r"\[[^\]\r\n]*\]\([^\)\r\n]+\)")
_POWERSHELL_ENV = re.compile(r"\$env:", re.IGNORECASE)
_READ_HOST = re.compile(r"\bRead-Host\b", re.IGNORECASE)


def _invalid(line_number: int, reason: str) -> NoReturn:
    raise ConfigurationError(f"Invalid .env file at line {line_number}: {reason}")


def _value(text: str, line_number: int) -> str:
    """Decode one same-line quoted value or an unquoted literal."""
    text = text.strip()
    if not text:
        return ""
    if text[0] not in {"'", '"'}:
        # A # inside a token (for example, an API key) is literal. A comment
        # starts only at the beginning of the value or after whitespace.
        comment = re.search(r"(?:^|\s)#", text)
        return text[:comment.start()].rstrip() if comment else text

    quote = text[0]
    characters: list[str] = []
    index = 1
    while index < len(text):
        character = text[index]
        if character == quote:
            remainder = text[index + 1:].strip()
            if remainder and not remainder.startswith("#"):
                _invalid(line_number, "unexpected text after the closing quote.")
            return "".join(characters)
        if character == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in {quote, "\\"}:
                characters.append(following)
                index += 2
                continue
        characters.append(character)
        index += 1
    _invalid(line_number, "a quoted value must close on the same line.")


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a UTF-8 dotenv file, allowing an optional UTF-8 BOM.

    Duplicate names and malformed assignments fail explicitly. Within quotes,
    escaped matching quotes and escaped backslashes are decoded; other
    backslashes are literal. Multiline values and variable expansion are not
    supported. Error messages contain no path, key, or value from the file.
    """
    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeError, TypeError, ValueError):
        raise ConfigurationError("Could not read the selected .env file as UTF-8.") from None

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\x00" in line:
            _invalid(line_number, "NUL characters are not allowed.")
        if _POWERSHELL_ENV.match(line):
            _invalid(line_number, "use KEY=value without the PowerShell $env: prefix.")
        if line.startswith("```"):
            _invalid(line_number, "use plain KEY=value lines without Markdown code fences.")
        if line.startswith("export") and len(line) > 6 and line[6].isspace():
            line = line[6:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or _KEY.fullmatch(key) is None:
            _invalid(line_number, "expected a name containing letters, digits, or underscores followed by =.")
        if key in values:
            _invalid(line_number, "duplicate variable names are not allowed.")
        value = _value(raw_value, line_number)
        if _POWERSHELL_ENV.search(value):
            _invalid(line_number, "use a literal value instead of a PowerShell $env: reference.")
        if _READ_HOST.search(value):
            _invalid(line_number, "Read-Host is a PowerShell command; enter the actual value as data.")
        if _MARKDOWN_LINK.search(value):
            _invalid(line_number, "use the plain value or URL instead of a Markdown link.")
        values[key] = value
    return values


def load_env_file(
    path: str | Path, *, environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return file defaults merged with an existing environment snapshot.

    Existing keys win even when their value is empty. The supplied mapping
    and ``os.environ`` are never modified, so loading a file does not affect
    subsequent commands in the same Python process. No file is auto-discovered.
    """
    inherited = os.environ if environ is None else environ
    merged = parse_env_file(path)
    merged.update(inherited)
    return merged

"""Optional cues for numeric table cells. Depend only on notation, never on field names."""

import re


# Use familiar SI symbols only as positive layout cues. Other units and expressions
# can always be named literally by the user or selected by the table reader.
_SYMBOL = r"(?:[YZEPTGMkhdcmµμunpfazy]?(?:mol|cd|Hz|Pa|Wb|rad|sr|lm|lx|g|m|s|A|K|N|J|W|C|V|F|Ω|S|T|H)|[%‰]|°[CF]?)"
_POWER = r"(?:\^[+−-]?\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)?"
_UNIT = rf"{_SYMBOL}{_POWER}(?:\s*[/·⋅]\s*{_SYMBOL}{_POWER})*"
_QUANTITY = re.compile(rf"\s*[<>≤≥≈~]?\s*[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?\s*(?:{_UNIT})?\s*")


def numeric_cell(text):
    """An intervening quantity is compatible with a continuous row or column of values."""
    return bool(_QUANTITY.fullmatch(text) or re.fullmatch(r"[\d\s.,%±+−<>~–—-]+", text))


def header_labels(quote):
    """Keep the entire heading first; split only a complete, recognizable unit suffix."""
    yield quote.strip()
    match = re.fullmatch(rf"(.+?)(?:\s*[/／]\s*{_UNIT}|\s*[(（]\s*{_UNIT}\s*[)）]|\s*\[\s*{_UNIT}\s*\])\s*", quote)
    if match:
        yield match[1].strip()

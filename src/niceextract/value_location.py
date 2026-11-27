"""Locate a reported value in one cited source span, without changing its spelling or scale."""

import re

from .evidence import is_missing


_NUMBER = r"[+−-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+−-]?[0-9]+)?"
_UNIT = r"(?:[%‰°µμA-Za-z]+(?:\^[+−-]?\d+)?(?:[/·][µμA-Za-z]+(?:\^[+−-]?\d+)?)*)"
_VALUE = rf"{_NUMBER}(?:{_UNIT})?(?:(?:~|–|—|-|±){_NUMBER}(?:{_UNIT})?)?"
_QUANTITY = re.compile(rf"(?:{_VALUE}|\({_VALUE}\))(?:×10\^[+−-]?\d+)?(?:{_UNIT})?")
_MATH = re.compile(r"(?<![\\$])(?P<delimiter>\$\$|\$)(?!\$)(?P<body>(?:\\[^\r\n]|[^\\$\r\n])+)\1(?!\$)"
                   r"|\\\((?P<inline>[^$\r\n]*?)\\\)|\\\[(?P<display>[^$\r\n]*?)\\\]")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def _math_spelling(text):
    """Compare only entire scalar/range expressions. Unsupported TeX stays opaque."""
    if len(text) > 512:
        return None
    text = re.sub(r"\\times\b", "×", text)
    text = re.sub(r"\\mu\b", "μ", text).replace(r"\%", "%")
    text = re.sub(r"\\mathrm\s*\{\s*([A-Za-z]+)\s*\}", r"\1", text)
    text = re.sub(r"\{\s*(×|[μµ](?:\s*[A-Za-z]+)?)\s*\}", r"\1", text)
    text = re.sub(r"\^\s*\{\s*([+−-]?\s*\d+)\s*\}", r"^\1", text)
    text = re.sub(r"[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m[0].translate(_SUPERSCRIPTS), text)
    # TeX unescaped ~ before a unit is a space. Between endpoints it stays a range marker.
    text = re.sub(r"(?<!\\)~(?=\s*[μµA-Za-z%°])", " ", text).replace(r"\~", "~")
    if re.search(r"\d\s+\d", text):
        # Only handle clearly typeset decimal digits and the base of an explicit power of ten.
        text = re.sub(r"(?<![\d.])(?:\d[ \t]+)*\d[ \t]*\.[ \t]*\d(?:[ \t]+\d)*(?![\d.])",
                      lambda m: re.sub(r"\s+", "", m[0]), text)
        text = re.sub(r"×\s*1\s+0\s*\^", "×10^", text)
        if re.search(r"\d\s+\d", text):
            return None
    if re.search(r"[A-Za-z]\s+[A-Za-z]", text):
        return None
    spelling = re.sub(r"\s+", "", text).replace("−", "-").replace("µ", "μ")
    return spelling if _QUANTITY.fullmatch(spelling) else None


def _literal_pattern(value):
    if not re.search(r"\d", value):
        return re.escape(value)
    # Only presentation escapes for literal percent signs and range markers may vary.
    return "".join(r"\\?" + re.escape(p[-1]) if p in {"%", "~", r"\%", r"\~"} else re.escape(p)
                   for p in re.split(r"(\\?[%~])", value))


def _numeric_boundary(source, start, end, value):
    measured = re.match(r"\(?[+−-]?(?:\d|\.\d)", value)
    after = source[end:end + 12]
    if measured and re.match(r"\s*(?:×|\\times)\s*1\s*0\s*\^", after):
        return False
    if measured and re.search(r"[A-Za-zµμ%°‰]$", value) and re.match(r"[A-Za-zµμ/·^⁰¹²³⁴⁵⁶⁷⁸⁹]", after):
        return False
    if re.match(r"[+−-]?(?:\d|\.\d)", value):
        before = source[max(0, start - 2):start]
        if re.search(r"[A-Za-z_\d.^+−-]\Z|\d,\Z", before):
            return False
        if re.search(r"[\^_]\s*\{\s*$", source[max(0, start - 12):start]):
            return False
    if value[-1].isdigit() and re.match(r"\d|[.,]\d|[eE][+−-]?\d|\^", source[end:end + 4]):
        return False
    return True


def value_occurrences(source, value, start, end, *, unit=None):
    """Find all matching original Unicode spans, retaining repeated measurements."""
    if (is_missing(value) or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source)):
        return []
    value = str(value).strip()
    if not value:
        return []
    text = source[start:end]
    maths = list(_MATH.finditer(text)) if re.search(r"\d", value) else []
    spans = set()
    for match in re.finditer(_literal_pattern(value), text):
        # A proper substring of a formula may be an exponent, endpoint, or multiplier.
        if any(m.start() <= match.start() and match.end() <= m.end() and match.span() != m.span()
               for m in maths):
            continue
        left, right = start + match.start(), start + match.end()
        if _numeric_boundary(source, left, right, value):
            spans.add((left, right))
    spelling = _math_spelling(value) if maths else None
    with_unit = _math_spelling(value + " " + unit) if spelling and unit else None
    if spelling:
        for match in maths:
            group = match.lastgroup
            original = match[group]
            # A single named equality can provide a measured right-hand side.
            # Products, sums and expressions with competing assignments stay opaque.
            offset = start + match.start(group)
            assignment = re.match(r"\s*(?:[A-Za-z][A-Za-z0-9]*|\\[A-Za-z]+)\s*=\s*", original)
            if assignment:
                offset += assignment.end()
                original = original[assignment.end():]
            complete = _math_spelling(original)
            if complete == spelling or (with_unit and complete == with_unit):
                spans.add((offset, offset + len(original)) if assignment else
                          (start + match.start(), start + match.end()))
            elif re.fullmatch(_NUMBER, value) and complete and re.fullmatch(re.escape(spelling) + _UNIT, complete):
                # Keep the scalar's own source span in an explicitly unit-bearing quantity.
                # A number from a multiplier, range or identifier cannot pass this check.
                run = re.search(re.escape(value), original)
                if run:
                    spans.add((offset + run.start(), offset + run.end()))
                else:
                    spans.add((offset, offset + len(original)))
    return [{"start": left, "end": right, "quote": source[left:right]} for left, right in sorted(spans)]


def locate_value(source, value, start, end):
    """Return the unique original Unicode span inside a validated citation, or None."""
    matches = value_occurrences(source, value, start, end)
    return matches[0] if len(matches) == 1 else None


def conflicting_coded_owner(source, fields, chunk):
    """Flag a sibling identifier explicitly heading the value statement; never reassign ownership."""
    owner, value = fields.get("entity"), fields.get("value")
    if not owner or not value or not isinstance(owner.value, str) or not re.search(r"\d", str(value.value)):
        return None
    code = re.search(r"(?<![A-Za-z0-9_])(?P<prefix>[A-Za-z]+[-_]?)\d+[A-Za-z]?$", owner.value.strip())
    if not code or owner.evidence.status != "matched" or code.group() not in owner.evidence.quote:
        return None
    evidence = value.evidence
    spans = evidence.matches if evidence.status == "matched" else []
    results = [match for span in spans for match in value_occurrences(source, value.value, span.start, span.end)]
    if len(results) != 1:
        return None
    start, end = results[0]["start"], results[0]["end"]
    visible = next((s for s in chunk.segments if s.start <= start and end <= s.end), None)
    if visible is None:
        return None
    # Use the original sentence even if the value quote contains only the number. Periods
    # between digits are part of values. A fragment beginning mid-sentence has no reliable subject.
    boundary = re.compile(r"[。！？!?\r\n]|\.(?!\d)")
    left = max((m.end() for m in boundary.finditer(source, visible.start, start)), default=visible.start)
    if left == visible.start and left and not boundary.fullmatch(source[left - 1:left]):
        return None
    stop = boundary.search(source, end, visible.end)
    sentence = source[left:stop.start() if stop else visible.end]
    family = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(code["prefix"]) + r"\d+[A-Za-z]?(?![A-Za-z0-9_])")
    labels = list(family.finditer(sentence))
    if len(labels) != 1 or labels[0].group() == code.group() or left + labels[0].end() > start:
        return None
    lead = sentence[:labels[0].start()].strip().casefold()
    noun = owner.value.strip()[:code.start()].strip().casefold()
    if lead not in {"", noun, "the", "the " + noun}:
        return None
    # A coordinated list with omitted subjects is not an incompatible singular reading.
    if re.search(r"[,，、;；]|\b(?:and|or|respectively)\b|和|与|分别", sentence[labels[0].end():start-left], re.I):
        return None
    return labels[0].group()

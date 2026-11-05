"""Conservative, position-preserving syntax checks for measurement expressions.

These helpers recognize literal structure, not scientific meaning or unit
equivalence. Unknown syntax is left for review; no source text is normalized.
"""

from __future__ import annotations

import re

from .models import EvidenceRef, RangeBinding, SourceSpan


_NUMBER = r"[+−-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+−-]?[0-9]+)?"
_SEPARATOR = r"\\~|~|–|—|-|至"
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_.])[A-Z][A-Z0-9_]*(?![A-Za-z0-9_])")
_ATOM = r"(?:\\(?:mathrm|mathbf|mathit|mathsf|mathtt)\s*\{[A-Za-z\s~]+\}|[A-Za-z]+)"
_SCRIPT = r"(?:\s*[_^]\s*(?:\{\s*[0-9]+\s*[+-]?\s*\}|[0-9](?![0-9])))"
_SCRIPTED_IDENTIFIER = re.compile(_ATOM + _SCRIPT + "+")


def numeric_tokens(text: str) -> list[tuple[int, int]]:
    """Find numeric tokens except digits provably attached to identifiers.

In particular, x_2=8 retains 8, and malformed math/OCR is not discarded as a
whole. Scientific notation is one numeric token; formatting commands alone
never authorize suppressing numbers elsewhere in a mathematical expression.
    """
    ignored = [match.span() for pattern in (_IDENTIFIER, _SCRIPTED_IDENTIFIER)
               for match in pattern.finditer(text)]
    matches = [match.span() for match in re.finditer(_NUMBER, text)]
    # Unsupported numeric glyphs remain visible to review, rather than becoming
    # silently harmless because the simple-range grammar only accepts ASCII.
    matches.extend((index, index + 1) for index, character in enumerate(text)
                   if character.isnumeric() and not any(start <= index < end for start, end in matches))
    return sorted((left, right) for left, right in matches
                  if not any(start <= left and right <= end for start, end in ignored))


def _evidence(text: str, start: int, end: int) -> EvidenceRef:
    return EvidenceRef(quote=text[start:end], status="matched", start=start, end=end,
                       matches=[SourceSpan(start=start, end=end)])


def _range_match(expression: str, unit: str):
    literal = re.escape(unit) if unit else r"(?!)"
    return re.fullmatch(
        rf"\s*(?P<left>{_NUMBER})\s*(?P<left_unit>{literal})?\s*"
        rf"(?P<separator>{_SEPARATOR})\s*(?P<right>{_NUMBER})\s*"
        rf"(?P<right_unit>{literal})?\s*", expression,
    )


def bind_range(text: str, context: EvidenceRef, value: EvidenceRef, unit: EvidenceRef,
               *, table_header: bool = False) -> tuple[RangeBinding | None, bool]:
    """Return an exact unit binding and whether the value is a simple range.

The separate boolean permits a dimensionless/simple range without fabricating
unit evidence. All bound in-context unit occurrences must already satisfy the
same token-boundary checks as ordinary units.
    """
    if context.status != "matched" or value.status != "matched":
        return None, False
    match = _range_match(value.quote, unit.quote)
    if match is None:
        return None, False
    start = value.start
    endpoints = [_evidence(text, start + match.start(name), start + match.end(name))
                 for name in ("left", "right")]
    separator = _evidence(text, start + match.start("separator"), start + match.end("separator"))
    unit_spans = [(start + match.start(name), start + match.end(name))
                  for name in ("left_unit", "right_unit") if match.group(name) is not None]
    known = {(span.start, span.end) for span in unit.matches}
    if table_header:
        if unit_spans or unit.status != "matched":
            return None, True
        return RangeBinding(endpoints=endpoints, separator=separator,
                            units=[unit.model_copy(deep=True)], unit_layout="table_header"), True
    if not unit_spans and unit.status == "matched" and unit.start >= value.end:
        # A shared unit may immediately follow value_text, but never come from
        # a later condition or another measurement.
        if not text[value.end:unit.start].strip():
            unit_spans = [(unit.start, unit.end)]
    if not unit_spans:
        return None, True
    if not all(span in known for span in unit_spans):
        return None, False
    # A unit only on the left endpoint does not establish a shared trailing unit.
    if len(unit_spans) == 1 and match.group("left_unit") is not None:
        return None, False
    if set(unit_spans) != known:
        return None, True
    units = [_evidence(text, left, right) for left, right in unit_spans]
    return RangeBinding(endpoints=endpoints, separator=separator, units=units,
                        unit_layout="per_endpoint" if len(units) == 2 else "shared"), True

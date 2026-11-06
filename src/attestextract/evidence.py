"""Deterministic source checks; none of these checks establish semantic truth."""

from __future__ import annotations

from itertools import combinations
import json
import re
import unicodedata
from typing import TYPE_CHECKING

from .models import (
    Candidate, CandidateLink, DocumentChunk, EvidenceMatch, EvidenceRef, FieldValue,
    MeasurementEvidence, MeasurementInput, MeasurementSpec, Scalar, SourceSpan, TaskSpec,
    TableReference,
)
from .measurement_syntax import bind_range, numeric_tokens
from .normalization import latex_spans, normalized_view

if TYPE_CHECKING:
    from .structure import DocumentStructure


def _with_table_refs(evidence: EvidenceRef, structure: DocumentStructure | None) -> EvidenceRef:
    if structure is None:
        return evidence
    spans = evidence.matches + [match.span for match in evidence.source_matches]
    for table in structure.tables:
        for cell in table.cells:
            if any(cell.span.start < span.end and span.start < cell.span.end for span in spans):
                role = ("header" if cell.row in table.header_rows else
                        "row_header" if cell.column in table.row_header_columns else
                        "header" if cell.is_header else "data")
                evidence.table_refs.append(TableReference(
                    table_id=table.table_id, cell_id=cell.cell_id,
                    row=cell.row, column=cell.column, rowspan=cell.rowspan, colspan=cell.colspan,
                    role=role,
                ))
    return evidence


def ground_quote(text: str, chunk: DocumentChunk, quote: str, *, normalize: bool = False,
                 structure: DocumentStructure | None = None) -> EvidenceRef:
    if not quote.strip():
        return EvidenceRef(quote=quote, status="missing")
    # Enriched chunks may split overlapping passages for text reconstruction.
    # Search the full passages actually displayed, without joining scopes.
    scopes = (chunk.body_segments + [item.span for item in chunk.context_segments]
              if chunk.context_policy is not None else chunk.segments)
    found: set[tuple[int, int]] = set()
    for segment in scopes:
        start = segment.start
        while (index := text.find(quote, start, segment.end)) >= 0:
            found.add((index, index + len(quote)))
            start = index + 1
    spans = [SourceSpan(start=start, end=end) for start, end in sorted(found)]
    if len(spans) == 1:
        return _with_table_refs(EvidenceRef(quote=quote, status="matched", start=spans[0].start,
                                           end=spans[0].end, matches=spans), structure)
    if spans or not normalize:
        return _with_table_refs(EvidenceRef(quote=quote, status="ambiguous" if spans else "unmatched",
                                           matches=spans), structure)
    query = normalized_view(quote)
    source_matches = {}
    math_spans = latex_spans(text)
    for segment in scopes:
        source_segment = text[segment.start:segment.end]
        view = normalized_view(source_segment, offset=segment.start)
        cursor = 0
        while (index := view.text.find(query.text, cursor)) >= 0:
            start, end = view.origins[index][0], view.origins[index + len(query.text) - 1][1]
            source_quote = text[start:end]
            rules = sorted(set(query.rules) | set(normalized_view(source_quote).rules))
            math_unchanged = all(start <= left and right <= end and text[left:right] in quote
                                 for left, right in math_spans if left < end and start < right)
            if rules and math_unchanged:
                source_matches[(start, end)] = EvidenceMatch(
                    source_quote=source_quote, span=SourceSpan(start=start, end=end), rules=rules,
                )
            cursor = index + 1
    matches = [source_matches[key] for key in sorted(source_matches)]
    evidence = EvidenceRef(quote=quote, status="normalized" if len(matches) == 1 else
                           "ambiguous" if matches else "unmatched", source_matches=matches)
    if len(matches) == 1:
        evidence.start, evidence.end = matches[0].span.start, matches[0].span.end
    return _with_table_refs(evidence, structure)


def is_missing(value: Scalar) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def rule_matches(rule: dict[str, list[Scalar]], fields: dict[str, FieldValue]) -> bool:
    """Rules are task-declared exact values, not keyword guesses or inference."""
    return bool(rule) and all(
        name in fields and not is_missing(fields[name].value)
        and any(type(fields[name].value) is type(value) and fields[name].value == value for value in values)
        for name, values in rule.items()
    )


def _unit_letter(character: str) -> bool:
    # Latin/Greek unit prefixes continue a token; adjacent Chinese prose does not.
    return bool(character) and unicodedata.name(character, "").startswith(("LATIN", "GREEK"))


def _token(text: str, context: EvidenceRef, token: str, *, unit: bool = False,
           structure: DocumentStructure | None = None) -> EvidenceRef:
    if context.status != "matched":
        return EvidenceRef(quote=token, status="missing" if not token else "unmatched")
    chunk = DocumentChunk(chunk_id="scope", text=context.quote,
                          segments=[SourceSpan(start=context.start, end=context.end)])
    located = ground_quote(text, chunk, token)
    valid = []
    for span in located.matches:
        # ASCII identifiers and larger decimal tokens are not standalone measurements.
        before = text[span.start - 1] if span.start > 0 else ""
        after = text[span.end] if span.end < len(text) else ""
        left_boundary = r"[A-Za-z_]" if unit else r"[A-Za-z0-9_]"
        right_boundary = r"[A-Za-z0-9_]" if unit else r"[0-9_]"
        if (before and re.fullmatch(left_boundary, before)) or (after and re.fullmatch(right_boundary, after)):
            continue
        if not unit and ((before == "." and span.start > 1 and text[span.start - 2].isdigit())
                         or (after == "." and span.end + 1 < len(text) and text[span.end + 1].isdigit())):
            continue
        if not unit and before in {"-", "+", "−"} and token[:1].isdigit():
            continue
        if unit and (before in {"°", "/", "^", "·", "⋅", "µ", "Ω"} or _unit_letter(before)
                     or after.isnumeric() or _unit_letter(after) or after in {"µ", "Ω"}
                     or re.match(r"\s*[/^·⋅⁻⁺]", text[span.end:context.end])):
            continue
        if unit and any(context.start + match.start() <= span.start < context.start + match.end()
                        for match in re.finditer(r"\\[A-Za-z]+", context.quote)):
            continue
        valid.append(span)
    if len(valid) == 1:
        return _with_table_refs(EvidenceRef(quote=token, status="matched", start=valid[0].start,
                                           end=valid[0].end, matches=valid), structure)
    return _with_table_refs(EvidenceRef(quote=token, status="missing" if not token else
                                       "ambiguous" if valid else "unmatched", matches=valid), structure)


def _legacy_table_column(text: str, span: EvidenceRef) -> tuple[int, int, int] | None:
    """Preserve pre-0.4 archive revalidation; new extraction uses structure."""
    if span.status != "matched":
        return None
    row = text.rfind("\n", 0, span.start) + 1
    end = text.find("\n", span.end)
    end = len(text) if end < 0 else end
    line = text[row:end].rstrip("\r")
    if not line.strip().startswith("|") or "\n" in text[span.start:span.end]:
        return None
    pipes = [row + match.start() for match in re.finditer(r"\|", line)]
    column = next((i for i, (left, right) in enumerate(zip(pipes, pipes[1:]))
                   if left < span.start and span.end <= right), None)
    if column is None:
        return None
    top = row
    while top > 0:
        previous = text.rfind("\n", 0, top - 1) + 1
        if not text[previous:top].strip().startswith("|"):
            break
        top = previous
    header_end = text.find("\n", top)
    separator_end = text.find("\n", header_end + 1)
    separator = text[header_end + 1:separator_end if separator_end >= 0 else len(text)]
    cells = separator.strip().strip("|").split("|")
    if not cells or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells):
        return None
    return top, row, column


def _table_value_scope(structure: DocumentStructure, value: EvidenceRef,
                        unit: EvidenceRef) -> SourceSpan | None:
    if value.status != "matched" or unit.status != "matched":
        return None
    scopes = []
    for table in structure.tables:
        data = [cell for cell in table.cells
                if cell.span.start <= value.start < value.end <= cell.span.end]
        headers = [cell for cell in table.cells
                   if cell.span.start <= unit.start < unit.end <= cell.span.end
                   and cell.row in table.header_rows]
        for cell in data:
            for header in headers:
                if (cell.row not in table.header_rows and cell.column not in table.row_header_columns
                        and not cell.is_header and header.row + header.rowspan <= cell.row
                        and header.column <= cell.column
                        and cell.column + cell.colspan <= header.column + header.colspan):
                    scopes.append(cell.span)
    return scopes[0] if len(scopes) == 1 else None


def check_measurement(text: str, chunk: DocumentChunk, raw: MeasurementInput,
                      spec: MeasurementSpec, fields: dict[str, FieldValue], *,
                      schema_version: str = "0.4", normalize: bool = False,
                      structure: DocumentStructure | None = None) -> MeasurementEvidence:
    if schema_version == "0.4" and structure is None and raw.unit_quote:
        from .structure import build_structure
        structure = build_structure(text)
    if schema_version != "0.4":
        normalize, structure = False, None
    context = ground_quote(text, chunk, raw.context_quote, normalize=normalize, structure=structure)
    value = _token(text, context, raw.value_text, structure=structure)
    unit_context = (ground_quote(text, chunk, raw.unit_quote, normalize=normalize, structure=structure)
                    if raw.unit_quote else context)
    unit = _token(text, unit_context, raw.unit_text, unit=True, structure=structure)
    issues = []
    if context.status != "matched":
        issues.append("measurement_context_unresolved")
    value_field = fields.get(spec.value_field)
    unit_field = fields.get(spec.unit_field)
    if value_field is not None and value_field.evidence.quote != raw.context_quote:
        issues.append("measurement_field_context_mismatch")
    if unit_field is not None and not is_missing(unit_field.value) and unit_field.evidence.quote != (raw.unit_quote or raw.context_quote):
        if "measurement_field_context_mismatch" not in issues:
            issues.append("measurement_field_context_mismatch")
    if value.status != "matched":
        issues.append("measurement_value_unresolved")
    if value_field is None or is_missing(value_field.value) or str(value_field.value) != raw.value_text:
        issues.append("measurement_value_mismatch")
    if unit_field is not None and not is_missing(unit_field.value):
        if unit.status != "matched":
            issues.append("measurement_unit_unresolved")
        if str(unit_field.value) != raw.unit_text:
            issues.append("measurement_unit_mismatch")
    elif raw.unit_text or raw.unit_quote:
        issues.append("measurement_unit_mismatch")
    scope_start, scope_end = context.start, context.end
    if raw.unit_quote:
        scope = None
        if schema_version == "0.4":
            scope = _table_value_scope(structure, value, unit)
        else:
            row = _legacy_table_column(text, value)
            header = _legacy_table_column(text, unit)
            if (row and header and row[0] == header[0] and header[0] == header[1]
                    and row[1] > header[1] and row[2] == header[2]):
                end = text.find("\n", row[1])
                pipes = [row[1] + match.start() for match in re.finditer(r"\|", text[row[1]:end if end >= 0 else len(text)])]
                scope = SourceSpan(start=pipes[row[2]] + 1, end=pipes[row[2] + 1])
        if scope is None:
            issues.append("measurement_header_mismatch")
            unit = EvidenceRef(quote=raw.unit_text, status="unmatched")
        else:
            scope_start, scope_end = scope.start, scope.end
    binding = None
    if schema_version in {"0.3", "0.4"} and context.status == value.status == "matched":
        binding, simple_range = bind_range(text, context, value, unit,
                                          table_header=bool(raw.unit_quote))
        if binding is not None and "measurement_header_mismatch" not in issues:
            issues = [code for code in issues if code != "measurement_unit_unresolved"]
        else:
            binding = None
        # Do not hide several quantities merely because the entire value string
        # was located. Unit exponents are not additional measured quantities.
        value_without_units = "".join(
            " " if any(span.start <= index < span.end for span in unit.matches) else text[index]
            for index in range(value.start, value.end)
        )
        if len(numeric_tokens(value_without_units)) > 1 and not simple_range:
            issues.append("measurement_value_structure_unresolved")
    if context.status == value.status == "matched":
        excluded = [value] + ([unit] if not raw.unit_quote and unit.status == "matched" else [])
        # Keep removed positions as spaces in the new checker: concatenating
        # either side can manufacture an identifier that was never in the source.
        if schema_version in {"0.3", "0.4"}:
            remainder = "".join(" " if any(item.start <= index < item.end for item in excluded)
                                else text[index] for index in range(scope_start, scope_end))
            extra_numbers = bool(numeric_tokens(remainder))
        else:
            remainder = "".join(text[index] for index in range(scope_start, scope_end)
                                if not any(item.start <= index < item.end for item in excluded))
            extra_numbers = bool(re.search(r"\d", remainder))
        if extra_numbers:
            issues.append("measurement_multiple_values")
    return MeasurementEvidence(context=context, value=value, unit=unit,
                               unit_context=unit_context if raw.unit_quote else None,
                               unit_source="table_header" if raw.unit_quote else "context",
                               condition_fields=spec.condition_fields, issues=issues,
                               range_binding=binding)


def link_candidates(candidates: list[Candidate], task: TaskSpec) -> list[CandidateLink]:
    """Offer review associations without deleting observations or resolving conflicts."""
    buckets: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        spec = task.record_types[candidate.record_type]
        if not spec.comparison_fields or any(name not in candidate.fields or is_missing(candidate.fields[name].value)
                                             for name in spec.comparison_fields):
            continue
        key = json.dumps([candidate.record_type, [candidate.fields[name].value for name in spec.comparison_fields]],
                         ensure_ascii=False, allow_nan=False)
        buckets.setdefault(key, []).append(candidate)
    links = []
    for group in buckets.values():
        spec = task.record_types[group[0].record_type]
        conditions = set(spec.condition_fields)
        conditions.update(name for measurement in spec.measurement_groups.values() for name in measurement.condition_fields)
        for left, right in combinations(group, 2):
            def scalar(candidate: Candidate, name: str) -> Scalar:
                return candidate.fields[name].value if name in candidate.fields else None
            if any(scalar(left, name) is not None and scalar(right, name) is not None
                   and scalar(left, name) != scalar(right, name) for name in conditions):
                continue
            compared = set(spec.identity_fields) | conditions
            unresolved = sorted(name for name in compared | set(spec.comparison_fields)
                                if is_missing(scalar(left, name)) or is_missing(scalar(right, name))
                                or left.fields[name].evidence.status != "matched" or right.fields[name].evidence.status != "matched")
            differing = sorted(name for name in spec.identity_fields
                               if scalar(left, name) is not None and scalar(right, name) is not None
                               and scalar(left, name) != scalar(right, name))
            # Equal comparison values do not establish a factual conflict when
            # identity differs or the evidence/conditions cannot be confirmed.
            kind = "insufficient_context" if unresolved or differing else "possible_repeat"
            links.append(CandidateLink(kind=kind, candidate_ids=[left.candidate_id, right.candidate_id],
                                       compared_fields=sorted(set(spec.comparison_fields) | compared),
                                       differing_fields=differing, unresolved_fields=unresolved))
    return links

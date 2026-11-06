"""Independent synthetic source expectations for conservative D4 retrieval."""

import hashlib
import json

import pytest

from attestextract import extract
from attestextract.archive import CandidateReview, ExtractionArchive, create_archive
from attestextract.backends import ReplayBackend
from attestextract.documents import Document
from attestextract.evidence import check_measurement, ground_quote
from attestextract.identity import grounded_candidate_id
from attestextract.models import (
    ContextOptions, ContextPassage, ContextSegment, DocumentChunk, FieldValue, MeasurementInput, MeasurementSpec, SourceSpan,
)
from attestextract.normalization import normalized_view
from attestextract.reporting import render_markdown_report
from attestextract.structure import build_structure


def chunk(source):
    return DocumentChunk(chunk_id="synthetic", text=source, segments=[SourceSpan(start=0, end=len(source))])


def locate(source, quote, **options):
    return ground_quote(source, chunk(source), quote, normalize=True, **options)


def test_normalized_match_keeps_model_quote_and_original_unicode_slice():
    source = "🙂 前置\r\n样品：12\t  m。"
    quote = "样品:12 m."
    evidence = locate(source, quote)
    assert evidence.status == "normalized"
    assert evidence.quote == quote
    assert evidence.matches == []
    assert (evidence.start, evidence.end) == (6, len(source))
    match = evidence.source_matches[0]
    assert match.source_quote == "样品：12\t  m。"
    assert source[match.span.start:match.span.end] == match.source_quote
    assert match.rules == ["punctuation", "whitespace"]


def test_exact_occurrence_wins_over_other_normalized_occurrences():
    evidence = locate("A: 12 m.\nA： 12 m。", "A: 12 m.")
    assert evidence.status == "matched"
    assert (evidence.start, evidence.end) == (0, 8)
    assert evidence.source_matches == []


def test_repeated_normalized_locations_remain_ambiguous_without_exact_matches():
    evidence = locate("A：12 m。\nA:12\t m.", "A:12 m.")
    assert evidence.status == "ambiguous"
    assert evidence.start is None and evidence.end is None
    assert evidence.matches == []
    assert [(m.span.start, m.span.end) for m in evidence.source_matches] == [(0, 7), (8, 16)]


@pytest.mark.parametrize("source,quote", [
    ("value −12 m", "value -12 m"), ("value １２ m", "value 12 m"),
    ("value 1O m", "value 10 m"), ("value 12 m²", "value 12 m2"),
    ("value 12\u200bm", "value 12m"),
    (r"$7\times10^{3}$", "7000"),
    (r"$7\times10^{3}$", r"$7 \times 10^3$"),
    ("$x  + y$", "$x + y$"), ("$x + y$", "x  + y"),
    (r"\mathrm{A  B}", r"\mathrm{A B}"),
])
def test_normalization_does_not_guess_scientific_or_formula_equivalence(source, quote):
    assert locate(source, quote).status == "unmatched"


def test_formula_is_exact_while_surrounding_prose_can_be_normalized():
    source = "结果： $x  + y$。"
    evidence = locate(source, "结果: $x  + y$.")
    assert evidence.status == "normalized"
    assert evidence.source_matches[0].source_quote == source
    assert normalized_view("$x  + y$").text == "$x  + y$"
    assert locate(source, "$x  + y$").status == "matched"


def test_normalized_search_stays_inside_the_provided_source_segments():
    source = "A：12 m。\nB: 13 m."
    scoped = DocumentChunk(chunk_id="B", text=source[8:], segments=[SourceSpan(start=8, end=len(source))])
    assert ground_quote(source, scoped, "A:12 m.", normalize=True).status == "unmatched"


def test_partial_formula_segment_cannot_bypass_formula_preservation():
    source = "$x  + y$"
    scoped = DocumentChunk(chunk_id="fragment", text=source[1:-1],
                           segments=[SourceSpan(start=1, end=len(source) - 1)])
    assert ground_quote(source, scoped, "x + y", normalize=True).status == "unmatched"


def test_overlapping_heading_and_body_keep_full_search_passages_and_deduplicate():
    source = "# Alpha：12\nBody"
    scoped = DocumentChunk(
        chunk_id="overlap", text=source,
        segments=[SourceSpan(start=0, end=8), SourceSpan(start=8, end=11), SourceSpan(start=11, end=15)],
        body_segments=[SourceSpan(start=8, end=15)],
        context_segments=[ContextSegment(role="heading", span=SourceSpan(start=0, end=11))],
        context_policy=ContextOptions(mode="structure"),
    )
    full = ground_quote(source, scoped, "# Alpha：12")
    assert full.status == "matched"
    assert (full.start, full.end) == (0, 10)
    normalized = ground_quote(source, scoped, "# Alpha:12", normalize=True)
    assert normalized.status == "normalized"
    assert normalized.source_matches[0].source_quote == "# Alpha：12"
    shared = ground_quote(source, scoped, "12")
    assert shared.status == "matched"
    assert [(span.start, span.end) for span in shared.matches] == [(8, 10)]


@pytest.mark.parametrize("gap", ["", " hidden "])
def test_separate_displayed_passages_are_not_joined_for_grounding(gap):
    source = "A" + gap + "B"
    scoped = DocumentChunk(
        chunk_id="separate", text="AB",
        segments=[SourceSpan(start=0, end=1), SourceSpan(start=len(source) - 1, end=len(source))],
        body_segments=[SourceSpan(start=len(source) - 1, end=len(source))],
        context_segments=[ContextSegment(role="heading", span=SourceSpan(start=0, end=1))],
        context_policy=ContextOptions(mode="structure"),
    )
    assert ground_quote(source, scoped, "AB", normalize=True).status == "unmatched"


def measurement(source, context, unit_quote="", *, structure=None, value="12", unit="m", normalize=False):
    scope = chunk(source)
    fields = {
        "value": FieldValue(value=value, evidence=ground_quote(source, scope, context)),
        "unit": FieldValue(value=unit, evidence=ground_quote(source, scope, unit_quote or context)),
    }
    raw = MeasurementInput(context_quote=context, value_text=value, unit_text=unit, unit_quote=unit_quote)
    return check_measurement(source, scope, raw, MeasurementSpec(value_field="value", unit_field="unit"),
                             fields, structure=structure, normalize=normalize)


def test_normalized_measurement_context_never_passes_exact_token_checks():
    result = measurement("length：12 m。", "length:12 m.", normalize=True)
    assert result.context.status == "normalized"
    assert result.value.status == result.unit.status == "unmatched"
    assert "measurement_context_unresolved" in result.issues
    assert result.range_binding is None


@pytest.mark.parametrize("outer", [False, True])
def test_markdown_table_binding_uses_real_cells_with_escaped_pipes(outer):
    header = r"Sample \| ID | Length (m) | Temperature (K)"
    separator = "--- | --- | ---"
    row = r"A \| B | 12 | 300"
    if outer:
        header, separator, row = ["| " + line + " |" for line in (header, separator, row)]
    source = "\n".join((header, separator, row))
    result = measurement(source, row, header, structure=build_structure(source))
    assert result.issues == []
    assert result.value.table_refs[0].column == 1
    assert result.unit.table_refs[0].column == 1
    assert result.unit.table_refs[0].role == "header"
    wrong = measurement(source, row, header, structure=build_structure(source), unit="K")
    assert "measurement_header_mismatch" in wrong.issues
    assert wrong.unit.status == "unmatched"


def test_html_table_with_merged_header_binds_only_covered_columns():
    source = ('<table><tr><th>Sample</th><th colspan="2">Length (m)</th><th>Pressure (Pa)</th></tr>'
              '<tr><td>A</td><td>12</td><td>14</td><td>90</td></tr></table>')
    result = measurement(source, "12", "Length (m)", structure=build_structure(source))
    assert result.issues == []
    assert result.value.table_refs[0].column == 1
    assert result.unit.table_refs[0].colspan == 2
    wrong = measurement(source, "12", "Pressure (Pa)", unit="Pa", structure=build_structure(source))
    assert "measurement_header_mismatch" in wrong.issues


def test_html_td_header_requires_an_explicit_structure_hint():
    source = '<table><tr><td>Sample</td><td>Length (m)</td></tr><tr><td>A</td><td>12</td></tr></table>'
    unknown = build_structure(source)
    result = measurement(source, "12", "Length (m)", structure=unknown)
    assert "measurement_header_mismatch" in result.issues
    explicit = build_structure(source, hints={"tables": {unknown.tables[0].table_id: {
        "header_rows": [0], "row_header_columns": [0]}}})
    result = measurement(source, "12", "Length (m)", structure=explicit)
    assert result.issues == []
    row_label = locate(source, "A", structure=explicit)
    assert row_label.table_refs[0].role == "row_header"
    assert locate(source, "Length (m)", structure=explicit).table_refs[0].role == "header"


def test_single_unspecified_th_is_not_assumed_to_be_a_column_header():
    source = '<table><tr><th>Length (m)</th><td>note</td></tr><tr><td>12</td><td>result</td></tr></table>'
    result = measurement(source, "12", "Length (m)", structure=build_structure(source))
    assert "measurement_header_mismatch" in result.issues


def test_whole_row_evidence_lists_cells_but_tokens_bind_to_single_cells():
    source = "| Sample | Length (m) |\n| --- | --- |\n| A | 12 |\n"
    structure = build_structure(source)
    evidence = locate(source, "| A | 12 |", structure=structure)
    assert [(reference.row, reference.column) for reference in evidence.table_refs] == [(1, 0), (1, 1)]
    result = measurement(source, "| A | 12 |", "| Sample | Length (m) |", structure=structure)
    assert [(reference.row, reference.column) for reference in result.value.table_refs] == [(1, 1)]


def normalized_archive():
    document = Document.from_text("Value：12 m。")
    task = {"name": "Synthetic", "record_types": {"item": {"fields": {"value": {"type": "string"}}}}}
    response = {"records": [{"record_type": "item", "values": {"value": "12 m"},
                              "quotes": {"value": "Value:12 m."}}]}
    result = extract(document, task, backend=ReplayBackend([response]))
    return document, result, create_archive(document, result)


def test_normalized_archive_roundtrip_reports_source_and_cannot_be_accepted():
    document, result, archive = normalized_archive()
    evidence = result.candidates[0].fields["value"].evidence
    assert evidence.status == "normalized"
    assert result.candidates[0].needs_review
    assert ExtractionArchive.model_validate_json(archive.model_dump_json()) == archive
    report = render_markdown_report(result)
    assert "Value：12 m。" in report
    assert "规范化检索仅提供待复核位置" in report
    review = CandidateReview(candidate_id=result.candidates[0].candidate_id, status="accepted",
                             origin="synthetic_fixture", reviewer="fixture", rationale="synthetic check")
    with pytest.raises(ValueError, match="Accepted candidates"):
        create_archive(document, result, [review])


@pytest.mark.parametrize("mutation", ["offset", "quote", "rules", "status"])
def test_archive_rejects_tampered_normalized_evidence(mutation):
    _, _, archive = normalized_archive()
    data = archive.model_dump()
    evidence = data["extraction"]["candidates"][0]["fields"]["value"]["evidence"]
    if mutation == "offset":
        evidence["source_matches"][0]["span"]["start"] = 1
    elif mutation == "quote":
        evidence["source_matches"][0]["source_quote"] = "forged"
    elif mutation == "rules":
        evidence["source_matches"][0]["rules"] = ["whitespace"]
    else:
        evidence["status"] = "matched"
    with pytest.raises(ValueError):
        ExtractionArchive.model_validate(data)


def test_old_identity_hash_omits_new_evidence_fields():
    source = "value 12"
    evidence = ground_quote(source, chunk(source), "12")
    field = FieldValue(value="12", evidence=evidence)
    digest = hashlib.sha256(source.encode()).hexdigest()
    historic = {"source": digest, "record_type": "item", "fields": {"value": {
        "value": "12", "evidence": {"quote": "12", "status": "matched", "start": 6, "end": 8,
                                      "matches": [{"start": 6, "end": 8}]}}}}
    expected = "candidate-" + hashlib.sha256(json.dumps(historic, sort_keys=True, ensure_ascii=False,
                                                         allow_nan=False).encode()).hexdigest()[:24]
    for version in ("0.1", "0.2", "0.3"):
        assert grounded_candidate_id(digest, "item", {"value": field}, schema_version=version) == expected
    assert grounded_candidate_id(digest, "item", {"value": field}, schema_version="0.4") != expected


def test_table_archive_preserves_hints_and_rejects_forged_cell_identity():
    source = '<table><tr><td>Sample</td><td>Length (m)</td></tr><tr><td>A</td><td>12</td></tr></table>'
    structure = build_structure(source, hints={"tables": {"table-0001": {
        "header_rows": [0], "row_header_columns": [0]}}})
    document = Document.from_text(source, structure=structure)
    task = {"name": "Table", "record_types": {"item": {
        "fields": {"value": {"type": "string"}, "unit": {"type": "string"}},
        "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}}}}}
    raw = {"records": [{"record_type": "item", "values": {"value": "12", "unit": "m"},
                         "quotes": {"value": "12", "unit": "Length (m)"},
                         "measurements": {"reading": {"context_quote": "12", "value_text": "12",
                                                       "unit_text": "m", "unit_quote": "Length (m)"}}}]}
    result = extract(document, task, backend=ReplayBackend([raw]))
    archive = create_archive(document, result)
    assert ExtractionArchive.model_validate_json(archive.model_dump_json()) == archive
    payload = archive.model_dump()
    reference = payload["extraction"]["candidates"][0]["measurements"]["reading"]["value"]["table_refs"][0]
    reference["column"] = 0
    with pytest.raises(ValueError, match="Table references"):
        ExtractionArchive.model_validate(payload)


def test_default_table_inline_unit_archive_reuses_inferred_structure():
    document = Document.from_text("| Sample | Reading |\n| --- | --- |\n| A | 12 m |\n")
    assert document.structure is None
    task = {"name": "Inline table unit", "record_types": {"item": {
        "fields": {"value": {"type": "string"}, "unit": {"type": "string"}},
        "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}}}}}
    raw = {"records": [{"record_type": "item", "values": {"value": "12", "unit": "m"},
                         "quotes": {"value": "12 m", "unit": "12 m"},
                         "measurements": {"reading": {"context_quote": "12 m", "value_text": "12",
                                                       "unit_text": "m", "unit_quote": ""}}}]}
    result = extract(document, task, backend=ReplayBackend([raw]))
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.issues == [] and measurement.value.table_refs and measurement.unit.table_refs
    archive = create_archive(document, result)
    assert ExtractionArchive.model_validate_json(archive.model_dump_json()) == archive


def test_schema_03_archive_keeps_its_pre_extension_candidate_id():
    document = Document.from_text("value 12")
    task = {"name": "Old synthetic", "record_types": {"item": {"fields": {"value": {"type": "string"}}}}}
    raw = {"records": [{"record_type": "item", "values": {"value": "12"}, "quotes": {"value": "12"}}]}
    result = extract(document, task, backend=ReplayBackend([raw]))
    historic = {"source": document.text_sha256, "record_type": "item", "fields": {"value": {
        "value": "12", "evidence": {"quote": "12", "status": "matched", "start": 6, "end": 8,
                                      "matches": [{"start": 6, "end": 8}]}}}}
    expected = "candidate-" + hashlib.sha256(json.dumps(historic, sort_keys=True, ensure_ascii=False,
                                                         allow_nan=False).encode()).hexdigest()[:24]
    result.schema_version = "0.3"
    result.candidates[0].candidate_id = expected
    archive = create_archive(document, result, [CandidateReview(
        candidate_id=expected, status="accepted", reviewer="fixture", origin="synthetic_fixture",
        rationale="Synthetic exact source")])
    payload = archive.model_dump()
    for field in payload["extraction"]["candidates"][0]["fields"].values():
        field["evidence"].pop("source_matches")
        field["evidence"].pop("table_refs")
    for field in payload["records"][0]["fields"].values():
        field["evidence"].pop("source_matches")
        field["evidence"].pop("table_refs")
    loaded = ExtractionArchive.model_validate(payload)
    assert loaded.extraction.candidates[0].candidate_id == expected
    assert loaded.reviews[0].status == "accepted"


def test_source_context_is_exact_reported_metadata_without_changing_candidate_identity():
    source = "# Sample A\n\nValue 12"
    document = Document.from_text(source)
    task = {"name": "Context", "record_types": {"item": {"fields": {"value": {"type": "string"}}}}}
    raw = {"records": [{"record_type": "item", "values": {"value": "12"}, "quotes": {"value": "12"}}]}
    result = extract(document, task, backend=ReplayBackend([raw]))
    candidate = result.candidates[0]
    before = candidate.candidate_id
    candidate.source_context = [ContextPassage(role="heading", span=SourceSpan(start=0, end=10),
                                                quote="# Sample A")]
    archive = create_archive(document, result)
    assert archive.extraction.candidates[0].candidate_id == before
    assert ExtractionArchive.model_validate_json(archive.model_dump_json()) == archive
    report = render_markdown_report(result)
    assert "实际提供的上下文" in report and "章节标题" in report
    assert "\\# Sample A" in report and "\\[0, 10\\)" in report
    bad = archive.model_dump()
    bad["extraction"]["candidates"][0]["source_context"][0]["quote"] = "invented heading"
    with pytest.raises(ValueError, match="exact original source passage"):
        ExtractionArchive.model_validate(bad)
    bad = archive.model_dump()
    bad["extraction"]["schema_version"] = "0.3"
    with pytest.raises(ValueError, match="source context requires"):
        ExtractionArchive.model_validate(bad)

"""Synthetic range and numeric-label regressions; no private paper fixtures."""

import hashlib
import json

import pytest

from attestextract import extract
from attestextract.archive import CandidateReview, ExtractionArchive, create_archive
from attestextract.backends import ReplayBackend
from attestextract.documents import Document
from attestextract.evidence import check_measurement, ground_quote
from attestextract.models import DocumentChunk, FieldValue, MeasurementInput, MeasurementSpec, SourceSpan


def evaluate(context, value, unit="%", *, prefix="🙂 ", suffix="", header="", schema_version="0.3"):
    source = header + prefix + context + suffix
    chunk = DocumentChunk(chunk_id="test", text=source, segments=[SourceSpan(start=0, end=len(source))])
    fields = {
        "quantity": FieldValue(value=value, evidence=ground_quote(source, chunk, context)),
        "unit": FieldValue(value=unit, evidence=ground_quote(source, chunk, header or context)),
    }
    raw = MeasurementInput(context_quote=context, value_text=value, unit_text=unit, unit_quote=header)
    spec = MeasurementSpec(value_field="quantity", unit_field="unit")
    return source, check_measurement(source, chunk, raw, spec, fields, schema_version=schema_version)


@pytest.mark.parametrize("expression,left,right,unit", [
    (r"11%\~24%", "11", "24", "%"),
    ("11–24%", "11", "24", "%"),
    ("11–24 %", "11", "24", "%"),
    ("-2.5–3.0 m", "-2.5", "3.0", "m"),
    ("-2--1 m", "-2", "-1", "m"),
    ("1e-3 m至2E+4 m", "1e-3", "2E+4", "m"),
    ("1.2 g/cm^3 — 2.7 g/cm^3", "1.2", "2.7", "g/cm^3"),
    ("1 % ~ 1 %", "1", "1", "%"),
])
def test_ranges_bind_exact_endpoints_and_every_unit(expression, left, right, unit):
    source, measurement = evaluate("reading " + expression, expression, unit)
    binding = measurement.range_binding
    assert binding is not None
    assert [item.quote for item in binding.endpoints] == [left, right]
    assert not measurement.issues
    for ref in [*binding.endpoints, binding.separator, *binding.units]:
        assert ref.status == "matched"
        assert source[ref.start:ref.end] == ref.quote
        assert [(span.start, span.end) for span in ref.matches] == [(ref.start, ref.end)]
    assert measurement.value.quote == expression
    assert measurement.semantic_status == "not_verified"
    if len(binding.units) == 2:
        assert binding.unit_layout == "per_endpoint"
        assert measurement.unit.status == "ambiguous"
        assert binding.units[0].end <= binding.endpoints[1].start
    else:
        assert binding.unit_layout == "shared"


def test_fixed_unicode_coordinates_are_not_byte_positions():
    source, measurement = evaluate("X 11%–24%", "11%–24%")
    binding = measurement.range_binding
    assert [(ref.start, ref.end) for ref in binding.endpoints] == [(4, 6), (8, 10)]
    assert [(ref.start, ref.end) for ref in binding.units] == [(6, 7), (10, 11)]
    assert source == "🙂 X 11%–24%"


def test_shared_unit_immediately_after_value_is_bound():
    _, measurement = evaluate("length 11–24 m", "11–24", "m")
    assert not measurement.issues
    assert measurement.range_binding.unit_layout == "shared"


def test_same_column_header_can_supply_shared_range_unit():
    header = "| sample | length (m) | temperature (K) |\n| --- | --- | --- |\n"
    row = "| A | 11–24 | 300 |"
    _, measurement = evaluate(row, "11–24", "m", prefix="", header=header)
    assert measurement.range_binding.unit_layout == "table_header"
    assert not measurement.issues


@pytest.mark.parametrize("expression,unit", [
    ("2 m–4 cm", "m"),
    ("11%,24%", "%"),
    (r"1 . 2 3 % \~ 4 . 5 6 %", "%"),
    ("2m–4m²", "m"),
    ("2 m–4", "m"),
    ("2 m–4 m and 6 m", "m"),
])
def test_unrecognized_multi_number_values_are_reviewed(expression, unit):
    _, measurement = evaluate("reading " + expression, expression, unit)
    assert measurement.range_binding is None
    assert "measurement_value_structure_unresolved" in measurement.issues
    assert measurement.value.quote == expression


@pytest.mark.parametrize("label", ["CO2", "X2Y3", r"$\mathrm { X } _ { 2 }$", r"$x_2$",
                                   r"$\mathrm { X } _ { 2 } \mathrm { Y } _ { 3 }$"])
def test_identifier_digits_are_not_an_extra_measurement(label):
    _, measurement = evaluate(label + " content 11%–24%", "11%–24%")
    assert not measurement.issues


@pytest.mark.parametrize("label", [r"$x_2=8$", r"$\mathrm { X } _ { 2 } 0$", "count 4",
                                   "dose 1e3", r"$\frac{1}{2}$", "count ٨", "depth3200m", "pressure80"])
def test_independent_or_uncertain_numbers_in_math_remain_reviewed(label):
    _, measurement = evaluate(label + " content 11%–24%", "11%–24%")
    assert measurement.range_binding is not None
    assert measurement.issues == ["measurement_multiple_values"]


def test_repeated_context_does_not_get_an_arbitrary_range_binding():
    context = "content 11%–24%"
    _, measurement = evaluate(context, "11%–24%", suffix=". " + context)
    assert measurement.context.status == "ambiguous"
    assert measurement.range_binding is None
    assert "measurement_context_unresolved" in measurement.issues


def test_second_measurement_and_wrong_header_are_not_hidden():
    _, measurement = evaluate("length 11–24 m; depth 3200 m", "11–24", "m")
    assert measurement.range_binding is None
    assert "measurement_unit_unresolved" in measurement.issues
    assert "measurement_multiple_values" in measurement.issues
    header = "| sample | length (m) | pressure (bar) |\n| --- | --- | --- |\n"
    _, measurement = evaluate("| A | 11–24 | 5 |", "11–24", "bar", prefix="", header=header)
    assert measurement.range_binding is None
    assert "measurement_header_mismatch" in measurement.issues


def test_historical_checker_retains_its_original_conservative_issues():
    _, old = evaluate(r"$X_{2}$ reading 11%–24%", "11%–24%", schema_version="0.2")
    assert old.range_binding is None
    assert old.issues == ["measurement_unit_unresolved", "measurement_multiple_values"]


def archive_example(context="X2 content 11%–24%", quantity="11%–24%"):
    document = Document.from_text(context)
    task = {"name": "Synthetic range", "record_types": {"reading": {
        "fields": {"quantity": {"type": "string", "required": True}, "unit": {"type": "string"}},
        "measurement_groups": {"range": {"value_field": "quantity", "unit_field": "unit"}},
    }}}
    payload = {"records": [{"record_type": "reading", "values": {"quantity": quantity, "unit": "%"},
                            "quotes": {"quantity": context, "unit": context},
                            "measurements": {"range": {"context_quote": context, "value_text": quantity,
                                                        "unit_text": "%", "unit_quote": ""}}}]}
    return document, extract(document, task, backend=ReplayBackend([payload]))


def test_new_archive_roundtrip_does_not_auto_accept():
    document, result = archive_example()
    archive = create_archive(document, result)
    assert archive.records == []
    assert archive.reviews[0].status == "unreviewed"
    assert ExtractionArchive.model_validate_json(archive.model_dump_json()) == archive


@pytest.mark.parametrize("other_issue", [False, True])
@pytest.mark.parametrize("mutation", ["endpoint", "unit", "layout", "separator"])
def test_binding_tampering_is_rejected_even_with_other_review_issues(other_issue, mutation):
    document, result = archive_example("X2 content 11%–24%" + ("; count 8" if other_issue else ""))
    measurement = result.candidates[0].measurements["range"]
    binding = measurement.range_binding
    if mutation == "endpoint":
        binding.endpoints.reverse()
    elif mutation == "unit":
        binding.units = [binding.units[1], binding.units[1]]
    elif mutation == "layout":
        binding.unit_layout = "shared"
    else:
        binding.separator = binding.units[0]
    with pytest.raises(ValueError, match="mechanical checks"):
        create_archive(document, result)


def test_old_accepted_archive_keeps_id_and_historical_checks():
    document, result = archive_example("content 11%", "11")
    candidate = result.candidates[0]
    # Build the old serialized identity independently; absence of new metadata
    # must not be interpreted as a different observation on archive loading.
    old_measurement = candidate.measurements["range"].model_dump(exclude={"range_binding"})
    identity = {"source": document.text_sha256, "record_type": "reading",
                "fields": {name: field.model_dump() for name, field in candidate.fields.items()},
                "measurements": {"range": old_measurement}}
    old_id = "candidate-" + hashlib.sha256(json.dumps(identity, sort_keys=True,
                          ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:24]
    result.schema_version = "0.2"
    candidate.candidate_id = old_id
    archive = create_archive(document, result, reviews=[CandidateReview(
        candidate_id=old_id, status="accepted", reviewer="Synthetic reviewer",
        rationale="Checked synthetic source", origin="synthetic_fixture")])
    payload = archive.model_dump(mode="json")
    payload["extraction"]["candidates"][0]["measurements"]["range"].pop("range_binding")
    loaded = ExtractionArchive.model_validate(payload)
    assert loaded.extraction.candidates[0].candidate_id == old_id
    assert loaded.reviews[0].status == "accepted"
    assert len(loaded.records) == 1

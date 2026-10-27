"""D1 contracts preserve exact source text and explicit human review provenance."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from attestextract import extract
from attestextract.archive import CandidateReview, ExtractionArchive, create_archive
from attestextract.backends import ReplayBackend
from attestextract.documents import Document, DocumentMetadata
from attestextract.storage import load_archive, save_archive


TEXT = "前言🙂\r\n\r\n样品甲在 20 ℃ 下容量为 12.5 mmol/g。\r\n"
TASK = {
    "name": "Synthetic conditional observation",
    "record_types": {
        "observation": {
            "fields": {
                "subject": {"type": "string", "required": True},
                "value": {"type": "number", "required": True},
                "condition": {"type": "string"},
            },
            "identity_fields": ["subject"],
            "condition_fields": ["condition"],
        },
    },
}


def make_result(*, text=TEXT, changes=None):
    """Produce one candidate without relying on network or research materials."""
    fields = {
        "subject": {"value": "样品甲", "quote": "样品甲"},
        "value": {"value": 12.5, "quote": "12.5"},
        "condition": {"value": "20 ℃", "quote": "20 ℃"},
    }
    if changes:
        changes(fields)
    document = Document.from_text(text, source_name="synthetic.txt")
    result = extract(
        document, TASK,
        backend=ReplayBackend([{"records": [{"record_type": "observation", "fields": fields}]}]),
    )
    return document, result


def review(candidate_id, status="accepted", **kwargs):
    return CandidateReview(
        candidate_id=candidate_id,
        status=status,
        reviewer="synthetic fixture author",
        rationale="Hand-checked against the synthetic source.",
        origin="synthetic_fixture",
        **kwargs,
    )


def accepted_archive():
    document, result = make_result()
    return create_archive(document, result, reviews=[review(result.candidates[0].candidate_id)])


def test_document_preserves_unicode_newlines_and_stable_text_identity():
    document = Document.from_text(TEXT, source_name="synthetic.txt")
    again = Document.from_text(TEXT, source_name="synthetic.txt")
    assert document.text == TEXT
    assert document.text_sha256 == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert document.document_id == again.document_id
    assert document.representation == "text/plain"
    assert document.representation_version == "1"
    assert Document.from_text(TEXT + " ").document_id != document.document_id
    assert Document.model_validate_json(document.model_dump_json()) == document


def test_document_metadata_is_optional_and_has_a_round_trip_contract():
    metadata = DocumentMetadata(
        title="Synthetic source", authors=["Example Author"], year=2026,
        doi="10.0000/synthetic", source_uri="https://example.invalid/source",
    )
    document = Document.from_text(
        "# Synthetic\n", source_name="source.md", representation="text/markdown", metadata=metadata,
    )
    restored = Document.model_validate_json(document.model_dump_json())
    assert restored.metadata == metadata
    assert restored.representation == "text/markdown"


@pytest.mark.parametrize("field,value", [
    ("text", "a different source"),
    ("text_sha256", "0" * 64),
    ("document_id", "document-invented"),
])
def test_deserialized_document_rejects_inconsistent_text_hash_and_id(field, value):
    payload = Document.from_text(TEXT).model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValueError):
        Document.model_validate(payload)


def test_extract_accepts_document_and_keeps_string_api_compatible():
    document, result = make_result()
    assert result.source.document_id == document.document_id
    assert result.source.name == document.source_name
    assert result.source.sha256 == document.text_sha256
    assert result.source.character_count == len(TEXT)
    candidate = result.candidates[0]
    for field in candidate.fields.values():
        evidence = field.evidence
        assert TEXT[evidence.start:evidence.end] == evidence.quote
    response = {"records": []}
    string_result = extract(TEXT, TASK, backend=ReplayBackend([response]))
    assert string_result.source.sha256 == result.source.sha256


@pytest.mark.parametrize("status", ["accepted", "rejected", "needs_review"])
@pytest.mark.parametrize("missing", ["reviewer", "rationale"])
def test_explicit_review_decisions_require_nonblank_provenance(status, missing):
    payload = {
        "candidate_id": "candidate-example", "status": status,
        "reviewer": "Example reviewer", "rationale": "Reviewed the source.",
    }
    payload[missing] = "  "
    with pytest.raises(ValidationError):
        CandidateReview.model_validate(payload)


def test_structural_success_never_automatically_accepts_a_candidate():
    document, result = make_result()
    assert not result.candidates[0].needs_review
    archive = create_archive(document, result)
    assert archive.archive_schema_version == "0.1"
    assert archive.records == []
    assert len(archive.reviews) == 1
    assert archive.reviews[0].candidate_id == result.candidates[0].candidate_id
    assert archive.reviews[0].status == "unreviewed"


def test_unresolved_evidence_defaults_to_review_and_preserves_candidate():
    def changes(fields):
        fields["value"]["quote"] = "absent from the source"

    document, result = make_result(changes=changes)
    archive = create_archive(document, result)
    assert archive.reviews[0].status == "needs_review"
    assert archive.records == []
    assert archive.extraction.candidates[0].fields["value"].value == 12.5


def test_only_explicitly_accepted_candidates_become_source_linked_records():
    document, result = make_result()
    accepted = review(result.candidates[0].candidate_id, annotation_ref="synthetic/example-1")
    archive = create_archive(document, result, reviews=[accepted])
    assert len(archive.records) == 1
    record = archive.records[0]
    assert record.document_id == document.document_id
    assert record.source_candidate_id == result.candidates[0].candidate_id
    assert record.record_type == "observation"
    assert record.fields == result.candidates[0].fields
    assert archive.reviews == [accepted]
    assert archive.reviews[0].annotation_ref == "synthetic/example-1"


@pytest.mark.parametrize("status", ["rejected", "needs_review", "unreviewed"])
def test_nonaccepted_review_states_never_emit_final_records(status):
    document, result = make_result()
    archive = create_archive(document, result, reviews=[review(result.candidates[0].candidate_id, status)])
    assert archive.reviews[0].status == status
    assert archive.records == []


def test_archive_rejects_unknown_or_duplicate_candidate_reviews():
    document, result = make_result()
    known = review(result.candidates[0].candidate_id)
    for reviews in ([review("candidate-does-not-exist")], [known, known]):
        with pytest.raises(ValueError):
            create_archive(document, result, reviews=reviews)


def test_archive_rejects_extraction_from_another_document():
    _, result = make_result()
    other = Document.from_text("A wholly different document.")
    with pytest.raises(ValueError):
        create_archive(other, result)


@pytest.mark.parametrize("bad_quote", ["", "absent", "12.5"])
def test_unresolved_or_ambiguous_evidence_cannot_be_accepted(bad_quote):
    text = TEXT if bad_quote != "12.5" else TEXT + "Another 12.5."

    def changes(fields):
        fields["value"]["quote"] = bad_quote

    document, result = make_result(text=text, changes=changes)
    with pytest.raises(ValueError):
        create_archive(document, result, reviews=[review(result.candidates[0].candidate_id)])


@pytest.mark.parametrize("mutate", [
    lambda candidate: setattr(candidate.fields["value"].evidence, "end", len(TEXT) + 5),
    lambda candidate: setattr(candidate.fields["value"].evidence, "quote", "wrong excerpt"),
    lambda candidate: setattr(candidate, "record_type", "undeclared"),
    lambda candidate: candidate.fields.update(unknown=candidate.fields["value"]),
    lambda candidate: candidate.fields.pop("subject"),
    lambda candidate: candidate.fields.pop("condition"),
    lambda candidate: setattr(candidate.fields["value"], "value", "12.5"),
])
def test_archive_rechecks_evidence_and_task_instead_of_trusting_old_checks(mutate):
    document, result = make_result()
    mutate(result.candidates[0])
    result.candidates[0].needs_review = False
    with pytest.raises(ValueError):
        create_archive(document, result, reviews=[review(result.candidates[0].candidate_id)])


def test_archive_round_trip_preserves_fields_reviews_and_source_slices(tmp_path):
    archive = accepted_archive()
    output = tmp_path / "中文🙂.archive.json"
    save_archive(archive, output)
    loaded = load_archive(output)
    assert loaded.model_dump(mode="json") == archive.model_dump(mode="json")
    for record in loaded.records:
        for field in record.fields.values():
            evidence = field.evidence
            assert loaded.document.text[evidence.start:evidence.end] == evidence.quote


def test_modifying_candidate_value_cannot_reuse_its_previous_acceptance():
    archive = accepted_archive()
    changed_extraction = archive.extraction.model_copy(deep=True)
    changed_extraction.candidates[0].fields["value"].value = 99.5

    with pytest.raises(ValueError):
        create_archive(archive.document, changed_extraction, reviews=archive.reviews)


def test_matching_edits_to_candidate_and_record_cannot_preserve_old_review_identity(tmp_path):
    archive = accepted_archive()
    archive.extraction.candidates[0].fields["value"].value = 99.5
    archive.records[0].fields["value"].value = 99.5

    with pytest.raises(ValueError):
        ExtractionArchive.model_validate(archive.model_dump(mode="json"))
    output = tmp_path / "tampered-review.archive.json"
    with pytest.raises(ValueError):
        save_archive(archive, output)
    assert not output.exists()


@pytest.mark.parametrize("mutation", [
    lambda payload: payload["document"].update(text="replacement document"),
    lambda payload: payload["extraction"]["source"].update(sha256="0" * 64),
    lambda payload: payload["records"][0]["fields"]["value"].update(value=999),
    lambda payload: payload["records"][0].update(source_candidate_id="candidate-invented"),
    lambda payload: payload["records"][0].update(document_id="document-invented"),
    lambda payload: payload["reviews"][0].update(status="rejected"),
])
def test_loading_rejects_tampered_source_links_and_formal_records(tmp_path, mutation):
    payload = accepted_archive().model_dump(mode="json")
    mutation(payload)
    path = tmp_path / "tampered.archive.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_archive(path)


@pytest.mark.parametrize("invalid_json", [
    '{"archive_schema_version":"0.1","archive_schema_version":"0.1"}',
    '{"invalid":NaN}',
    '{"invalid":Infinity}',
    '{"invalid":-Infinity}',
])
def test_archive_reader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path, invalid_json):
    path = tmp_path / "invalid.archive.json"
    path.write_text(invalid_json, encoding="utf-8")
    with pytest.raises(ValueError):
        load_archive(path)

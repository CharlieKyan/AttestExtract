"""Archive boundaries for synthetic grouped observations and explicit review."""

from copy import deepcopy

import pytest

from attestextract import extract
from attestextract.archive import CandidateReview, ExtractionArchive, create_archive
from attestextract.backends import ReplayBackend
from attestextract.documents import Document
from attestextract.models import CandidateLink, EvidenceRef, SourceSpan


def task(*, grouped=True):
    spec = {
        "fields": {
            "subject": {"type": "string", "required": True},
            "parameter": {"type": "string", "required": True},
            "quantity": {"type": "string", "required": True},
            "unit": {"type": "string", "not_applicable_when": {"parameter": ["ratio"]}},
            "condition": {"type": "string", "required_when": {"parameter": ["capacity"]}},
        },
        "identity_fields": ["subject", "parameter"],
    }
    if grouped:
        spec["measurement_groups"] = {"reading": {
            "value_field": "quantity", "unit_field": "unit", "condition_fields": ["condition"],
        }}
    return {"name": "Synthetic archive measurement", "record_types": {"observation": spec}}


def response(*, parameter="厚度", quantity="17.5", unit="m", condition=None, grouped=True,
             context=None, unit_quote=""):
    context = context or f"{parameter}{quantity}" + (f" {unit}" if unit else "")
    record = {"record_type": "observation", "fields": {
        "subject": {"value": "样品甲", "quote": "样品甲"},
        "parameter": {"value": parameter, "quote": context},
        "quantity": {"value": quantity, "quote": context},
        "unit": {"value": unit, "quote": (unit_quote or context) if unit else ""},
        "condition": {"value": condition, "quote": condition or ""},
    }}
    if grouped:
        record["measurements"] = {"reading": {
            "context_quote": context, "value_text": quantity,
            "unit_text": unit or "", "unit_quote": unit_quote,
        }}
    return record


def run(*, source="样品甲厚度17.5 m。", record=None, grouped=True):
    document = Document.from_text(source, source_name="synthetic.txt")
    result = extract(document, task(grouped=grouped), backend=ReplayBackend([
        {"records": [record or response(grouped=grouped)]},
    ]))
    return document, result


def accept(result):
    return [CandidateReview(candidate_id=result.candidates[0].candidate_id, status="accepted",
                            reviewer="Synthetic test reviewer", rationale="Hand-checked synthetic example.",
                            origin="human")]


def conceal_review_flags(result):
    result.candidates[0].needs_review = False
    result.candidates[0].checks = []
    result.review = []


def test_optional_null_can_be_explicitly_accepted_but_never_automatically():
    document, result = run()
    assert result.candidates[0].field_status["condition"] == "not_reported"
    assert create_archive(document, result).records == []
    archive = create_archive(document, result, reviews=accept(result))
    assert len(archive.records) == 1
    assert archive.records[0].fields["condition"].value is None
    assert archive.extraction.candidates[0].measurements["reading"].semantic_status == "not_verified"


def test_all_missing_optional_fields_cannot_be_accepted_as_an_observation():
    document = Document.from_text("No measurement is supplied in this synthetic source.")
    result = extract(document, {"name": "Optional note", "record_types": {"entry": {
        "fields": {"note": {"type": "string"}},
    }}}, backend=ReplayBackend([{"records": [{"record_type": "entry", "fields": {
        "note": {"value": None, "quote": ""},
    }}]}]))
    with pytest.raises(ValueError, match="required-field"):
        create_archive(document, result, reviews=accept(result))


def test_legacy_optional_null_keeps_its_old_acceptance_restriction():
    document, result = run(grouped=False)
    result.schema_version = "0.1"
    assert create_archive(document, result).records == []
    with pytest.raises(ValueError, match="required-field"):
        create_archive(document, result, reviews=accept(result))


def test_task_declared_dimensionless_unit_can_be_explicitly_accepted():
    document, result = run(source="样品甲ratio 0.8。", record=response(
        parameter="ratio", quantity="0.8", unit=None, context="ratio 0.8",
    ))
    assert result.candidates[0].field_status["unit"] == "not_applicable"
    assert len(create_archive(document, result, reviews=accept(result)).records) == 1


@pytest.mark.parametrize("condition", [None, "", "   "])
def test_critical_missing_or_blank_condition_cannot_hide_behind_flags(condition):
    document, result = run(source="样品甲capacity60 t。", record=response(
        parameter="capacity", quantity="60", unit="t", condition=condition,
    ))
    conceal_review_flags(result)
    result.candidates[0].field_status["condition"] = "not_applicable"
    with pytest.raises(ValueError, match="required-field"):
        create_archive(document, result, reviews=accept(result))


def test_configured_missing_measurement_can_be_archived_for_review_but_not_accepted():
    document, result = run(record=response(grouped=False))
    assert create_archive(document, result).reviews[0].status == "needs_review"
    conceal_review_flags(result)
    with pytest.raises(ValueError, match="required-field"):
        create_archive(document, result, reviews=accept(result))


def test_wrong_header_column_cannot_be_accepted_by_deleting_issues():
    header = "| 样品 | 厚度 (m) | 压力 (bar) |"
    row = "| 样品甲 | 17.5 | 20 |"
    document, result = run(source=f"{header}\n| --- | --- | --- |\n{row}\n", record=response(
        context=row, unit="bar", unit_quote=header,
    ))
    assert result.candidates[0].measurements["reading"].issues
    assert create_archive(document, result).records == []
    conceal_review_flags(result)
    result.candidates[0].measurements["reading"].issues = []
    with pytest.raises(ValueError, match="mechanical checks"):
        create_archive(document, result, reviews=accept(result))


def test_token_cannot_be_relocated_to_same_text_outside_its_context():
    source = "样品甲厚度17.5 m。另一个读数17.5。"
    document, result = run(source=source)
    measurement = result.candidates[0].measurements["reading"]
    start = source.rindex("17.5")
    measurement.value = EvidenceRef(quote="17.5", status="matched", start=start, end=start + 4,
                                    matches=[SourceSpan(start=start, end=start + 4)])
    with pytest.raises(ValueError, match="within their exact context"):
        create_archive(document, result)


def test_group_context_and_token_offsets_are_revalidated_even_for_unaccepted_archives():
    document, result = run()
    token = result.candidates[0].measurements["reading"].unit
    token.start += 1
    token.end += 1
    token.matches = [SourceSpan(start=token.start, end=token.end)]
    with pytest.raises(ValueError, match="recover the exact"):
        create_archive(document, result)


def test_optional_null_does_not_disable_candidate_content_identity():
    document, result = run(grouped=False)
    archive = create_archive(document, result, reviews=accept(result))
    changed = archive.model_dump(mode="json")
    changed["extraction"]["candidates"][0]["fields"]["quantity"]["value"] = "99.5"
    changed["records"][0]["fields"]["quantity"]["value"] = "99.5"
    with pytest.raises(ValueError, match="Candidate content changed"):
        ExtractionArchive.model_validate(changed)


@pytest.mark.parametrize("ids", [["known", "unknown"], ["known", "known"]])
def test_link_ids_must_reference_distinct_existing_candidates(ids):
    document, result = run()
    known = result.candidates[0].candidate_id
    result.links = [CandidateLink(kind="possible_repeat", candidate_ids=[known if x == "known" else x for x in ids],
                                  compared_fields=["quantity"])]
    with pytest.raises(ValueError, match="distinct existing candidates"):
        create_archive(document, result)


def test_links_do_not_accept_candidates_and_cannot_name_undeclared_fields():
    document = Document.from_text("样品甲厚度17.5 m。样品乙厚度18.5 m。")
    second = response(quantity="18.5")
    second["fields"]["subject"] = {"value": "样品乙", "quote": "样品乙"}
    result = extract(document, task(), backend=ReplayBackend([{"records": [response(), second]}]))
    result.links = [CandidateLink(kind="possible_repeat", candidate_ids=[x.candidate_id for x in result.candidates],
                                  compared_fields=["quantity"])]
    assert create_archive(document, result).records == []
    result.links[0].compared_fields = ["undeclared"]
    with pytest.raises(ValueError, match="fields declared"):
        create_archive(document, result)


def test_legacy_v01_archive_loads_without_new_optional_metadata():
    document, result = run(grouped=False, source="样品甲厚度17.5 m。温度20℃。", record=response(
        grouped=False, condition="20℃",
    ))
    archive = create_archive(document, result, reviews=accept(result))
    payload = deepcopy(archive.model_dump(mode="json"))
    payload["extraction"]["schema_version"] = "0.1"
    payload["extraction"]["package_version"] = "0.1.0.dev6"
    payload["extraction"].pop("links")
    candidate = payload["extraction"]["candidates"][0]
    candidate.pop("field_status")
    candidate.pop("measurements")
    spec = payload["extraction"]["task"]["record_types"]["observation"]
    spec.pop("measurement_groups")
    spec.pop("comparison_fields")
    for field in spec["fields"].values():
        field.pop("required_when")
        field.pop("not_applicable_when")
    for item in payload["extraction"]["review"]:
        item.pop("severity")
    loaded = ExtractionArchive.model_validate(payload)
    assert loaded.extraction.schema_version == "0.1"
    assert len(loaded.records) == 1

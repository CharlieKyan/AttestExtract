"""Offline suggestions never become accepted facts or mutate their sources."""

from copy import deepcopy
import json
import re

import pytest
from pydantic import ValidationError

from attestextract import ExecutionOptions, extract
from attestextract.archive import create_archive
from attestextract.backends import ReplayBackend
from attestextract.chunking import chunk_document
from attestextract.decision_bundle import prepare_decision_bundle
from attestextract.decision_ledger import (
    DecisionLedger, check_decisions, decision_ledger_summary,
    load_decision_responses, save_decision_ledger,
)
from attestextract.diagnostics import RunTrace


def make_bundle(tmp_path, *, missing=False, unlocated=False, measurement=False):
    text = "Sample A reads 11 m. Sample B reads 22 m.\n\nContext without a measurement."
    task = {"name": "Synthetic decision suggestions", "record_types": {"reading": {
        "fields": {"subject": {"type": "string", "required": True},
                   "amount": {"type": "integer", "required": True},
                   "unit": {"type": "string"}},
        "identity_fields": ["subject"],
    }}}
    if missing:
        task["record_types"]["reading"]["fields"]["conditions"] = {"type": "string", "required": True}
    if measurement:
        task["record_types"]["reading"]["measurement_groups"] = {
            "reading": {"value_field": "amount", "unit_field": "unit"}}
    responses = []
    for chunk in chunk_document(text, max_chars=48):
        records = []
        for match in re.finditer(r"Sample ([AB]) reads (\d+) m\.", chunk.text):
            values = {"subject": "Sample " + match[1], "amount": int(match[2]), "unit": "m"}
            quotes = {name: match[0] for name in values}
            if missing:
                values["conditions"], quotes["conditions"] = None, ""
            if unlocated:
                quotes["amount"] = "A synthetic sentence not present in the source."
            records.append({"record_type": "reading", "values": values, "quotes": quotes})
        responses.append({"records": records})
    directory = tmp_path / "run"
    trace = RunTrace(directory / "diagnostics")
    result = extract(text, task, backend=ReplayBackend(responses), trace=trace, max_chunk_chars=48,
                     execution=ExecutionOptions(adaptive_chunking=False), backend_identity="synthetic-ledger-fixture")
    trace.finish(result.model_dump(mode="json"))
    (directory / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    return prepare_decision_bundle(directory)


@pytest.fixture
def bundle(tmp_path):
    return make_bundle(tmp_path)


def all_results(bundle, action="select"):
    return {"bundle_id": bundle.bundle_id, "results": [
        {"task_id": task.task_id, "action": action if task.candidate_ids else "review",
         "candidate_id": task.candidate_ids[0] if task.candidate_ids and action == "select" else None}
        for task in bundle.tasks
    ]}


def test_multiple_same_chunk_selections_are_only_suggestions(bundle, tmp_path):
    before = bundle.model_dump()
    source_before = (tmp_path / "run/result.json").read_bytes()
    assert len(bundle.batches[0].candidate_ids) == 2
    ledger = check_decisions(bundle, all_results(bundle))
    assert ledger.status == "valid"
    assert [entry.recommendation for entry in ledger.entries] == ["suggest_keep", "suggest_keep", "needs_review"]
    assert decision_ledger_summary(ledger) == {
        "bundle_id": bundle.bundle_id, "status": "valid", "response_count": 3,
        "valid_count": 3, "invalid_count": 0, "missing_count": 0,
        "selected_count": 2, "rejected_count": 0, "review_count": 1,
        "blocked_selection_count": 0, "applied": False, "created_final_records": 0, "model_calls": 0,
    }
    assert ledger.entries[-1].blocking_reasons[0].code == "coverage_not_verified"
    assert bundle.model_dump() == before
    assert (tmp_path / "run/result.json").read_bytes() == source_before
    archive = create_archive(bundle.document, bundle.extraction)
    assert archive.records == []
    assert {review.status for review in archive.reviews} == {"unreviewed"}
    path = tmp_path / "ledger.json"
    save_decision_ledger(ledger, path)
    assert DecisionLedger.model_validate_json(path.read_text(encoding="utf-8")) == ledger
    with pytest.raises(FileExistsError):
        save_decision_ledger(ledger, path)


@pytest.mark.parametrize("action, recommendation", [("reject", "suggest_reject"), ("review", "needs_review")])
def test_other_actions_do_not_modify_candidates(bundle, action, recommendation):
    before = bundle.model_dump()
    ledger = check_decisions(bundle, all_results(bundle, action))
    assert ledger.status == "valid"
    assert ledger.entries[0].recommendation == recommendation
    assert bundle.model_dump() == before


def test_missing_results_are_incomplete_not_invented(bundle):
    ledger = check_decisions(bundle, {"bundle_id": bundle.bundle_id, "results": []})
    assert ledger.status == "incomplete"
    assert ledger.entries == []
    assert ledger.missing_task_ids == [task.task_id for task in bundle.tasks]


def test_duplicate_responses_invalidate_both_not_first_wins(bundle):
    payload = all_results(bundle)
    payload["results"].append(deepcopy(payload["results"][0]))
    payload["results"][-1]["action"] = "review"
    payload["results"][-1]["candidate_id"] = None
    ledger = check_decisions(bundle, payload)
    assert ledger.status == "invalid"
    assert ledger.entries[0].code == ledger.entries[-1].code == "duplicate_result"
    assert ledger.missing_task_ids == [bundle.tasks[0].task_id]
    assert decision_ledger_summary(ledger)["selected_count"] == 1


@pytest.mark.parametrize("change, code", [
    ({"task_id": "unknown-private-task"}, "unknown_task"),
    ({"action": "delete"}, "invalid_action"),
    ({"candidate_id": "unknown-private-candidate"}, "invalid_candidate"),
    ({"candidate_id": 1}, "invalid_result"),
    ({"action": "review", "candidate_id": "unknown-private-candidate"}, "invalid_decision"),
    ({"private-extra": "secret-not-for-ledger"}, "invalid_result"),
])
def test_invalid_responses_are_rejected_without_raw_payload(bundle, change, code):
    payload = all_results(bundle)
    payload["results"][0].update(change)
    ledger = check_decisions(bundle, payload)
    assert ledger.status == "invalid"
    assert ledger.entries[0].code == code
    assert ledger.entries[0].result is None
    saved = ledger.model_dump_json()
    assert "unknown-private" not in saved and "secret-not-for-ledger" not in saved


def test_cross_task_selection_is_not_valid_even_with_known_candidate(bundle):
    payload = all_results(bundle)
    payload["results"][0]["candidate_id"] = bundle.tasks[1].candidate_ids[0]
    assert check_decisions(bundle, payload).entries[0].code == "invalid_candidate"


@pytest.mark.parametrize("action", ["select", "reject"])
def test_empty_chunk_only_permits_review(bundle, action):
    payload = all_results(bundle)
    payload["results"][-1]["action"] = action
    if action == "select":
        payload["results"][-1]["candidate_id"] = bundle.tasks[0].candidate_ids[0]
    ledger = check_decisions(bundle, payload)
    assert ledger.entries[-1].code == "action_not_allowed"
    assert ledger.missing_task_ids == [bundle.tasks[-1].task_id]


@pytest.mark.parametrize("raw", [None, [], "private string", {}, {"task_id": 12}, {"task_id": []}])
def test_malformed_entries_are_recorded(bundle, raw):
    ledger = check_decisions(bundle, {"bundle_id": bundle.bundle_id, "results": [raw]})
    assert ledger.entries[0].code == "invalid_result"
    assert ledger.status == "invalid"
    assert "private string" not in ledger.model_dump_json()


@pytest.mark.parametrize("option, expected", [("missing", "required_missing"), ("unlocated", "evidence_unmatched"),
                                               ("measurement", "measurement_missing")])
def test_select_does_not_override_existing_blockers(tmp_path, option, expected):
    bundle = make_bundle(tmp_path, **{option: True})
    before = bundle.model_dump()
    ledger = check_decisions(bundle, all_results(bundle))
    assert ledger.status == "valid"
    assert expected in {item.code for item in ledger.entries[0].blocking_reasons}
    assert ledger.entries[0].recommendation == "suggest_keep"
    assert decision_ledger_summary(ledger)["blocked_selection_count"] == 2
    assert bundle.model_dump() == before
    assert create_archive(bundle.document, bundle.extraction).records == []


def test_wrong_bundle_and_bad_envelopes_fail_without_private_content(bundle):
    for value in ([], {}, {"bundle_id": "wrong-private-value", "results": []},
                  {"bundle_id": bundle.bundle_id, "results": {}, "secret": "private-value"}):
        with pytest.raises(ValueError) as error:
            check_decisions(bundle, value)
        assert "private-value" not in str(error.value)


def test_mutated_bundle_is_revalidated(bundle):
    bundle.extraction.candidates[0].fields["amount"].value = 98765
    with pytest.raises(ValueError, match="Invalid decision bundle"):
        check_decisions(bundle, all_results(bundle))


@pytest.mark.parametrize("text", [
    '{"bundle_id":"a","bundle_id":"b","results":[]}',
    '{"bundle_id":"a","results":[{"task_id":"x","task_id":"y"}]}',
    '{"bundle_id":"a","results":[NaN]}',
    '{"bundle_id":"private-key","results":[',
])
def test_strict_response_json_and_safe_errors(tmp_path, text):
    path = tmp_path / "responses.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="valid UTF-8 JSON") as error:
        load_decision_responses(path)
    assert "private-key" not in str(error.value)


def test_response_loader_accepts_bom(bundle, tmp_path):
    payload = all_results(bundle)
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(payload), encoding="utf-8-sig")
    assert load_decision_responses(path) == payload


def test_ledger_cannot_claim_applied_or_clear_incomplete_status(bundle, tmp_path):
    ledger = check_decisions(bundle, {"bundle_id": bundle.bundle_id, "results": []})
    ledger.applied = True
    with pytest.raises(ValidationError):
        save_decision_ledger(ledger, tmp_path / "not-saved.json")
    assert not (tmp_path / "not-saved.json").exists()
    ledger.applied = False
    ledger.status = "valid"
    with pytest.raises(ValidationError):
        save_decision_ledger(ledger, tmp_path / "not-saved.json")

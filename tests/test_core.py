"""End-to-end checks for schema boundaries and original-source evidence."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from attestextract import TaskSpec, extract, load_task
from attestextract.backends import ReplayBackend
from attestextract.chunking import chunk_document
from attestextract.decisions import DecisionResult, DecisionTask, validate_decision
from attestextract.errors import InvalidResponseError, TaskValidationError


@pytest.fixture
def task() -> dict:
    return {
        "name": "Synthetic measurements",
        "record_types": {
            "measurement": {
                "fields": {
                    "subject": {"type": "string", "required": True},
                    "value": {"type": "number"},
                    "condition": {"type": "string"},
                },
                "identity_fields": ["subject"],
                "condition_fields": ["condition"],
            }
        },
    }


def record(subject="A", value=12, condition="warm", *, quote=None) -> dict:
    return {
        "record_type": "measurement",
        "fields": {
            "subject": {"value": subject, "quote": quote or subject},
            "value": {"value": value, "quote": quote or str(value)},
            "condition": {"value": condition, "quote": quote or condition},
        },
    }


def replay(*responses: dict | str) -> ReplayBackend:
    return ReplayBackend([
        json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item
        for item in responses
    ])


def test_unicode_offsets_round_trip_to_the_original_source(task):
    text = "前置🙂\n\n样品甲在 20 ℃ 时容量为 12.5。"
    response = record("样品甲", 12.5, "20 ℃")
    result = extract(text, task, backend=replay({"records": [response]}), source_name="中文.md")

    candidate = result.candidates[0]
    assert not candidate.needs_review
    assert not result.review
    for value in candidate.fields.values():
        evidence = value.evidence
        assert evidence.status == "matched"
        assert text[evidence.start:evidence.end] == evidence.quote
        assert evidence.start == text.index(evidence.quote)
    assert candidate.fields["subject"].evidence.start == len("前置🙂\n\n")
    assert result.source.character_count == len(text)
    assert result.source.name == "中文.md"
    assert result.schema_version == "0.2"
    assert result.run.mode == "replay"
    assert result.run.candidate_count == 1


def test_ambiguous_quote_is_reviewed_without_guessing_an_offset(task):
    text = "A has value 12 when warm; another reading is 12."
    result = extract(text, task, backend=replay({"records": [record()]}))
    evidence = result.candidates[0].fields["value"].evidence

    assert evidence.status == "ambiguous"
    assert evidence.start is None and evidence.end is None
    assert [(span.start, span.end) for span in evidence.matches] == [
        (text.index("12"), text.index("12") + 2),
        (text.rindex("12"), text.rindex("12") + 2),
    ]
    assert result.candidates[0].needs_review
    assert any(item.field == "value" and item.code == "evidence_ambiguous" for item in result.review)


@pytest.mark.parametrize("quote,status", [(None, "missing"), ("", "missing"), ("not in source", "unmatched")])
def test_absent_or_unmatched_quote_preserves_candidate_for_review(task, quote, status):
    response = record()
    if quote is None:
        del response["fields"]["value"]["quote"]
    else:
        response["fields"]["value"]["quote"] = quote
    result = extract("A has value 12 when warm.", task, backend=replay({"records": [response]}))

    candidate = result.candidates[0]
    assert candidate.fields["value"].value == 12
    evidence = candidate.fields["value"].evidence
    assert evidence.status == status
    assert evidence.start is None and evidence.end is None
    assert candidate.needs_review


def test_missing_identity_condition_and_null_value_are_reviewable(task):
    response = {"record_type": "measurement", "fields": {"value": {"value": None, "quote": "unreported"}}}
    result = extract("The result is unreported.", task, backend=replay({"records": [response]}))

    assert len(result.candidates) == 1
    assert result.candidates[0].needs_review
    assert {item.field for item in result.review if item.code == "missing_required_field"} == {"subject", "condition"}
    assert any(item.field == "value" and item.code == "missing_value" for item in result.review)


def test_matching_only_searches_the_current_chunk(task):
    first = "First observation."
    second = "Second observation."
    response = record(subject="A")
    response["fields"]["subject"]["quote"] = second
    result = extract(
        first + "\n\n" + second,
        task,
        backend=replay({"records": [response]}, {"records": []}),
        max_chunk_chars=len(first) + 2,
    )

    evidence = result.candidates[0].fields["subject"].evidence
    assert result.run.chunk_count == 2
    assert evidence.status == "unmatched"
    assert evidence.start is None


def test_same_values_at_different_source_positions_remain_separate(task):
    sentence = "A has value 12 when warm."
    response = {"records": [record()]}
    result = extract(
        sentence + "\n\n" + sentence,
        task,
        backend=replay(response, response),
        max_chunk_chars=len(sentence) + 2,
    )

    assert len(result.candidates) == 2
    assert len({candidate.candidate_id for candidate in result.candidates}) == 2
    assert {candidate.fields["value"].evidence.start for candidate in result.candidates} == {
        sentence.index("12"), len(sentence) + 2 + sentence.index("12"),
    }
    assert not result.review


def test_equal_numbers_under_different_conditions_are_not_merged(task):
    text = "A has value 12 when warm. B has value 12 when cold."
    warm = record(quote="A has value 12 when warm.")
    cold = record(subject="B", condition="cold", quote="B has value 12 when cold.")
    result = extract(text, task, backend=replay({"records": [warm, cold]}))

    assert len(result.candidates) == 2
    assert {candidate.fields["condition"].value for candidate in result.candidates} == {"warm", "cold"}


def test_table_headers_and_later_rows_keep_original_offsets(task):
    text = "# Results\n\n| Sample | Value | Condition |\n| --- | --- | --- |\n| A | 12 | warm |\n| B | 13 | cold |\n"
    chunks = chunk_document(text, table_rows_per_chunk=1)
    second_chunk = next(chunk for chunk in chunks if "| B |" in chunk.text)
    assert "| Sample | Value | Condition |" in second_chunk.text
    assert len(second_chunk.segments) == 2
    assert second_chunk.text == "".join(text[span.start:span.end] for span in second_chunk.segments)

    responses = [{"records": [record(subject="B", value=13, condition="cold")]} if "| B |" in chunk.text else {"records": []} for chunk in chunks]
    result = extract(text, task, backend=replay(*responses), table_rows_per_chunk=1)
    candidate = result.candidates[0]
    assert not candidate.needs_review
    assert candidate.fields["value"].evidence.start == text.index("13")
    assert text[candidate.fields["subject"].evidence.start:candidate.fields["subject"].evidence.end] == "B"


def test_repeated_header_evidence_deduplicates_the_same_original_span():
    text = "| Sample | Value |\n| --- | --- |\n| A | 12 |\n| B | 13 |\n"
    task = {"name": "Headers", "record_types": {"header": {"fields": {"label": {"type": "string"}}}}}
    response = {"records": [{"record_type": "header", "fields": {"label": {"value": "Value", "quote": "Value"}}}]}

    def run():
        return extract(text, task, backend=replay(response, response), table_rows_per_chunk=1)

    result = run()
    assert result.run.chunk_count == 2
    assert len(result.candidates) == 1
    assert len(result.candidates[0].chunk_ids) == 2
    assert result.candidates[0].fields["label"].evidence.start == text.index("Value")
    assert result.candidates[0].candidate_id == run().candidates[0].candidate_id


@pytest.mark.parametrize("response", [
    "not JSON",
    '{"records": [], "records": []}',
    '{"records": [{"record_type": "measurement", "fields": {"value": {"value": NaN, "quote": "12"}}}]}',
    '{"records": [{"record_type": "measurement", "fields": {"value": {"value": 12, "quote": null}}}]}',
    '{"records": [{"record_type": "unknown", "fields": {}}]}',
    '{"records": [{"record_type": "measurement", "fields": {"unknown": {"value": 1, "quote": "1"}}}]}',
])
def test_invalid_json_and_undeclared_output_are_explicit_failures(task, response):
    with pytest.raises(InvalidResponseError):
        extract("A has value 12 when warm.", task, backend=replay(response))


@pytest.mark.parametrize("value", ["12", True, [12], {"amount": 12}])
def test_field_types_are_not_silently_coerced(task, value):
    response = record(value=value)
    with pytest.raises(InvalidResponseError):
        extract("A has value 12 when warm.", task, backend=replay({"records": [response]}))


def test_load_task_reads_utf8_and_rejects_unknown_identity_fields(task, tmp_path):
    task["name"] = "参数提取🙂"
    path = tmp_path / "任务.json"
    path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    assert load_task(path).name == task["name"]
    task["record_types"]["measurement"]["identity_fields"] = ["undeclared"]
    path.write_text(json.dumps(task), encoding="utf-8")
    with pytest.raises(TaskValidationError):
        load_task(path)


def test_decision_results_are_bounded_to_the_provided_candidates():
    task = DecisionTask(task_id="decision-1", candidate_ids=["candidate-1", "candidate-2"])
    selected = DecisionResult(task_id=task.task_id, action="select", candidate_id="candidate-2")
    assert validate_decision(task, selected) == selected
    for action in ("reject", "review"):
        result = DecisionResult(task_id=task.task_id, action=action)
        assert validate_decision(task, result) == result
    for result in (
        DecisionResult(task_id=task.task_id, action="select", candidate_id="invented"),
        DecisionResult(task_id=task.task_id, action="select"),
        DecisionResult(task_id="other-task", action="review"),
        DecisionResult(task_id=task.task_id, action="reject", candidate_id="candidate-1"),
    ):
        with pytest.raises(ValueError):
            validate_decision(task, result)
    with pytest.raises(ValidationError):
        DecisionResult(task_id=task.task_id, action="verify")


def test_empty_document_does_not_call_the_model(task):
    result = extract(" \n\t", TaskSpec.model_validate(task), backend=replay())
    assert result.candidates == []
    assert result.review == []
    assert result.run.chunk_count == result.run.response_count == 0

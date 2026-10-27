"""Output failures explain every invalid field without exposing model inputs."""

from __future__ import annotations

import json

import pytest

from attestextract import cli, extract
from attestextract.backends import ReplayBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import InvalidResponseError
from attestextract.models import BackendResponse
from attestextract.pipeline import _parse_response


TASK = {
    "name": "Synthetic output diagnostics",
    "record_types": {"entry": {"fields": {
        "subject": {"type": "string", "required": True},
        "value": {"type": "number"},
        "condition": {"type": "string"},
        "unit": {"type": "string"},
        "note": {"type": "string"},
    }}},
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_no_input_snapshots(value):
    if isinstance(value, dict):
        assert not {"input", "input_value"} & value.keys()
        for child in value.values():
            assert_no_input_snapshots(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_input_snapshots(child)


def trace_failure(tmp_path, raw, *, secret=None):
    directory = tmp_path / "trace"
    trace = RunTrace(directory, secrets=[secret] if secret else [])
    with pytest.raises(InvalidResponseError) as caught:
        extract("Synthetic source only.", TASK, backend=ReplayBackend([raw]), trace=trace)
    exc = caught.value
    assert isinstance(exc.validation_details, dict)
    assert exc.validation_artifact is not None
    artifact = directory / exc.validation_artifact
    assert artifact.is_relative_to(directory)
    assert artifact.parent == directory / "validation"
    assert artifact.name.endswith(".errors.json")
    details = read_json(artifact)
    assert_no_input_snapshots(details)
    error = read_json(directory / "error.json")
    assert error["stage"] == "validation"
    assert error["validation_phase"] == details["phase"]
    assert error["error_count"] == details["error_count"]
    assert error["validation_details"] == exc.validation_artifact
    assert error["first_error"] == details["errors"][0]
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert not (directory / "result.json").exists()
    return directory, exc, details


def bare_records(counts, *, value="synthetic-field-value"):
    names = ["subject", "value", "condition", "unit", "note"]
    return {"records": [
        {"record_type": "entry", "fields": {name: value for name in names[:count]}}
        for count in counts
    ]}


def test_ten_records_with_43_bare_fields_report_all_43_paths(tmp_path):
    raw = json.dumps(bare_records([5, 5, 5, 4, 4, 4, 4, 4, 4, 4]))
    directory, exc, details = trace_failure(tmp_path, raw)
    assert details["phase"] == "records_contract"
    assert details["error_count"] == 43
    assert len(details["errors"]) == 43
    assert len({item["path"] for item in details["errors"]}) == 43
    first = details["errors"][0]
    assert first["path"] == "records[0].fields.subject"
    assert first["actual_type"] in {"string", "str"}
    assert "object" in first["expected"].lower()
    for item in details["errors"]:
        assert {"path", "code", "expected", "actual_type", "message"} <= item.keys()
    assert first["path"] in str(exc)
    assert first["expected"] in str(exc)
    assert first["actual_type"] in str(exc)
    assert "synthetic-field-value" not in json.dumps(details) + str(exc)
    assert list((directory / "responses").glob("*.txt"))


def test_error_list_is_bounded_but_total_count_is_complete(tmp_path):
    _, _, details = trace_failure(tmp_path, json.dumps(bare_records([5] * 16)))
    assert details["phase"] == "records_contract"
    assert details["error_count"] == 80
    assert len(details["errors"]) == 50
    assert details["truncated"] is True


def test_json_syntax_reports_line_and_column_after_removing_markdown_fence(tmp_path):
    inside = '{\n  "records": [\n}'
    raw = "```json\n" + inside + "\n```"
    with pytest.raises(json.JSONDecodeError) as expected:
        json.loads(inside)
    _, _, details = trace_failure(tmp_path, raw)
    assert details["phase"] == "json_syntax"
    assert details["error_count"] == 1
    first = details["errors"][0]
    assert first["line"] == expected.value.lineno
    assert first["column"] == expected.value.colno


@pytest.mark.parametrize("raw", [
    '{"records":[],"synthetic-private-name":0,"synthetic-private-name":1}',
    '{"records":[{"record_type":"entry","fields":{"value":{"value":NaN,"quote":"synthetic-private-name"}}}]}',
    '{"records":[{"record_type":"entry","fields":{"value":{"value":Infinity,"quote":"synthetic-private-name"}}}]}',
])
def test_duplicate_keys_and_nonfinite_numbers_have_safe_json_summaries(tmp_path, raw):
    _, exc, details = trace_failure(tmp_path, raw)
    assert details["phase"] == "json_syntax"
    assert details["error_count"] == 1
    assert "synthetic-private-name" not in json.dumps(details) + str(exc)


@pytest.mark.parametrize("record,path", [
    ({"record_type": "undeclared", "fields": {}}, "records[0].record_type"),
    ({"record_type": "entry", "fields": {"undeclared": {"value": "A", "quote": "A"}}}, "records[0].fields.undeclared"),
    ({"record_type": "entry", "fields": {"value": {"value": "12", "quote": "12"}}}, "records[0].fields.value.value"),
])
def test_task_schema_failures_identify_unknown_types_fields_and_wrong_scalar_types(tmp_path, record, path):
    _, exc, details = trace_failure(tmp_path, json.dumps({"records": [record]}))
    assert details["phase"] == "task_schema"
    assert details["error_count"] >= 1
    assert path in {item["path"] for item in details["errors"]}
    assert path in str(exc)


def test_correct_field_wrapper_with_missing_quote_is_preserved_for_review(tmp_path):
    trace = RunTrace(tmp_path / "review")
    result = extract("Sample A has value 12.", TASK, backend=ReplayBackend([{"records": [{
        "record_type": "entry", "fields": {
            "subject": {"value": "Sample A", "quote": "Sample A"},
            "value": {"value": 12},
        },
    }]}]), trace=trace)
    trace.finish(result.model_dump(mode="json"))

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_review
    assert candidate.fields["value"].value == 12
    assert candidate.fields["value"].evidence.status == "missing"
    assert any(item.field == "value" and item.code == "evidence_missing" for item in result.review)
    assert not (tmp_path / "review" / "error.json").exists()


def test_validation_metadata_and_artifacts_never_expose_secret_input_values(tmp_path):
    secret = "synthetic-validation-api-key"
    raw = json.dumps(bare_records([1], value=secret))
    directory, exc, details = trace_failure(tmp_path, raw, secret=secret)
    assert secret not in str(exc)
    assert secret not in json.dumps(exc.validation_details) + json.dumps(details)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())
    assert secret not in combined
    assert "[REDACTED]" in combined


def test_parse_response_without_trace_exposes_details_without_an_artifact_path():
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response('{"records":[{"record_type":"entry","fields":{"subject":"bare"}}]}')
    assert caught.value.validation_details["phase"] == "records_contract"
    assert caught.value.validation_artifact is None


def test_valid_response_contract_accepts_outer_null_only_inside_field_wrapper():
    parsed = _parse_response('{"records":[{"record_type":"entry","fields":{"subject":{"value":null,"quote":""}}}]}')
    assert parsed.records[0].fields["subject"].value is None
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response('{"records":[{"record_type":"entry","fields":{"subject":null}}]}')
    assert caught.value.validation_details["phase"] == "records_contract"
    assert caught.value.validation_details["errors"][0]["path"] == "records[0].fields.subject"


@pytest.mark.parametrize("raw,path,expected_parts", [
    ("[]", "$", ["object", "records", "array"]),
    ('{"records":["bad"]}', "records[0]", ["object", "record_type", "fields"]),
])
def test_wrong_response_or_record_shape_describes_its_actual_contract(raw, path, expected_parts):
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response(raw)
    first = caught.value.validation_details["errors"][0]
    assert first["path"] == path
    for part in expected_parts:
        assert part in first["expected"]
    assert "value" not in first["expected"]
    assert "quote" not in first["expected"]


def test_union_diagnostics_report_the_whole_inner_object_without_branch_name_paths():
    raw = '{"records":[{"record_type":"entry","fields":{"value":{"value":{"str":"bad"},"quote":""}}}]}'
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response(raw)
    errors = caught.value.validation_details["errors"]
    assert errors
    assert {item["path"] for item in errors} == {"records[0].fields.value.value"}
    assert {item["actual_type"] for item in errors} == {"object"}


def test_cli_masks_raw_and_json_escaped_secrets_in_error_paths_and_every_trace_artifact(tmp_path, monkeypatch, capsys):
    secret = 'synthetic-key-"quoted\\segment'
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", secret)
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    directory = tmp_path / "secret-path"
    source.write_text("Synthetic source only.", encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")

    class OfflineBackend:
        name = "synthetic-error-path-test"

        def __init__(self, **kwargs):
            assert kwargs["api_key"] == secret

        def generate(self, request):
            return BackendResponse(text=json.dumps({"records": [{
                "record_type": "entry", "fields": {secret: "bare synthetic value"},
            }]}))

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", OfflineBackend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--base-url", "https://example.invalid/v1", "--model", "synthetic-model",
        "--log-dir", str(directory),
    ])

    assert status == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert "[REDACTED]" in captured.err
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())
    combined = captured.err + captured.out + artifact_text
    assert secret not in combined
    assert json.dumps(secret, ensure_ascii=False)[1:-1] not in combined
    assert "[REDACTED]" in artifact_text

"""A gateway's HTTP success and permissive retention are not schema compliance."""

import hashlib
import json

import httpx
import pytest

from attestextract import extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, InvalidResponseError
from attestextract.models import ExtractionRequest, TaskSpec
from attestextract.prompting import build_output_schema
from attestextract.schema_diagnostics import assess_schema_compliance


TASK = TaskSpec.model_validate({
    "name": "Synthetic schema diagnostics",
    "record_types": {"reading": {"fields": {
        "amount": {"type": "number", "required": True},
        "note": {"type": "string"},
    }}},
})


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def output(fields):
    return json.dumps({"records": [{"record_type": "reading", "fields": fields}]})


def envelope(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}


@pytest.mark.parametrize("mode", ["off", "json_schema"])
def test_http_200_can_violate_requested_schema_without_dropping_candidate(tmp_path, mode):
    trace = RunTrace(tmp_path / "trace")
    sent = []

    def endpoint(request):
        sent.append(json.loads(request.content))
        # The candidate contract retains this missing quote/optional field for review.
        return httpx.Response(200, json=envelope(output({"amount": {"value": 12}})))

    backend = OpenAICompatibleBackend(
        "https://example.invalid/v1", "synthetic-model", trace=trace,
        structured_output=mode, transport=httpx.MockTransport(endpoint),
    )
    result = extract("Amount: 12.", TASK, backend=backend, trace=trace)
    assert len(result.candidates) == 1
    assert result.candidates[0].needs_review
    diagnostic = read(trace.directory / "schema/chunk-0001.compliance.json")
    assert diagnostic["status"] == "failed"
    assert diagnostic["schema_sent"] is (mode == "json_schema")
    assert diagnostic["server_enforcement"] == "not_verified"
    assert {error["path"] for error in diagnostic["errors"]} == {
        "records[0].fields.amount.quote", "records[0].fields.note",
    }
    expected = hashlib.sha256(json.dumps(build_output_schema(TASK), sort_keys=True,
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    request_info = read(trace.directory / "schema/chunk-0001.request.json")
    assert request_info["schema_sha256"] == diagnostic["schema_sha256"] == expected
    assert ("response_format" in sent[0]) is (mode == "json_schema")


def test_passed_is_an_observation_not_server_enforcement():
    text = output({"amount": {"value": 12, "quote": "Amount: 12."},
                   "note": {"value": None, "quote": ""}})
    result = assess_schema_compliance(text, build_output_schema(TASK), "json_schema")
    assert result["status"] == "passed"
    assert result["error_count"] == 0
    assert result["server_enforcement"] == "not_verified"


@pytest.mark.parametrize(("text", "code"), [
    ('```json\n{"records": []}\n```', "invalid_json"),
    ('{"records": [], "records": []}', "duplicate_key"),
    ('{"records": NaN}', "non_finite_number"),
    ('{"records": 1e999}', "non_finite_number"),
    ('{"records": [', "invalid_json"),
    ('{"records": [], "sensitive-generated-key": "private-value"}', "additional_property"),
    ('{"records": [{}]}', "required"),
    ('{"records": false}', "type"),
])
def test_raw_completion_failures_are_precise_and_do_not_echo_values(text, code):
    result = assess_schema_compliance(text, build_output_schema(TASK), "json_schema")
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == code
    assert "sensitive-generated-key" not in json.dumps(result)
    assert "private-value" not in json.dumps(result)


def test_schema_subset_checks_nested_branches_and_scalar_types():
    schema = {"anyOf": [
        {"type": "object", "properties": {"enabled": {"type": "boolean"}},
         "required": ["enabled"], "additionalProperties": False},
        {"type": "array", "items": {"type": ["integer", "null"]}},
    ]}
    for text in ('{"enabled": true}', '[1, 1.0, null]'):
        assert assess_schema_compliance(text, schema, "off")["status"] == "passed"
    for text in ('{"enabled": 1}', '[true]', '[1.2]'):
        assert assess_schema_compliance(text, schema, "off")["status"] == "failed"
    result = assess_schema_compliance('"wrong"', {"type": "string", "enum": ["ok"]}, "off")
    assert result["errors"][0]["code"] == "enum"


def test_unknown_constraints_and_absent_schema_never_claim_pass():
    result = assess_schema_compliance('"x"', {"type": "string", "minLength": 2}, "off")
    assert result["status"] == "not_evaluated"
    assert result["reason"] == "unsupported_schema"
    assert assess_schema_compliance('{}', None, "off")["reason"] == "no_output_schema"


def test_errors_are_bounded():
    text = json.dumps({"records": [{}] * 100})
    result = assess_schema_compliance(text, build_output_schema(TASK), "json_schema")
    assert result["error_count"] == 200
    assert result["truncated"] is True
    assert len(result["errors"]) == 50


def test_schema_rejection_retains_request_fingerprint_and_safe_provider_evidence(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    calls = []

    def endpoint(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {
            "param": "response_format", "message": "synthetic-key: unsupported format",
        }})

    backend = OpenAICompatibleBackend(
        "https://example.invalid/v1", "synthetic-model", api_key="synthetic-key",
        structured_output="json_schema", trace=trace, transport=httpx.MockTransport(endpoint),
    )
    with pytest.raises(BackendError) as caught:
        extract("Amount: 12.", TASK, backend=backend, trace=trace)
    assert len(calls) == 1
    diagnostic = caught.value.http_diagnostics
    assert diagnostic["schema_request"]["schema_sent"] is True
    assert diagnostic["root_cause_verified"] is False
    assert diagnostic["provider_error"]["param"] == "response_format"
    assert "synthetic-key" not in json.dumps(diagnostic)
    assert not (trace.directory / "schema/chunk-0001.compliance.json").exists()


def test_each_format_attempt_keeps_its_own_schema_observation(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    replies = iter(['{"records":[],"records":[]}', '{"records":[]}'])
    backend = OpenAICompatibleBackend(
        "https://example.invalid/v1", "synthetic-model", trace=trace,
        structured_output="json_schema",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(next(replies)))),
    )
    result = extract("Amount: 12.", TASK, backend=backend, trace=trace, format_retries=1)
    assert result.run.response_count == 2
    first = read(trace.directory / "schema/chunk-0001.compliance.json")
    second = read(trace.directory / "schema/chunk-0001.format-2.compliance.json")
    assert (first["status"], second["status"]) == ("failed", "passed")
    assert first["schema_sha256"] == second["schema_sha256"]


def test_truncation_is_observed_before_envelope_failure(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    backend = OpenAICompatibleBackend(
        "https://example.invalid/v1", "synthetic-model", trace=trace,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope('{"records": [', "length"))),
    )
    with pytest.raises(InvalidResponseError, match="truncated"):
        backend.generate(ExtractionRequest(chunk_id="chunk-0001", messages=[], output_schema=build_output_schema(TASK)))
    assert read(trace.directory / "schema/chunk-0001.compliance.json")["status"] == "failed"

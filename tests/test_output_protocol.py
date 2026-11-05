"""Flat live output and explicit legacy offline decoding share evidence checks."""

import json

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import ConfigurationError, InvalidResponseError
from attestextract.models import BackendResponse
from attestextract.pipeline import _format_feedback, _parse_response
from attestextract.models import TaskSpec
from attestextract.responses_backend import DeepSeekResponsesBackend


SOURCE = "Sample A: amount 12."
TASK = {"name": "Synthetic protocol boundary", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "测量.值": {"type": "integer", "required": True}},
}}}
FLAT = {"records": [{"record_type": "reading", "values": {"subject": "Sample A", "测量.值": 12},
                     "quotes": {"subject": SOURCE, "测量.值": SOURCE}}]}
NATIVE = {"records": [{"record_type": "reading", "fields": {
    "subject": {"value": "Sample A", "quote": SOURCE},
    "测量.值": {"value": 12, "quote": SOURCE},
}}]}


def test_explicit_legacy_replay_preserves_the_same_internal_evidence():
    flat = extract(SOURCE, TASK, backend=ReplayBackend([FLAT]))
    legacy = extract(SOURCE, TASK, backend=ReplayBackend([NATIVE], output_protocol="native-v1"))
    assert flat.candidates == legacy.candidates
    assert flat.review == legacy.review == []
    assert flat.run.output_protocol == "flat-v1"
    assert legacy.run.output_protocol == "native-v1"
    with pytest.raises(InvalidResponseError):
        extract(SOURCE, TASK, backend=ReplayBackend([NATIVE]))
    with pytest.raises(InvalidResponseError):
        extract(SOURCE, TASK, backend=ReplayBackend([FLAT], output_protocol="native-v1"))


@pytest.mark.parametrize("protocol", ["native", "auto", None, 1])
def test_replay_requires_a_known_explicit_protocol(protocol):
    with pytest.raises(ConfigurationError):
        ReplayBackend([], output_protocol=protocol)


def test_a_custom_backend_cannot_claim_legacy_by_attributes_or_replay_name():
    class PretendReplay:
        name = mode = "replay"
        output_protocol = "native-v1"

        def generate(self, request):
            assert request.output_protocol == "flat-v1"
            return BackendResponse(text=json.dumps(NATIVE))

    with pytest.raises(InvalidResponseError):
        extract(SOURCE, TASK, backend=PretendReplay())


def test_missing_quote_is_reviewable_and_raw_schema_failure_is_retained(tmp_path):
    payload = json.loads(json.dumps(FLAT))
    del payload["records"][0]["quotes"]["测量.值"]
    trace = RunTrace(tmp_path / "run")
    result = extract(SOURCE, TASK, backend=ReplayBackend([payload]), trace=trace)
    assert result.candidates[0].fields["测量.值"].evidence.status == "missing"
    assert result.candidates[0].needs_review
    compliance = json.loads((trace.directory / "schema/chunk-0001.compliance.json").read_text(encoding="utf-8"))
    assert compliance["status"] == "failed"
    assert compliance["server_enforcement"] == "not_verified"
    original = json.loads((trace.directory / "responses/chunk-0001.txt").read_text(encoding="utf-8"))
    assert original == payload
    normalized = json.loads((trace.directory / "normalized/chunk-0001.json").read_text(encoding="utf-8"))
    assert normalized["output_protocol"] == "flat-v1"
    record = normalized["response"]["records"][0]
    assert "measurements" not in record
    assert record["fields"]["测量.值"]["quote"] == ""


@pytest.mark.parametrize("mutation", ["orphan_quote", "quote_type", "extra_record", "extra_top",
                                    "unknown_value", "wrong_scalar", "undeclared_measurements"])
def test_flat_rejects_invalid_contracts_without_repair(mutation):
    payload = json.loads(json.dumps(FLAT))
    record = payload["records"][0]
    if mutation == "orphan_quote":
        record["quotes"]["extra"] = SOURCE
    elif mutation == "quote_type":
        record["quotes"]["subject"] = 1
    elif mutation == "extra_record":
        record["fields"] = {}
    elif mutation == "extra_top":
        payload["extra"] = 1
    elif mutation == "unknown_value":
        record["values"]["extra"] = "A"
    elif mutation == "wrong_scalar":
        record["values"]["测量.值"] = "12"
    else:
        record["measurements"] = {}
    with pytest.raises(InvalidResponseError):
        extract(SOURCE, TASK, backend=ReplayBackend([payload]))


def test_flat_scalar_object_errors_use_the_original_unicode_path():
    payload = json.loads(json.dumps(FLAT))
    payload["records"][0]["values"]["测量.值"] = {"str": "untrusted"}
    with pytest.raises(InvalidResponseError) as caught:
        extract(SOURCE, TASK, backend=ReplayBackend([payload]))
    errors = caught.value.validation_details["errors"]
    assert {item["path"] for item in errors} == {'records[0].values["测量.值"]'}
    assert {item["actual_type"] for item in errors} == {"object"}


def test_malformed_legacy_syntax_does_not_add_legacy_shapes_to_flat_retry():
    text = '{"records":[{"record_type":"reading","fields":{"subject":{"value":"A","quote":"A"},{"测量.值":{"value":12,"quote":"12"}}}}]}'
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response(text, allowed_keys={"subject", "测量.值"})
    assert "structure" in caught.value.validation_details["errors"][0]
    feedback = _format_feedback(caught.value.validation_details, TaskSpec.model_validate(TASK))
    assert "structure" not in feedback[0]
    assert feedback[0]["code"] == "invalid_json"


def test_regeneration_feedback_uses_flat_unicode_paths_without_values():
    bad = json.loads(json.dumps(FLAT))
    bad["records"][0]["values"]["测量.值"] = "private-synthetic-value"

    class Backend:
        name = "synthetic-flat-retry"

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return BackendResponse(text=json.dumps(bad if len(self.requests) == 1 else FLAT))

    backend = Backend()
    result = extract(SOURCE, TASK, backend=backend, format_retries=1)
    assert len(result.candidates) == 1 and len(backend.requests) == 2
    first, second = backend.requests
    feedback = second.messages[0]["content"][len(first.messages[0]["content"]):]
    assert 'records[0].values[\\"测量.值\\"]' in feedback
    assert "private-synthetic-value" not in feedback
    assert "fields.value" not in feedback


def test_cached_flat_response_reused_and_protocol_change_rejected_before_generate(tmp_path):
    options = ExecutionOptions(adaptive_chunking=False, max_model_calls=2)
    first = RunTrace(tmp_path / "first")
    result = extract(SOURCE, TASK, backend=ReplayBackend([FLAT]), trace=first, execution=options,
                     backend_identity="synthetic-fixed")
    resumed = extract(SOURCE, TASK, backend=ReplayBackend([]), trace=RunTrace(tmp_path / "second"),
                      execution=options, backend_identity="synthetic-fixed", resume_from=first.directory)
    assert resumed.candidates == result.candidates
    old = ReplayBackend([NATIVE], output_protocol="native-v1")
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        extract(SOURCE, TASK, backend=old, trace=RunTrace(tmp_path / "third"), execution=options,
                backend_identity="synthetic-fixed", resume_from=first.directory)
    assert old._position == 0
    state = json.loads((first.directory / "execution/checkpoint.json").read_text(encoding="utf-8"))["state"]
    assert state["output_protocol"] == "flat-v1"


@pytest.mark.parametrize("adapter", [OpenAICompatibleBackend, DeepSeekResponsesBackend])
@pytest.mark.parametrize("mode", ["off", "json_object", "json_schema"])
def test_live_adapters_only_send_flat_schema_and_never_protocol_metadata(adapter, mode):
    received = []

    def endpoint(request):
        received.append(json.loads(request.content))
        if adapter is OpenAICompatibleBackend:
            body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(FLAT)}}]}
        else:
            body = {"object": "response", "status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(FLAT)}]}]}
        return httpx.Response(200, json=body)

    backend = adapter("https://api.deepseek.com" if adapter is DeepSeekResponsesBackend else "https://models.example/v1",
                      "synthetic-model", structured_output=mode, transport=httpx.MockTransport(endpoint))
    result = extract(SOURCE, TASK, backend=backend)
    assert result.run.output_protocol == "flat-v1" and len(received) == 1
    assert "output_protocol" not in received[0]
    if mode == "json_schema":
        schema = (received[0]["response_format"]["json_schema"]["schema"] if adapter is OpenAICompatibleBackend
                  else received[0]["text"]["format"]["schema"])
        properties = schema["properties"]["records"]["items"]["properties"]
        assert "values" in properties and "quotes" in properties and "fields" not in properties

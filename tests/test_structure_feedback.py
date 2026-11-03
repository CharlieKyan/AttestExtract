"""Structural retry feedback must remain useful, private and budgeted end to end."""

import io
import json

import pytest

from attestextract import ExecutionOptions, extract
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, InvalidResponseError
from attestextract.models import BackendResponse
from attestextract.pipeline import _parse_response
from attestextract.progress import ConsoleProgress


TEXT = "Sample A reads 12."
TASK = {"name": "Synthetic structural feedback", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "number", "required": True}},
}}}
BAD = ('{"records":[{"record_type":"reading","fields":{"subject":'
       '{"value":"PRIVATE-SYNTHETIC-VALUE","quote":"PRIVATE-SYNTHETIC-QUOTE"},'
       '{"amount":{"value":12,"quote":"Sample A reads 12."}}}}]}')
GOOD = json.dumps({"records": [{"record_type": "reading", "fields": {
    "subject": {"value": "Sample A", "quote": TEXT},
    "amount": {"value": 12, "quote": TEXT},
}}]})


class Backend:
    name = "synthetic-structure-feedback"

    def __init__(self, *texts):
        self.texts = iter(texts)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return BackendResponse(text=next(self.texts))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(directory, backend, *, budget=3, resume=None, progress=None):
    return extract(TEXT, TASK, backend=backend, format_retries=1,
                   trace=RunTrace(directory, on_event=progress),
                   execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=budget),
                   backend_identity="synthetic-structure-v1", resume_from=resume)


def test_retry_contains_specific_redacted_shape_and_preserves_original_outputs(tmp_path):
    backend = Backend(BAD, GOOD)
    stream = io.StringIO()
    progress = ConsoleProgress(stream=stream)
    try:
        result = run(tmp_path / "run", backend, progress=progress)
    finally:
        progress.close()
    assert len(backend.requests) == 2
    assert result.candidates[0].fields["amount"].value == 12
    assert not result.candidates[0].needs_review
    diagnostic = read(tmp_path / "run/validation/chunk-0001.errors.json")["errors"][0]
    hint = diagnostic["structure"]
    assert diagnostic["code"] == "invalid_json"
    assert hint["path"] == "records[0].fields"
    assert hint["wrong_shape"] == '{"subject": <value>, {"amount": <value>}}'
    assert hint["correct_shape"] == '{"subject": <value>, "amount": <value>}'
    system = backend.requests[1].messages[0]["content"]
    assert '"unexpected_object_in_object"' in system and '"records[0].fields"' in system
    assert "PRIVATE-SYNTHETIC" not in system
    assert backend.requests[1].messages[1:] == backend.requests[0].messages[1:]
    assert (tmp_path / "run/responses/chunk-0001.txt").read_text(encoding="utf-8") == BAD
    assert (tmp_path / "run/responses/chunk-0001.format-2.txt").read_text(encoding="utf-8") == GOOD
    console = stream.getvalue()
    assert "records[0].fields; expected a named property, found anonymous '{'" in console
    assert "PRIVATE-SYNTHETIC" not in console


def test_budget_pause_preserves_structural_feedback_for_one_resumed_regeneration(tmp_path):
    first = Backend(BAD)
    with pytest.raises(BackendError) as caught:
        run(tmp_path / "first", first, budget=1)
    assert caught.value.code == "call_budget_exhausted"
    old = (tmp_path / "first/execution/checkpoint.json").read_bytes()
    saved = read(tmp_path / "first/execution/checkpoint.json")["state"]["nodes"]["chunk-0001"]
    assert saved["format_feedback"][0]["structure"]["path"] == "records[0].fields"
    assert saved["format_retries_reserved"] == 0
    resumed = Backend(GOOD)
    result = run(tmp_path / "second", resumed, budget=2, resume=tmp_path / "first")
    assert len(resumed.requests) == 1 and resumed.requests[0].format_attempt == 2
    assert '"unexpected_object_in_object"' in resumed.requests[0].messages[0]["content"]
    assert "PRIVATE-SYNTHETIC" not in resumed.requests[0].messages[0]["content"]
    assert result.run.response_count == 2 and len(result.candidates) == 1
    assert (tmp_path / "first/execution/checkpoint.json").read_bytes() == old


def test_repeated_structure_failure_remains_failure_and_resume_does_not_reset_allowance(tmp_path):
    backend = Backend(BAD, BAD)
    with pytest.raises(InvalidResponseError):
        run(tmp_path / "first", backend)
    assert len(backend.requests) == 2
    assert not (tmp_path / "first/result.json").exists()
    resumed = Backend(GOOD)
    with pytest.raises(InvalidResponseError) as caught:
        run(tmp_path / "second", resumed, budget=9, resume=tmp_path / "first")
    assert caught.value.code == "format_retry_exhausted"
    assert resumed.requests == []


def test_regenerated_json_still_requires_source_grounding(tmp_path):
    good_shape_bad_evidence = GOOD.replace(TEXT, "Absent synthetic quote.")
    result = run(tmp_path / "run", Backend(BAD, good_shape_bad_evidence))
    assert result.candidates[0].needs_review
    assert result.candidates[0].fields["amount"].evidence.status == "unmatched"


def test_fenced_unicode_offsets_and_structure_path_are_independent():
    bad = BAD.replace("PRIVATE-SYNTHETIC-VALUE", "合成🙂")
    fenced = " \r\n```json\r\n" + bad + "\r\n```\r\n"
    with pytest.raises(InvalidResponseError) as caught:
        _parse_response(fenced, allowed_keys={"subject", "amount"})
    error = caught.value.validation_details["errors"][0]
    assert error["structure"]["path"] == "records[0].fields"
    assert error["offset"] == bad.index('{"amount"')
    assert error["raw_offset"] == fenced.index('{"amount"')
    assert error["raw_line"] == 3


def test_unknown_keys_and_nested_values_are_not_echoed_into_structure_feedback(tmp_path):
    bad = BAD.replace("subject", "UNKNOWN-PRIVATE-KEY").replace("amount", "ANOTHER-PRIVATE-KEY")
    backend = Backend(bad, GOOD)
    run(tmp_path / "run", backend)
    system = backend.requests[1].messages[0]["content"]
    assert "PRIVATE-KEY" not in system and "PRIVATE-SYNTHETIC" not in system
    assert "<undeclared>" in system

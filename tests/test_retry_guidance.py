"""Generic syntax failures must not refer to nonexistent structural illustrations."""

import json

import pytest

from attestextract import extract
from attestextract.diagnostics import RunTrace
from attestextract.models import BackendResponse


TASK = {"name": "Synthetic syntax guidance", "record_types": {"reading": {
    "fields": {"amount": {"type": "integer", "required": True}},
}}}
SOURCE = "Sample reads 12."
GOOD = json.dumps({"records": [{"record_type": "reading", "values": {"amount": 12},
                               "quotes": {"amount": SOURCE}}]})


@pytest.mark.parametrize("bad", [
    # Earlier premature closure leaves a named property in the records array.
    '{"records":[{"record_type":"reading","fields":{}},"measurements":{}]}',
    # A property name lacks its opening double quote.
    '{"records":[{"record_type":"reading","fields":{amount":{"value":12}}}]}',
])
def test_explicit_retry_without_specific_hint_uses_only_relevant_diagnostics(tmp_path, bad):
    class Backend:
        name = "synthetic-guidance"

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return BackendResponse(text=bad if len(self.requests) == 1 else GOOD)

    backend = Backend()
    result = extract(SOURCE, TASK, backend=backend, format_retries=1, trace=RunTrace(tmp_path / "run"))
    assert len(backend.requests) == 2 and len(result.candidates) == 1
    first, second = backend.requests
    assert first.messages[1:] == second.messages[1:]
    feedback = second.messages[0]["content"][len(first.messages[0]["content"]):]
    assert '"invalid_json"' in feedback and '"column"' in feedback
    assert "wrong_shape" not in feedback and "correct_shape" not in feedback and "<value>" not in feedback
    assert bad not in feedback
    assert (tmp_path / "run/responses/chunk-0001.txt").read_text(encoding="utf-8") == bad
    assert result.candidates[0].fields["amount"].evidence.status == "matched"

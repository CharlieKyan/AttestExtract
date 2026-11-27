import json

import pytest

from niceextract import ContextOptions, ExecutionOptions, extract
from niceextract.errors import ConfigurationError, InvalidResponseError
from niceextract.models import BackendResponse
from niceextract.task_input import compile_task


TEXT = "Unit A measured 10 m. Unit B measured 15 m. Unit C measured 9 m. Unit D measured 11 m."
TASK = compile_task(prompt_description="Read ranges.", field_list={"range": "Measured length."})


class Fixture:
    name = "recorded-syntax"
    mode = "live"

    def __init__(self, bad=None):
        self.requests = []
        self.bad = bad

    def generate(self, request):
        self.requests.append(request)
        return BackendResponse(text=self.bad or '{"records":[]}', finish_reason="stop", model="fixture")


def test_short_double_failure_bisects_at_a_sentence_and_restores_the_identical_tree(tmp_path):
    from niceextract.errors import BackendError
    source = "Sensor A reports 3.14 mg. Sensor B reports 0.01 mg."
    def execute(directory, backend, maximum=4, **options):
        return extract(source, TASK, observation_protocol="source-v2", backend=backend, backend_identity="short",
                       execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=500, max_model_calls=maximum),
                       format_retries=1, output_dir=directory, progress="quiet", **options)
    old, second, final = (tmp_path / name for name in ("old", "next", "final"))
    failed = Fixture('{"records":[')
    with pytest.raises(BackendError, match="budget"):
        execute(old, failed, maximum=2)
    state = json.loads((old / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    root, = state["roots"]
    receipt, = state["migrations"]
    assert receipt["split_policy"] == "sentence-midpoint-v1" and state["calls_used"] == 2 and len(failed.requests) == 2
    boundary = source.index("Sensor B")
    children = state["nodes"][root]["children"]
    assert [state["nodes"][child]["body_segments"] for child in children] == [
        [{"start": 0, "end": boundary}], [{"start": boundary, "end": len(source)}]]
    success = Fixture()
    execute(second, success, resume_from=old)
    assert [request.chunk_id for request in success.requests] == children
    cached = Fixture()
    execute(final, cached, resume_from=second)
    assert cached.requests == []


@pytest.mark.parametrize("source,depth", [("A core measured 0.3 mg and 1.24 mg.", 3), (TEXT, 0)])
def test_short_syntax_split_respects_sentence_boundaries_and_maximum_depth(tmp_path, source, depth):
    failed = Fixture('{"records":[')
    with pytest.raises(InvalidResponseError):
        extract(source, TASK, observation_protocol="source-v2", backend=failed, backend_identity="test",
                execution=ExecutionOptions(min_chunk_chars=500, max_split_depth=depth, max_model_calls=4),
                format_retries=1, output_dir=tmp_path, progress="quiet")
    assert len(failed.requests) == 2


def run(directory, backend, *, budget=6, retries=1, **kwargs):
    return extract(TEXT, TASK, observation_protocol="source-v2", backend=backend, backend_identity="fixture",
                   execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=budget, min_chunk_chars=10),
                   context=ContextOptions(), table_mode="text", format_retries=retries,
                   output_dir=directory, progress="quiet", **kwargs)


@pytest.mark.parametrize("explicit", [False, True])
def test_split_exhausted_syntax_reuses_full_ledger_and_resumes_without_calls(tmp_path, monkeypatch, explicit):
    original, output = tmp_path / "old", tmp_path / "split"
    first = Fixture('{"records":[{')
    # Recreate the older executor's terminal checkpoint with intact recorded replies.
    with monkeypatch.context() as old:
        old.setattr("niceextract.prose_syntax_retry.split_verified_syntax_failure", lambda *args: [])
        with pytest.raises(InvalidResponseError):
            run(original, first)
    assert len(first.requests) == 2
    second = Fixture()
    result = run(output, second, resume_from=original, retry_prose_syntax=explicit)
    ledger = json.loads((output / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    assert ledger["calls_used"] == result.run.response_count == 2 + len(second.requests)
    parent = first.requests[0].chunk_id
    assert [r.chunk_id for r in second.requests] == ledger["nodes"][parent]["children"]
    audit, = ledger["migrations"]
    assert audit["children"] == ledger["nodes"][parent]["children"] and len(audit["response_sha256"]) == 2
    assert ledger["nodes"][parent]["calls"] == 2
    third = Fixture()
    resumed = run(tmp_path / "resume", third, resume_from=output)
    assert not third.requests and resumed.candidates == result.candidates


def test_valid_json_with_bad_observation_cannot_trigger_syntax_split(tmp_path):
    original, second = tmp_path / "old", Fixture()
    with pytest.raises(InvalidResponseError):
        run(original, Fixture('{"records":[{"record_type":12}]}'))
    with pytest.raises(ConfigurationError, match="syntax"):
        run(tmp_path / "other", second, resume_from=original, retry_prose_syntax=True)
    assert not second.requests


def test_two_normal_completions_with_invalid_json_split_during_first_run(tmp_path):
    class RejectParent(Fixture):
        def generate(self, request):
            self.bad = '{"records":[{' if "." not in request.chunk_id else None
            return super().generate(request)
    backend = RejectParent()
    result = run(tmp_path, backend)
    ledger = json.loads((tmp_path / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    parent = ledger["roots"][0]
    assert len(ledger["nodes"][parent]["children"]) >= 2
    assert [r.chunk_id for r in backend.requests] == [parent, parent, *ledger["nodes"][parent]["children"]]
    receipt, = ledger["migrations"]
    from hashlib import sha256
    assert receipt["response_sha256"] == [sha256(b'{"records":[{').hexdigest()] * 2
    assert result.run.response_count == ledger["calls_used"] == len(backend.requests) <= 6


def test_first_bad_reply_and_regeneration_in_different_runs(tmp_path):
    original, output = tmp_path / "original", tmp_path / "resume"
    first = Fixture('{"records":[{')
    with pytest.raises(InvalidResponseError):
        run(original, first, retries=0)
    parent = first.requests[0].chunk_id
    class SecondTry(Fixture):
        def generate(self, request):
            self.bad = '{"records":[' if request.chunk_id == parent else None
            return super().generate(request)
    second = SecondTry()
    result = run(output, second, resume_from=original)
    assert second.requests[0].format_attempt == 2
    assert result.run.response_count == 1 + len(second.requests)
    state = json.loads((output / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    assert state["migrations"][0]["attempt_diagnostics"] == [str(path / "diagnostics") for path in (original, output)]


def test_exhausted_budget_saves_split_and_never_spends_a_child_call(tmp_path):
    from niceextract.errors import BackendError
    failed, other = tmp_path / "limited", tmp_path / "more"
    two = Fixture('{"records":[{')
    with pytest.raises(BackendError) as caught:
        run(failed, two, budget=2)
    assert "budget" in caught.value.code and len(two.requests) == 2
    state = json.loads((failed / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    parent = state["roots"][0]
    assert state["nodes"][parent]["children"] and state["calls_used"] == 2
    continued = Fixture()
    run(other, continued, resume_from=failed)
    assert [r.chunk_id for r in continued.requests] == state["nodes"][parent]["children"]


def test_unknown_transmission_outcome_after_invalid_json_cannot_trigger_split(tmp_path):
    from niceextract.errors import BackendError
    class Timeout(Fixture):
        def generate(self, request):
            if request.format_attempt == 2:
                self.requests.append(request)
                raise BackendError("read timeout", code="timeout")
            return super().generate(request)
    backend = Timeout('{"records":[{')
    with pytest.raises(BackendError, match="timeout"):
        run(tmp_path, backend)
    state = json.loads((tmp_path / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    assert len(backend.requests) == 2 and not state["nodes"][state["roots"][0]]["children"]


def test_resume_detects_an_altered_archived_syntax_diagnosis_before_generation(tmp_path, monkeypatch):
    original = tmp_path / "old"
    first = Fixture('{"records":[{')
    with monkeypatch.context() as old:
        old.setattr("niceextract.prose_syntax_retry.split_verified_syntax_failure", lambda *args: [])
        with pytest.raises(InvalidResponseError):
            run(original, first)
    key = first.requests[0].chunk_id
    (original / "diagnostics" / "responses" / f"{key}.format-2.txt").write_text('{"records":[]}', encoding="utf8")
    second = Fixture()
    with pytest.raises(ConfigurationError, match="Cannot verify archived prose syntax failure"):
        run(tmp_path / "resume", second, resume_from=original)
    assert second.requests == []

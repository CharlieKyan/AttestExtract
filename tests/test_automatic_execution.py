"""Synthetic accounting and immutable recovery, with no model/network calls."""

import json

import pytest

from attestextract.automatic import ModelConnection, build_automatic_backend
from attestextract.automatic_execution import AutomaticExecutionSession, validate_automatic_state
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.execution import _read_state, digest
from attestextract.models import BackendResponse, DocumentChunk, ExecutionOptions, SourceSpan


def coordinator(tmp_path, *, fallback=True, read_timeout=None):
    primary = ModelConnection(base_url="https://research-unseen.invalid/v1", model="research-model-v9",
                              api_key="synthetic-primary", read_timeout=read_timeout)
    backup = ModelConnection(base_url="https://backup-unseen.invalid/v1", model="backup-v1",
                             api_key="synthetic-backup") if fallback else None
    return build_automatic_backend(primary, fallback=backup, cache_dir=tmp_path / "cache", use_cache=False)


def session(tmp_path, *, count=1, resume=None, backend=None, budget=None, retries=0, fingerprint="synthetic-identity"):
    chunks = [DocumentChunk(chunk_id=f"chunk-{i:04}", text="Lamp A uses 8 W.",
                            segments=[SourceSpan(start=(i - 1) * 16, end=i * 16)]) for i in range(1, count + 1)]
    backend = backend or coordinator(tmp_path)
    trace = RunTrace(tmp_path / "diagnostics")
    return AutomaticExecutionSession("Lamp A uses 8 W." * count, chunks,
        ExecutionOptions(max_model_calls=budget), fingerprint, backend=backend, trace=trace,
        resume_from=resume, output_protocol="flat-v1", format_retries=retries,
        transport_settings=backend.transport_settings)


def complete_one(run, key, *, model="research-model-v9"):
    route = run.route_for(key)
    attempt = run.attempt_session(key, route.route_id)
    attempt.reserve(key)
    response = BackendResponse(text='{"records":[]}', model=model, reported_model=model,
                               usage={"prompt_tokens": 3, "completion_tokens": 2}, request_id="synthetic-request")
    attempt.observe(response)
    attempt.complete(key, response)
    return attempt


def malformed():
    return InvalidResponseError("Synthetic invalid JSON", validation_details={"phase": "json_syntax", "errors": []})


def test_default_budget_and_response_provenance(tmp_path):
    run = session(tmp_path, count=2)
    assert run.state.max_model_calls == 4
    complete_one(run, "chunk-0001")
    validate_automatic_state(run.state, run.cached)
    assert run.state.calls_used == run.state.response_count == run.state.request_attempts == 1
    assert run.state.model is None
    item = run.provenance_dump()
    assert item["chunk_provenance"]["chunk-0001"]["attempt_id"] == "chunk-0001.primary-01.attempt-1"
    assert item["generation_attempts"][0]["status"] == "completed"
    assert all("base_url" not in route["config"] for route in item["routes"].values())


def test_format_failure_uses_backup_without_lowering_mode(tmp_path):
    run = session(tmp_path)
    key = "chunk-0001"
    route = run.route_for(key)
    attempt = run.attempt_session(key, route.route_id)
    attempt.reserve(key)
    attempt.observe(BackendResponse(text="broken", model="research-model-v9"))
    attempt.reject_format(key, [])
    assert run.advance(key, route.route_id, malformed())
    assert run.route_for(key).role == "fallback"
    assert run.route_for(key).config.structured_output == "json_schema"
    complete_one(run, key, model="backup-v1")
    validate_automatic_state(run.state, run.cached)
    assert run.state.calls_used == 2
    assert run.state.route_plan["preferred_route_id"] == "primary-01"
    assert [item.status for item in run.state.generation_attempts] == ["rejected", "completed"]


@pytest.mark.parametrize("code", ["timeout", "transport_error"])
def test_uncertain_generation_never_advances_to_backup(tmp_path, code):
    run = session(tmp_path)
    key = "chunk-0001"
    route = run.route_for(key)
    attempt = run.attempt_session(key, route.route_id)
    attempt.reserve(key)
    error = BackendError("Synthetic unknown outcome", attempts=1, code=code)
    attempt.backend_failed(error)
    assert not run.advance(key, route.route_id, error)
    assert run.state.generation_attempts[0].outcome_unknown is True
    assert run.state.calls_used == 1
    validate_automatic_state(run.state)


def test_resume_eleven_completed_keeps_timeout_call_and_reuses_cache(tmp_path):
    original = session(tmp_path / "first", count=16)
    for i in range(1, 12):
        complete_one(original, f"chunk-{i:04}")
    key = "chunk-0012"
    route = original.route_for(key)
    attempt = original.attempt_session(key, route.route_id)
    attempt.reserve(key)
    error = BackendError("Synthetic timeout", attempts=1, code="timeout")
    attempt.backend_failed(error)
    assert not original.advance(key, route.route_id, error)
    source = tmp_path / "first" / "diagnostics"
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    resumed = session(tmp_path / "second", count=16, resume=source,
                      backend=coordinator(tmp_path / "second", read_timeout=240), budget=17)
    assert len(resumed.cached) == 11 and resumed.state.calls_used == 12
    for i in range(12, 17):
        complete_one(resumed, f"chunk-{i:04}")
    validate_automatic_state(resumed.state, resumed.cached)
    assert resumed.state.calls_used == 17 and len(resumed.cached) == 16
    assert resumed.state.transport_changes
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_resume_ignores_changed_success_cache_preference(tmp_path):
    original = session(tmp_path / "first")
    second_backend = coordinator(tmp_path / "second")
    second_backend.preferred_route_id = "primary-03"
    second_backend.fallback_route_id = "fallback-03"
    resumed = session(tmp_path / "second", resume=tmp_path / "first" / "diagnostics", backend=second_backend)
    assert resumed.route_for("chunk-0001").route_id == "primary-01"
    assert second_backend.fallback_route_id == "fallback-01"


@pytest.mark.parametrize("change", ["identity", "routes", "cache", "ledger"])
def test_resume_rejects_corruption_before_reservation(tmp_path, change):
    original = session(tmp_path / "first")
    complete_one(original, "chunk-0001")
    directory = tmp_path / "first" / "diagnostics"
    checkpoint = directory / "execution" / "checkpoint.json"
    envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
    backend = coordinator(tmp_path / "second")
    if change == "identity":
        envelope["state"]["fingerprint"] = "different"
    elif change == "routes":
        envelope["state"]["route_plan"]["routes"][0]["config"]["model"] = "different"
    elif change == "cache":
        (directory / "execution" / "cache" / "chunk-0001.json").write_text("{}", encoding="utf-8")
    else:
        envelope["state"]["generation_attempts"][0]["http_attempts"] = 0
    envelope["sha256"] = digest(envelope["state"])
    checkpoint.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        session(tmp_path / "second", resume=directory, backend=backend)


def test_interrupted_reservation_is_not_erased_on_manual_resume(tmp_path):
    run = session(tmp_path / "first")
    key = "chunk-0001"
    run.attempt_session(key, run.route_for(key).route_id).reserve(key)
    resumed = session(tmp_path / "second", resume=tmp_path / "first" / "diagnostics")
    complete_one(resumed, key)
    assert resumed.state.calls_used == 2
    assert resumed.state.generation_attempts[0].outcome_unknown is True
    assert resumed.state.generation_attempts[1].attempt_id.endswith("attempt-2")
    validate_automatic_state(resumed.state, resumed.cached)


def test_three_attempt_limit_cannot_be_reset_by_larger_total_budget(tmp_path):
    run = session(tmp_path / "first", budget=20)
    key = "chunk-0001"
    route = run.route_for(key)
    for _ in range(3):
        attempt = run.attempt_session(key, route.route_id)
        attempt.reserve(key)
        error = BackendError("Synthetic timeout", code="timeout")
        attempt.backend_failed(error)
        assert not run.advance(key, route.route_id, error)
    resumed = session(tmp_path / "second", resume=tmp_path / "first" / "diagnostics", budget=30)
    with pytest.raises(BackendError, match="three generation"):
        resumed.attempt_session(key, route.route_id).reserve(key)
    assert resumed.state.calls_used == 3


def test_interrupt_after_response_preserves_usage_and_closes_old_attempt_on_resume(tmp_path):
    original = session(tmp_path / "first")
    key = "chunk-0001"
    attempt = original.attempt_session(key, original.route_for(key).route_id)
    attempt.reserve(key)
    attempt.observe(BackendResponse(text='{"records":[]}', model="research-model-v9",
                                     usage={"completion_tokens": 7}))
    resumed = session(tmp_path / "second", resume=tmp_path / "first" / "diagnostics")
    complete_one(resumed, key)
    previous = resumed.state.generation_attempts[0]
    assert previous.status == "failed" and previous.error_code == "interrupted"
    assert previous.response_received and not previous.outcome_unknown
    assert previous.usage == {"completion_tokens": 7} and previous.http_attempts == 1
    assert resumed.state.response_count == 2
    assert resumed.state.usage == {"completion_tokens": 9, "prompt_tokens": 3}
    validate_automatic_state(resumed.state, resumed.cached)


def test_explicit_format_regeneration_is_shared_with_call_budget(tmp_path):
    run = session(tmp_path, retries=1)
    key = "chunk-0001"
    route = run.route_for(key)
    first = run.attempt_session(key, route.route_id)
    first.reserve(key)
    first.observe(BackendResponse(text="broken"))
    first.reject_format(key, [])
    assert run.advance(key, route.route_id, malformed())
    second = run.attempt_session(key, route.route_id)
    assert second.format_attempt(key)[0] == 2
    second.reserve(key, format_attempt=2)
    response = BackendResponse(text='{"records":[]}')
    second.observe(response)
    second.complete(key, response)
    validate_automatic_state(run.state, run.cached)
    assert run.state.calls_used == 2 and run.state.format_regenerations[key] == 1


def test_state_reader_dispatches_v4_and_verifies_aggregate(tmp_path):
    run = session(tmp_path)
    complete_one(run, "chunk-0001")
    payload = run.state.model_dump(mode="json")
    assert _read_state({"state": payload, "sha256": digest(payload)}).checkpoint_version == "4"
    payload["response_count"] = 0
    with pytest.raises(ValueError, match="aggregate ledger"):
        _read_state({"state": payload, "sha256": digest(payload)})


def test_resume_rejects_received_response_marked_unknown_before_any_reservation(tmp_path, monkeypatch):
    original = session(tmp_path / "first", count=2)
    complete_one(original, "chunk-0001")
    checkpoint = tmp_path / "first" / "diagnostics" / "execution" / "checkpoint.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["state"]["generation_attempts"][0]["outcome_unknown"] = True
    payload["sha256"] = digest(payload["state"])
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    backend = coordinator(tmp_path / "second")
    for route in backend.routes.values():
        monkeypatch.setattr(route.backend, "generate", lambda request: pytest.fail("Corrupt checkpoint must not generate"))
    with pytest.raises(ConfigurationError, match="damaged|incompatible"):
        session(tmp_path / "second", count=2, resume=tmp_path / "first" / "diagnostics", backend=backend)
    assert not (tmp_path / "second" / "diagnostics" / "execution" / "checkpoint.json").exists()
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["state"]["calls_used"] == 1


def test_eligible_fallback_reports_exhausted_budget_without_rewriting_failure(tmp_path):
    run = session(tmp_path / "first", budget=1)
    key = "chunk-0001"
    first = run.attempt_session(key, run.route_for(key).route_id)
    first.reserve(key)
    first.observe(BackendResponse(text="broken"))
    first.reject_format(key, [])
    with pytest.raises(BackendError) as error:
        run.advance(key, first.route_id, malformed())
    assert error.value.code == "call_budget_exhausted"
    assert run.state.generation_attempts[0].error_code == "invalid_response"
    assert len(run.state.generation_attempts) == 1
    validate_automatic_state(run.state)
    resumed = session(tmp_path / "second", resume=tmp_path / "first" / "diagnostics", budget=2)
    assert resumed.route_for(key).role == "fallback"
    complete_one(resumed, key, model="backup-v1")
    assert resumed.state.calls_used == 2

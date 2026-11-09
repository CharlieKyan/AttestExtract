"""Frozen dev19 compatibility and transport-only recovery contracts."""

import hashlib
import json
import shutil
from pathlib import Path

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.decision_bundle import prepare_decision_bundle
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError
from attestextract.execution import digest, prepare_execution
from attestextract.models import GenerationOptions, TaskSpec
from attestextract.pipeline import _validate_cached_responses


FIXTURE = Path(__file__).parent / "fixtures/dev19_v2_failed"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def backend(*, calls, trace=None, fail_at=None, **settings):
    def endpoint(request):
        calls.append(json.loads(request.content))
        if len(calls) == fail_at:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"model": "synthetic", "choices": [
            {"message": {"content": '{"records":[]}'}, "finish_reason": "stop"}]})
    return OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic", trace=trace,
        structured_output="json_object", transport=httpx.MockTransport(endpoint), **settings)


def fixture_session(path, *, calls, **overrides):
    inputs = read(FIXTURE / "inputs.json")
    instance = backend(calls=calls, timeout=180, read_timeout=240, max_attempts=1, tls_mode="auto_fallback")
    instance._legacy_cli_config = inputs["config"]
    args = dict(text=inputs["text"], task=TaskSpec.model_validate(inputs["task"]), backend=instance,
                generation=GenerationOptions(), options=ExecutionOptions(**inputs["execution"]),
                max_chars=inputs["max_chunk_chars"], resume_from=path)
    args.update(overrides)
    session = prepare_execution(**args)
    _validate_cached_responses(args["task"], session)
    return session


def test_frozen_dev19_fixture_offline_migrates_without_writes_or_network():
    before = hashes(FIXTURE)
    calls = []
    session = fixture_session(FIXTURE, calls=calls)
    assert calls == [] and hashes(FIXTURE) == before
    assert len(session.cached) == 2 and len(session.leaves()) == 3
    assert session.state.calls_used == 3
    assert session.state.checkpoint_version == "3"
    assert session.state.migrations[0]["from_package_version"] == "0.1.0.dev19"
    assert session.state.transport_changes == [{
        "from": {"timeout": 60.0, "read_timeout": None, "tls_mode": "1.2", "max_attempts": 1},
        "to": {"timeout": 180, "read_timeout": 240, "tls_mode": "auto_fallback", "max_attempts": 1}}]


def test_frozen_dev19_python_identity_migrates(tmp_path):
    directory = tmp_path / "legacy"
    shutil.copytree(FIXTURE, directory)
    shutil.copyfile(FIXTURE / "python-checkpoint.json", directory / "execution/checkpoint.json")
    for path in (FIXTURE / "python-cache").glob("*.json"):
        shutil.copyfile(path, directory / "execution/cache" / path.name)
    calls = []
    session = fixture_session(directory, calls=calls, backend=backend(calls=calls, read_timeout=180))
    assert len(session.cached) == 2 and calls == []


@pytest.mark.parametrize("changed", ["source", "model", "endpoint", "generation", "context"])
def test_legacy_semantic_changes_are_rejected_before_network(changed):
    from attestextract.models import ContextOptions
    calls = []
    changes = {}
    if changed == "source":
        changes["text"] = "Different synthetic source."
    elif changed == "generation":
        changes["generation"] = GenerationOptions(temperature=0.75)
    elif changed == "context":
        changes["context"] = ContextOptions(mode="neighbors")
    else:
        instance = backend(calls=calls)
        instance._legacy_cli_config = read(FIXTURE / "inputs.json")["config"]
        key = "base_url" if changed == "endpoint" else "model"
        instance._legacy_cli_config[key] = "https://other.invalid/v1" if changed == "endpoint" else "different"
        changes["backend"] = instance
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        fixture_session(FIXTURE, calls=calls, **changes)
    assert calls == []


@pytest.mark.parametrize("artifact", ["manifest.json", "backend.json", "requests/chunk-0001.request.json",
                                      "schema/chunk-0001.request.json", "execution/cache/chunk-0002.json"])
def test_legacy_missing_artifact_rejected(tmp_path, artifact):
    directory = tmp_path / "legacy"
    shutil.copytree(FIXTURE, directory)
    (directory / artifact).unlink()
    calls = []
    with pytest.raises(ConfigurationError):
        fixture_session(directory, calls=calls)
    assert calls == []


@pytest.mark.parametrize("artifact", ["requests/chunk-0001.request.json", "schema/chunk-0001.request.json"])
def test_legacy_prompt_or_contract_change_rejected(tmp_path, artifact):
    directory = tmp_path / "legacy"
    shutil.copytree(FIXTURE, directory)
    (directory / artifact).write_text('{}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="incompatible"):
        fixture_session(directory, calls=[])


def test_legacy_unknown_version_has_no_override(tmp_path):
    directory = tmp_path / "legacy"
    shutil.copytree(FIXTURE, directory)
    manifest = read(directory / "manifest.json")
    manifest["package_version"] = "0.1.0.dev18"
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Only dev19"):
        fixture_session(directory, calls=[])


@pytest.mark.parametrize("name", ["manifest.json", "backend.json"])
def test_malformed_legacy_metadata_has_clear_configuration_error(tmp_path, name):
    directory = tmp_path / "legacy"
    shutil.copytree(FIXTURE, directory)
    (directory / name).write_text('[]',encoding="utf-8")
    with pytest.raises(ConfigurationError, match="incompatible"):
        fixture_session(directory, calls=[])


def test_frozen_legacy_resume_requests_only_remaining_leaf_and_exports_decisions(tmp_path):
    inputs = read(FIXTURE / "inputs.json")
    calls = []
    trace = RunTrace(tmp_path / "new/diagnostics")
    instance = backend(calls=calls, trace=trace, read_timeout=180, max_attempts=1)
    instance._legacy_cli_config = inputs["config"]
    before = hashes(FIXTURE)
    result = extract(inputs["text"], inputs["task"], backend=instance, trace=trace,
        max_chunk_chars=18, execution=ExecutionOptions(adaptive_chunking=False,min_chunk_chars=1,max_model_calls=4),
        resume_from=FIXTURE)
    trace.finish(result.model_dump(mode="json"))
    assert len(calls) == 1 and "Item C" in calls[0]["messages"][-1]["content"]
    assert hashes(FIXTURE) == before
    state = read(trace.directory / "execution/checkpoint.json")["state"]
    assert state["calls_used"] == 4 and state["checkpoint_version"] == "3"
    assert len(prepare_decision_bundle(tmp_path / "new").batches) == 3


@pytest.mark.parametrize("resumed_tls", ["1.2", "tls12_fallback"])
def test_sixteen_chunks_timeout_resume_reuses_eleven_with_five_call_budget(tmp_path, monkeypatch, resumed_tls):
    import attestextract
    text = "\n\n".join(f"Item {n:02} reads 11." for n in range(16))
    task = read(FIXTURE / "inputs.json")["task"]
    first, second = tmp_path / "first", tmp_path / "second"
    trace = RunTrace(first)
    calls = []
    monkeypatch.setattr(attestextract, "__version__", "0.1.0.dev23")
    with pytest.raises(BackendError) as failure:
        extract(text, task, backend=backend(calls=calls,trace=trace,fail_at=12,timeout=60,max_attempts=3,tls_mode="auto"),
            trace=trace,max_chunk_chars=19,
            execution=ExecutionOptions(adaptive_chunking=False,min_chunk_chars=1,max_model_calls=48))
    assert failure.value.code == "timeout" and len(calls) == 12
    previous = hashes(first)
    state = read(first / "execution/checkpoint.json")["state"]
    assert len(state["nodes"]) == 16 and sum(n["status"] == "completed" for n in state["nodes"].values()) == 11
    trace2 = RunTrace(second)
    more = []
    monkeypatch.setattr(attestextract,"__version__","0.1.0.dev999")
    result = extract(text, task, backend=backend(calls=more,trace=trace2,read_timeout=180,tls_mode=resumed_tls,max_attempts=1),
        trace=trace2,max_chunk_chars=19,resume_from=first,
        execution=ExecutionOptions(adaptive_chunking=False,min_chunk_chars=1,max_model_calls=17))
    assert len(more) == 5 and result.run.chunk_count == 16
    summary = read(second / "execution/summary.json")
    assert summary["calls_reserved"] == 17 and summary["reused_chunks"] == 11
    assert summary["completed_chunks"] == 16 and len(summary["transport_changes"]) == 1
    assert summary["transport_changes"][0]["to"]["tls_mode"] == resumed_tls
    assert hashes(first) == previous


@pytest.mark.parametrize("damage", ["model", "endpoint", "cache"])
def test_tls_first_resume_keeps_identity_and_cache_guards(tmp_path, damage):
    text = "\n\n".join(f"Item {letter} reads 11." for letter in "ABC")
    task = read(FIXTURE / "inputs.json")["task"]
    first = tmp_path / "first"
    trace = RunTrace(first)
    with pytest.raises(BackendError):
        extract(text, task, backend=backend(calls=[], trace=trace, fail_at=3, tls_mode="auto", max_attempts=1),
                trace=trace, max_chunk_chars=19,
                execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1, max_model_calls=4))
    calls = []
    resumed = backend(calls=calls, tls_mode="tls12_fallback", max_attempts=1)
    if damage == "model":
        resumed.model = "different-synthetic"
    elif damage == "endpoint":
        resumed.base_url = "https://different.invalid/v1"
    else:
        cache = next((first / "execution/cache").glob("*.json"))
        cache.write_text("{}", encoding="utf-8")
    before = hashes(first)
    with pytest.raises(ConfigurationError):
        extract(text, task, backend=resumed, trace=RunTrace(tmp_path / "second"), resume_from=first,
                max_chunk_chars=19,
                execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1, max_model_calls=4))
    assert calls == [] and hashes(first) == before


def test_successful_v2_decision_archive_remains_readable(tmp_path):
    trace = RunTrace(tmp_path / "diagnostics")
    inputs = read(FIXTURE / "inputs.json")
    result = extract(inputs["text"], inputs["task"], backend=backend(calls=[],trace=trace),trace=trace,
                     execution=ExecutionOptions())
    trace.finish(result.model_dump(mode="json"))
    checkpoint = trace.directory / "execution/checkpoint.json"
    envelope = read(checkpoint)
    for key in ("engine_revision", "package_version", "transport_settings", "transport_changes", "migrations"):
        envelope["state"].pop(key)
    envelope["state"]["checkpoint_version"] = "2"
    envelope["sha256"] = digest(envelope["state"])
    checkpoint.write_text(json.dumps(envelope),encoding="utf-8")
    assert len(prepare_decision_bundle(tmp_path).batches) == 1

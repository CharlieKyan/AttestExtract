"""Minimal public entry points with an unregistered synthetic service."""

import json

import httpx
import pytest

from attestextract import ExecutionOptions, ModelConnection, RunTrace, extract
from attestextract.errors import BackendError, ConfigurationError


SOURCE = "Lamp A uses 8 W."
TASK = {"name": "power", "record_types": {"power": {"fields": {
    "value": {"type": "integer", "required": True},
}}}}
CONTENT = '{"records":[{"record_type":"power","values":{"value":8},"quotes":{"value":"8"}}]}'


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)


def envelope(content=CONTENT):
    return httpx.Response(200, json={
        "model": "reported-synthetic-revision", "id": "synthetic-request",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    })


def install_http(monkeypatch, handler):
    original = httpx.Client
    requests = []

    def dispatch(request):
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(401)
        requests.append(request)
        return handler(request)

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return requests


def call(**kwargs):
    return extract(SOURCE, TASK, base_url="https://unregistered.example/v1",
                   model="unlisted:research-model", api_key="synthetic-primary", **kwargs)


def test_unknown_model_uses_public_entry_without_preset_or_probe_generation(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unrelated-secret")
    monkeypatch.setattr("attestextract.providers.resolve_model_config", lambda *a, **kw: pytest.fail("preset used"))
    requests = install_http(monkeypatch, lambda request: envelope())
    result = call()
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://unregistered.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer synthetic-primary"
    assert payload["model"] == "unlisted:research-model"
    assert payload["max_tokens"] == 8192
    assert payload["response_format"]["type"] == "json_schema"
    assert not ({"temperature", "thinking", "enable_thinking"} & payload.keys())
    assert json.loads(payload["messages"][-1]["content"])["source"] == SOURCE
    assert result.schema_version == "0.5"
    assert result.run.model is None
    assert len(result.run.generation_attempts) == 1
    assert result.run.generation_attempts[0].status == "completed"
    field = result.candidates[0].fields["value"]
    assert field.value == 8
    assert SOURCE[field.evidence.start:field.evidence.end] == "8"
    assert "unregistered.example" not in json.dumps(result.model_dump())


def test_explicit_schema_rejection_changes_mode_preserves_original_logs(monkeypatch, tmp_path):
    def respond(request):
        if json.loads(request.content)["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"code": "unsupported_value", "param": "response_format",
                                                      "message": "json_schema is not supported"}})
        return envelope()

    requests = install_http(monkeypatch, respond)
    trace = RunTrace(tmp_path / "diagnostics")
    result = call(trace=trace)
    assert len(requests) == 2
    assert [json.loads(r.content)["response_format"]["type"] for r in requests] == ["json_schema", "json_object"]
    assert len(result.run.generation_attempts) == 2
    assert result.run.request_attempts == 2
    assert len(list((trace.directory / "requests").glob("*.request.json"))) == 2
    state = json.loads((trace.directory / "execution/checkpoint.json").read_text())["state"]
    assert state["checkpoint_version"] == "4" and state["calls_used"] == 2
    assert state["max_model_calls"] == 3
    for path in trace.directory.rglob("*"):
        if path.is_file():
            assert "synthetic-primary" not in path.read_text(encoding="utf-8")


def test_format_failure_can_use_explicit_backup_and_records_both_accounts(monkeypatch):
    requests = install_http(monkeypatch, lambda request: envelope("{bad") if request.url.host == "unregistered.example" else envelope())
    result = call(fallback=ModelConnection(base_url="https://backup.example/v1", model="other-model",
                                          api_key="synthetic-backup"))
    assert [r.headers["authorization"] for r in requests] == ["Bearer synthetic-primary", "Bearer synthetic-backup"]
    assert [json.loads(r.content)["response_format"]["type"] for r in requests] == ["json_schema", "json_schema"]
    assert [item.status for item in result.run.generation_attempts] == ["rejected", "completed"]
    source = result.run.chunk_provenance["chunk-0001"]
    assert result.run.routes[source.route_id]["role"] == "fallback"
    assert source.reported_model == "reported-synthetic-revision"


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError])
def test_unknown_outcome_stops_without_backup(monkeypatch, tmp_path, error):
    def respond(request):
        raise error("synthetic failure", request=request)

    requests = install_http(monkeypatch, respond)
    trace = RunTrace(tmp_path / "diagnostics")
    with pytest.raises(BackendError):
        call(trace=trace, fallback=ModelConnection(base_url="https://backup.example/v1", model="backup",
                                                 api_key="synthetic-backup"))
    assert len(requests) == 1
    state = json.loads((trace.directory / "execution/checkpoint.json").read_text())["state"]
    assert state["calls_used"] == 1
    assert state["generation_attempts"][0]["outcome_unknown"]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_unknown_parameter_auth_and_permission_errors_do_not_fallback(monkeypatch, status):
    requests = install_http(monkeypatch, lambda request: httpx.Response(status, json={"error": {"message": "unclassified error"}}))
    with pytest.raises(BackendError):
        call(fallback=ModelConnection(base_url="https://backup.example/v1", model="backup", api_key="synthetic-backup"))
    assert len(requests) == 1


def test_explicit_budget_prevents_additional_compatibility_request(monkeypatch):
    requests = install_http(monkeypatch, lambda request: httpx.Response(400, json={"error": {
        "code": "unsupported_value", "param": "response_format", "message": "json_schema is not supported"}}))
    with pytest.raises(BackendError, match="budget"):
        call(execution=ExecutionOptions(max_model_calls=1))
    assert len(requests) == 1


def test_raw_model_requires_endpoint(monkeypatch):
    with pytest.raises(ConfigurationError):
        extract(SOURCE, TASK, model="new-model", api_key="synthetic-primary")


def test_advanced_connection_options_respect_explicit_argument_priority(monkeypatch):
    monkeypatch.setenv("MY_MODEL_KEY", "synthetic-option-key")
    requests = install_http(monkeypatch, lambda request: envelope())
    extract(SOURCE, TASK, model="unlisted-model", model_options={
        "base_url": "https://options.example/v1", "api_key_env": "MY_MODEL_KEY"})
    assert requests[0].url.host == "options.example"
    assert requests[0].headers["authorization"] == "Bearer synthetic-option-key"
    extract(SOURCE, TASK, model="unlisted-model", base_url="https://explicit.example/v1",
            api_key="synthetic-explicit", model_options={"base_url": "https://ignored.example/v1",
                                                          "api_key_env": "MY_MODEL_KEY"})
    assert requests[1].url.host == "explicit.example"
    assert requests[1].headers["authorization"] == "Bearer synthetic-explicit"


def test_raw_connection_rejects_region_and_empty_credential_binding():
    with pytest.raises(ConfigurationError, match="region"):
        call(region="beijing")
    with pytest.raises(ConfigurationError):
        call(api_key_env="")


def test_automatic_result_cannot_bypass_source_validation_by_downgrading_schema(monkeypatch):
    from attestextract import ExtractionResult
    install_http(monkeypatch, lambda request: envelope())
    payload = call().model_dump(mode="json")
    payload["schema_version"] = "0.4"
    with pytest.raises(ValueError, match="requires result schema 0.5"):
        ExtractionResult.model_validate(payload)


def test_cli_minimal_inputs_and_offline_preview(monkeypatch, tmp_path, capsys):
    from attestextract.cli import main

    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text(SOURCE, encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    args = ["extract", "--base-url", "https://unregistered.example/v1", "--model", "unlisted-model",
            "--input", str(source), "--task", str(task), "--progress", "quiet"]
    requests = install_http(monkeypatch, lambda request: envelope())
    assert main([*args, "--check"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["network_calls"] == 0 and requests == []
    assert preview["execution"]["max_model_calls"] == preview["chunk_count"] + 2
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-primary")
    assert main(args) == 0
    assert len(requests) == 1
    results = list((tmp_path / "outputs").glob("*/result.json"))
    assert len(results) == 1
    assert json.loads(results[0].read_text())["schema_version"] == "0.5"


def test_public_resume_reuses_eleven_chunks_and_only_calls_remaining_five(monkeypatch, tmp_path):
    source = "\n".join(f"Unit {number:02d} uses 8 W." for number in range(16))
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        if count == 12:
            raise httpx.ReadTimeout("synthetic read timeout", request=request)
        return envelope()

    requests = install_http(monkeypatch, respond)
    options = ExecutionOptions(adaptive_chunking=False, max_split_depth=0, max_model_calls=17)
    first = RunTrace(tmp_path / "first")
    args = dict(base_url="https://unregistered.example/v1", model="new-model", api_key="synthetic-primary",
                max_chunk_chars=18, execution=options)
    with pytest.raises(BackendError):
        extract(source, TASK, trace=first, **args)
    checkpoint = first.directory / "execution/checkpoint.json"
    state = json.loads(checkpoint.read_text())["state"]
    assert len(state["roots"]) == 16
    assert sum(node["status"] == "completed" for node in state["nodes"].values()) == 11
    original = checkpoint.read_bytes()
    result = extract(source, TASK, trace=RunTrace(tmp_path / "resumed"), resume_from=checkpoint,
                     model_options={"read_timeout": 240}, **args)
    assert len(requests) == 17
    assert result.run.chunk_count == 16
    assert len(result.run.generation_attempts) == 17
    assert len(result.run.chunk_provenance) == 16
    assert checkpoint.read_bytes() == original


def test_auto_explicit_format_regeneration_is_counted_and_not_hidden(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return envelope("{bad" if len(requests) == 1 else CONTENT)

    install_http(monkeypatch, respond)
    result = call(format_retries=1)
    assert len(requests) == 2
    assert [a.format_attempt for a in result.run.generation_attempts] == [1, 2]
    assert len({a.route_id for a in result.run.generation_attempts}) == 1


@pytest.mark.parametrize("safe_input", [True, False])
def test_cli_hidden_key_never_falls_back_to_echo(monkeypatch, tmp_path, capsys, safe_input):
    import getpass
    import sys
    import warnings
    from attestextract.cli import main

    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text(SOURCE, encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def hidden(prompt):
        if not safe_input:
            warnings.warn("Cannot control echo on the terminal.", getpass.GetPassWarning)
        return "synthetic-hidden-secret"

    monkeypatch.setattr(getpass, "getpass", hidden)
    requests = install_http(monkeypatch, lambda request: envelope())
    status = main(["extract", "--base-url", "https://unregistered.example/v1", "--model", "new-model",
                   "--input", str(source), "--task", str(task)])
    assert status == (0 if safe_input else 1)
    assert len(requests) == (1 if safe_input else 0)
    captured = capsys.readouterr()
    assert "synthetic-hidden-secret" not in captured.out + captured.err

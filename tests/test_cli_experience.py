"""Synthetic acceptance of minimal CLI, strict JSON failures and safe TLS defaults."""

import io
import json
import ssl
import threading
from pathlib import Path

import httpx
import pytest

from attestextract import cli, extract
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, InvalidResponseError
from attestextract.models import ExtractionRequest
from attestextract.progress import ConsoleProgress
from attestextract.providers import resolve_model_config
from attestextract.transport import tls_context


TASK = {"name": "Synthetic lamp", "record_types": {"power": {
    "fields": {"value": {"type": "integer", "required": True}, "unit": {"type": "string"}},
}}}
VALID = '{"records":[{"record_type":"power","values":{"value":8,"unit":"W"},"quotes":{"value":"8","unit":"W"}}]}'
INVALID = '{"records":[{"record_type":"power","values":{"value":8,{"unit":"W"}},"quotes":{"value":"8","unit":"W"}}]}'


def arguments(tmp_path):
    source, task, env = (tmp_path / name for name in ("source.md", "task.json", ".env"))
    source.write_text("Lamp A uses 8 W.", encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    env.write_text("DEEPSEEK_API_KEY=synthetic-key\nVLM_API_KEY=synthetic-qwen\n", encoding="utf-8")
    return ["extract", "--input", str(source), "--task", str(task), "--env-file", str(env),
            "--model-id", "deepseek:deepseek-flash"]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            monkeypatch.delenv(key)


def intercept(monkeypatch, handler):
    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kwargs:
                        OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(handler)))


def response(content=VALID):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 37, "completion_tokens": 24, "total_tokens": 61}})


def test_minimal_cli_creates_unique_results_report_logs_and_progress(tmp_path, monkeypatch, capsys):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["max_tokens"] == 8192
        assert body["temperature"] == 0
        return response()
    intercept(monkeypatch, handler)
    args = arguments(tmp_path)
    for _ in range(2):
        assert cli.main(args) == 0
    runs = list((tmp_path / "outputs").iterdir())
    assert len(runs) == len(calls) == 2
    for run in runs:
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        assert result["candidates"][0]["fields"]["value"]["value"] == 8
        assert (run / "report.md").exists()
        assert (run / "diagnostics/responses/chunk-0001.txt").read_text(encoding="utf-8") == VALID
    stderr = capsys.readouterr().err
    assert "[1/1] requesting model" in stderr
    assert "tokens in/out: 37/24" in stderr
    assert "1 candidates" in stderr and "Completed:" in stderr
    assert "Lamp A" not in stderr and "synthetic-key" not in stderr
    assert "field_checked" not in stderr


def test_check_is_network_free_and_marks_checked_not_extracted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("--check made a network call"))
    assert cli.main(arguments(tmp_path) + ["--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["chunk_count"] == 1 and plan["network_calls"] == 0
    assert plan["max_attempts"] == 1 and plan["format_retries"] == 0
    run = next((tmp_path / "outputs").iterdir())
    assert not (run / "result.json").exists()
    assert json.loads((run / "diagnostics/manifest.json").read_text())["status"] == "checked"


@pytest.mark.parametrize("folder", ["inputs", "Inputs"])
def test_default_outputs_are_sibling_of_inputs_and_preserve_previous_runs(tmp_path, monkeypatch, folder):
    source_dir = tmp_path / folder
    source_dir.mkdir()
    legacy = source_dir / "attestextract-runs" / "previous"
    legacy.mkdir(parents=True)
    original = legacy / "keep.txt"
    original.write_text("synthetic previous evidence", encoding="utf-8")
    intercept(monkeypatch, lambda _: response())
    assert cli.main(arguments(source_dir)) == 0
    results = list((tmp_path / "outputs").glob("*/result.json"))
    assert len(results) == 1
    assert not (source_dir / "outputs").exists()
    assert original.read_text(encoding="utf-8") == "synthetic previous evidence"
    assert (results[0].parent / "report.md").is_file()


@pytest.mark.parametrize("selector", ["deepseek:deepseek-flash", "qwen:qwen3.8-27b"])
def test_provider_token_limit_omits_both_wire_parameters_even_on_format_retry(tmp_path, monkeypatch, selector):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and "max_completion_tokens" not in body
        assert body["temperature"] == 0
        calls.append(body)
        return response(INVALID if len(calls) == 1 else VALID)
    intercept(monkeypatch, handler)
    args = arguments(tmp_path)
    args[-1] = selector
    assert cli.main(args + ["--max-output-tokens", "provider", "--format-retries", "1"]) == 0
    assert len(calls) == 2
    run = next((tmp_path / "outputs").iterdir())
    config = json.loads((run / "diagnostics/backend.json").read_text(encoding="utf-8"))
    assert config["generation"]["max_output_tokens"] is None


def test_provider_token_limit_clears_profile_limit_without_losing_other_controls(tmp_path, monkeypatch):
    profile = tmp_path / "profiles.toml"
    profile.write_text('[profiles.test]\nbase_url="https://synthetic.invalid/v1"\nmodel="synthetic"\n'
                       'output_token_parameter="max_completion_tokens"\n'
                       '[profiles.test.generation]\nmax_output_tokens=80\ntemperature=0.25\n', encoding="utf-8")
    calls = []
    def handler(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and "max_completion_tokens" not in body
        assert body["temperature"] == 0.25
        calls.append(body)
        return response()
    intercept(monkeypatch, handler)
    args = arguments(tmp_path)[:-2]  # Advanced profile instead of model preset.
    assert cli.main(args + ["--config", str(profile), "--profile", "test", "--max-output-tokens", "provider"]) == 0
    assert len(calls) == 1


def test_provider_limit_check_is_explicit_and_network_free(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("--check made a network call"))
    assert cli.main(arguments(tmp_path) + ["--max-output-tokens", "provider", "--check"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["generation"]["max_output_tokens"] is None
    assert "provider default" in captured.err


@pytest.mark.parametrize("value", ["0", "-1", "unlimited", "8.5"])
def test_invalid_token_limit_rejected_before_any_network_or_output(tmp_path, monkeypatch, value):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Invalid arguments must not call a model"))
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments(tmp_path) + ["--max-output-tokens", value])
    assert caught.value.code == 2
    assert not (tmp_path / "outputs").exists()


def test_explicit_output_needs_no_trace_for_progress_and_quiet_is_quiet(tmp_path, monkeypatch, capsys):
    intercept(monkeypatch, lambda _: response())
    args = arguments(tmp_path) + ["--output", str(tmp_path / "output.json")]
    assert cli.main(args) == 0
    assert "[1/1]" in capsys.readouterr().err
    assert not (tmp_path / "outputs").exists()
    assert cli.main(args + ["--progress", "quiet"]) == 0
    assert capsys.readouterr().err == ""


def test_invalid_json_is_preserved_located_with_explicit_retry_opt_out(tmp_path, monkeypatch, capsys):
    calls = []
    intercept(monkeypatch, lambda request: (calls.append(request), response(INVALID))[1])
    assert cli.main(arguments(tmp_path) + ["--format-retries", "0"]) == 1
    assert len(calls) == 1
    run = next((tmp_path / "outputs").iterdir())
    assert not (run / "result.json").exists()
    assert (run / "diagnostics/responses/chunk-0001.txt").read_text() == INVALID
    error = json.loads((run / "diagnostics/error.json").read_text())
    first = error["first_error"]
    assert INVALID[first["raw_offset"]] == "{"
    assert first["message"] == "Expecting property name enclosed in double quotes"
    assert first["raw_column"] == INVALID.index(',{"unit"') + 2
    terminal = capsys.readouterr().err
    assert "Original model output:" in terminal and "column" in terminal
    assert INVALID not in terminal


def test_explicit_format_retry_uses_reason_and_retains_both_responses(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 2:
            system = body["messages"][0]["content"]
            assert "Expecting property name enclosed in double quotes" in system
            assert '"line": 1' in system and INVALID not in system
        return response(INVALID if len(calls) == 1 else VALID)
    intercept(monkeypatch, handler)
    assert cli.main(arguments(tmp_path) + ["--format-retries", "1"]) == 0
    assert len(calls) == 2
    run = next((tmp_path / "outputs").iterdir())
    assert (run / "diagnostics/responses/chunk-0001.txt").read_text() == INVALID
    assert (run / "diagnostics/responses/chunk-0001.format-2.txt").read_text() == VALID
    result = json.loads((run / "result.json").read_text())
    assert result["run"]["response_count"] == 2
    assert result["run"]["usage"]["total_tokens"] == 122


def test_json_raw_coordinates_include_fences_unicode_and_leading_whitespace(tmp_path):
    raw = " \r\n```json\n" + INVALID.replace('"8"', '"🙂8"') + "\n```\n"
    with pytest.raises(InvalidResponseError) as caught:
        extract("Lamp A uses 8 W.", TASK, backend=ReplayBackend([raw]))
    error = caught.value.validation_details["errors"][0]
    assert error["raw_line"] == 3 and error["line"] == 1
    assert raw[error["raw_offset"]:][:8] == '{"unit":'
    assert error["raw_column"] == error["column"]


def test_terminal_coordinates_point_to_original_fenced_response(tmp_path, monkeypatch, capsys):
    raw = " \n```json\n" + INVALID + "\n```"
    intercept(monkeypatch, lambda _: response(raw))
    assert cli.main(arguments(tmp_path)) == 1
    terminal = capsys.readouterr().err
    assert "original response line 3, column" in terminal
    assert "unfenced JSON line 1, column" in terminal


def test_report_write_failure_does_not_claim_result_was_never_written(tmp_path, monkeypatch, capsys):
    intercept(monkeypatch, lambda _: response())
    original = Path.write_bytes
    def write(path, content):
        if path.name == "report.md":
            raise OSError("Synthetic report write failure")
        return original(path, content)
    monkeypatch.setattr(Path, "write_bytes", write)
    assert cli.main(arguments(tmp_path)) == 1
    run = next((tmp_path / "outputs").iterdir())
    assert (run / "result.json").is_file()
    terminal = capsys.readouterr().err
    assert "run did not complete" in terminal
    assert "no successful result was written" not in terminal


def test_console_heartbeat_stops_and_does_not_echo_payload():
    ready = threading.Event()
    class Stream(io.StringIO):
        def write(self, value):
            result = super().write(value)
            if "waiting for model" in value:
                ready.set()
            return result
    stream = Stream()
    console = ConsoleProgress(stream=stream, heartbeat_seconds=0.01)
    try:
        console({"stage": "chunking", "event": "completed", "chunk_count": 1})
        console({"stage": "model_request", "event": "started", "chunk_id": "chunk-0001", "format_attempt": 1,
                 "source": "private-example"})
        assert ready.wait(2)
    finally:
        console.close()
    assert not console._thread.is_alive()
    assert "private-example" not in stream.getvalue()


def test_tls_compatibility_verifies_certificates_and_is_provider_scoped(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    context = tls_context("1.2")
    assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert tls_context("auto") is True
    assert resolve_model_config("deepseek:deepseek-flash", environment={}).tls_mode == "1.2"
    assert resolve_model_config("qwen:qwen3.8-27b", environment={}).tls_mode == "auto"
    assert resolve_model_config("deepseek:deepseek-flash", environment={}, overrides={"tls_mode": "auto"}).tls_mode == "auto"


def test_transport_causes_survive_without_exception_strings_or_extra_calls(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        try:
            raise ssl.SSLError(1, "private-key private-host")
        except ssl.SSLError as exc:
            raise httpx.ReadError("private-key private-host") from exc
    trace = RunTrace(tmp_path / "trace")
    backend = OpenAICompatibleBackend("https://example.invalid", "synthetic", max_attempts=1,
                                      tls_mode="1.2", transport=httpx.MockTransport(handler), trace=trace)
    with pytest.raises(BackendError) as caught:
        backend.generate(ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "synthetic"}]))
    assert len(calls) == 1
    details = caught.value.http_diagnostics
    assert [cause["type"] for cause in details["causes"]] == ["ReadError", "SSLError"]
    assert details["causes"][1]["errno"] == 1
    logged = (trace.directory / caught.value.http_details).read_text()
    assert "private-key" not in logged and "private-host" not in logged


def test_event_only_trace_keeps_diagnostics_without_nonexistent_file_references():
    events = []
    trace = RunTrace(None, on_event=events.append)
    with pytest.raises(InvalidResponseError) as caught:
        extract("Lamp A uses 8 W.", TASK, backend=ReplayBackend([INVALID]), trace=trace)
    assert caught.value.validation_artifact is None
    assert caught.value.validation_details["phase"] == "json_syntax"
    rejected = next(e for e in events if e["event"] == "rejected")
    assert rejected["details"] is None
    backend = OpenAICompatibleBackend("https://example.invalid", "synthetic", trace=trace,
        transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": {"message": "synthetic"}})))
    with pytest.raises(BackendError) as caught:
        backend.generate(ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "synthetic"}]))
    assert caught.value.http_details is None and caught.value.http_diagnostics is not None
    assert next(e for e in events if e["event"] == "error_reported")["artifact"] is None

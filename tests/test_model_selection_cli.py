"""The minimal CLI switches model selectors without profile files or live calls."""

import json

import httpx
import pytest

from attestextract import cli
from attestextract.backends import OpenAICompatibleBackend


TEXT = "Lamp A uses 8 W."
TASK = {"name": "synthetic-power", "record_types": {"power": {
    "fields": {"value": {"type": "integer", "required": True}},
}}}
RESPONSE = '{"records":[{"record_type":"power","values":{"value":8},"quotes":{"value":"8"}}]}'


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            monkeypatch.delenv(key)


def inputs(tmp_path):
    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text(TEXT, encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    return ["extract", "--input", str(source), "--task", str(task)]


def intercept(monkeypatch, handler):
    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kwargs:
                        OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(handler)))


def completion():
    return httpx.Response(200, json={"choices": [{"message": {"content": RESPONSE}, "finish_reason": "stop"}]})


def test_switching_only_selector_routes_model_key_and_parameters(tmp_path, monkeypatch):
    env = tmp_path / "keys.env"
    env.write_text("DEEPSEEK_API_KEY=synthetic-deepseek\nVLM_API_KEY=synthetic-qwen\n"
                   "VLM_BASE_URL=https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1\n"
                   "ATTESTEXTRACT_API_KEY=stale-key\nATTESTEXTRACT_BASE_URL=https://stale.invalid/v1\n"
                   "ATTESTEXTRACT_MODEL=stale-model\n", encoding="utf-8")
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.host, request.headers["authorization"], body))
        assert json.loads(body["messages"][-1]["content"])["source"] == TEXT
        return completion()

    intercept(monkeypatch, handler)
    common = inputs(tmp_path) + ["--env-file", str(env)]
    results = []
    for index, selector in enumerate(("deepseek:deepseek-flash", "qwen:qwen3.8-27b")):
        output = tmp_path / f"result-{index}.json"
        logs = tmp_path / f"logs-{index}"
        assert cli.main(common + ["--model-id", selector, "--output", str(output), "--log-dir", str(logs)]) == 0
        results.append(json.loads(output.read_text(encoding="utf-8")))
        meta = json.loads((logs / "backend.json").read_text(encoding="utf-8"))
        assert meta["model_id"] == selector and meta["selection_mode"] == "preset"
        assert meta["tls_mode"] == "tls12_fallback" and meta["max_attempts"] == 1
        logged = "".join(p.read_text(encoding="utf-8") for p in logs.rglob("*") if p.is_file())
        assert "synthetic-deepseek" not in logged and "synthetic-qwen" not in logged
    assert calls[0][0:2] == ("api.deepseek.com", "Bearer synthetic-deepseek")
    assert calls[1][0:2] == ("demo.cn-beijing.maas.aliyuncs.com", "Bearer synthetic-qwen")
    for (_, _, body), model in zip(calls, ("deepseek-flash", "qwen3.8-27b")):
        assert body["model"] == model and body["max_tokens"] == 8192
        assert body["response_format"] == {"type": "json_object"}
    assert calls[0][2]["thinking"] == {"type": "disabled"}
    assert calls[1][2]["enable_thinking"] is False and "thinking" not in calls[1][2]
    assert results[0]["task"] == results[1]["task"]
    assert results[0]["candidates"] == results[1]["candidates"]


def test_models_is_offline_and_needs_no_credentials(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Listing preset models must not contact a service")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert cli.main(["models"]) == 0
    text = capsys.readouterr().out
    assert "offline" in text and "deepseek:deepseek-flash" in text
    assert "qwen:qwen3.8-27b" in text and "qwen:qwen-plus" in text


def test_missing_key_fails_before_transport_with_an_action(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing credentials must fail before a model call")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    output = tmp_path / "result.json"
    assert cli.main(inputs(tmp_path) + ["--model-id", "deepseek:deepseek-flash", "--output", str(output)]) == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("extra", [
    ["--model", "raw-name"], ["--adapter", "openai-compatible"],
    ["--profile", "x"], ["--config", "missing.toml"],
])
def test_ambiguous_selection_fails_before_requests(tmp_path, monkeypatch, extra, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous selection must fail before a model call")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert cli.main(inputs(tmp_path) + ["--model-id", "deepseek:deepseek-flash",
                                       "--output", str(tmp_path / "result.json"), *extra]) == 1
    assert "cannot be combined" in capsys.readouterr().err


def test_legacy_colon_model_id_is_still_literal(tmp_path, monkeypatch):
    received = []
    intercept(monkeypatch, lambda request: received.append(json.loads(request.content)) or completion())
    assert cli.main(inputs(tmp_path) + ["--base-url", "https://synthetic.invalid/v1",
                    "--model", "deepseek:custom-old-route", "--structured-output", "off",
                    "--output", str(tmp_path / "result.json")]) == 0
    assert received[0]["model"] == "deepseek:custom-old-route"
    assert "response_format" not in received[0] and "thinking" not in received[0]


def test_explicit_parameters_override_preset_without_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    received = []
    intercept(monkeypatch, lambda request: received.append(json.loads(request.content)) or completion())
    assert cli.main(inputs(tmp_path) + ["--model-id", "deepseek:deepseek-flash",
                    "--structured-output", "off", "--max-output-tokens", "96", "--temperature", "0",
                    "--output", str(tmp_path / "result.json")]) == 0
    body = received[0]
    assert "response_format" not in body and body["temperature"] == 0 and body["max_tokens"] == 96


@pytest.mark.parametrize("tls_mode", ["auto", "1.2", "auto_fallback", "tls12_fallback"])
def test_cli_connection_overrides_beat_profile_without_network(tmp_path, monkeypatch, capsys, tls_mode):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Offline check contacted service"))
    profile = tmp_path / "profiles.toml"
    profile.write_text('[profiles.synthetic]\nbase_url="https://synthetic.invalid/v1"\nmodel="synthetic"\n'
                       'tls_mode="auto"\ntimeout=60\nread_timeout=240\nmax_attempts=3\n', encoding="utf-8")
    assert cli.main(inputs(tmp_path) + ["--config", str(profile), "--profile", "synthetic",
                    "--tls-mode", tls_mode, "--timeout", "90", "--max-attempts", "1", "--check"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["tls_mode"] == tls_mode and preview["max_attempts"] == 1
    assert preview["effective_timeouts"] == {"connect": 90, "read": 90, "write": 90, "pool": 90}
    assert preview["network_calls"] == 0


@pytest.mark.parametrize("selection", ["preset", "profile", "legacy"])
def test_omitted_attempt_limit_sends_once_for_retryable_http_error(tmp_path, monkeypatch, selection):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    calls = []
    intercept(monkeypatch, lambda request: calls.append(request) or httpx.Response(503))
    if selection == "preset":
        options = ["--model-id", "deepseek:deepseek-flash"]
    elif selection == "profile":
        profile = tmp_path / "profiles.toml"
        profile.write_text('[profiles.synthetic]\nbase_url="https://synthetic.invalid/v1"\nmodel="synthetic"\n', encoding="utf-8")
        options = ["--config", str(profile), "--profile", "synthetic"]
    else:
        options = ["--base-url", "https://synthetic.invalid/v1", "--model", "synthetic",
                   "--structured-output", "off"]  # Retain the legacy fixed/anonymous path.
    assert cli.main(inputs(tmp_path) + options) == 1
    assert len(calls) == 1
    run = next((tmp_path / "outputs").iterdir())
    saved = json.loads((run / "diagnostics/backend.json").read_text(encoding="utf-8"))
    assert saved["max_attempts"] == 1 and saved["tls_mode"] == "tls12_fallback"
    assert saved["format_retries"] == 0


@pytest.mark.parametrize("status,code,action", [
    (401, "authentication_error", "API key"), (402, "billing_error", "balance"),
    (403, "permission_denied", "access"), (404, "not_found", "model ID"),
    (400, "invalid_request", "parameters"),
])
def test_actionable_http_failures_keep_code_and_never_retry(tmp_path, monkeypatch, capsys, status, code, action):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    received = []
    intercept(monkeypatch, lambda request: received.append(request) or httpx.Response(
        status, json={"error": {"message": "private-provider-detail synthetic-secret"}}))
    logs = tmp_path / "logs"
    assert cli.main(inputs(tmp_path) + ["--model-id", "deepseek:deepseek-flash", "--max-attempts", "3",
                    "--log-dir", str(logs), "--output", str(tmp_path / "result.json")]) == 1
    assert len(received) == 1
    error = json.loads((logs / "error.json").read_text(encoding="utf-8"))
    assert error["code"] == code and not error["retryable"]
    message = capsys.readouterr().err
    assert action.lower() in message.lower()
    assert "synthetic-secret" not in message and "private-provider-detail" not in message

"""Format regeneration stays opt-in across CLI entry points, using synthetic HTTP."""

import json
import os

import httpx
import pytest

from attestextract import cli, extract
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.config import resolve_backend_config
from attestextract.errors import InvalidResponseError
from attestextract.providers import resolve_model_config


TASK = {"name": "Synthetic power", "record_types": {"power": {
    "fields": {"value": {"type": "integer", "required": True}},
}}}
VALID = '{"records":[{"record_type":"power","fields":{"value":{"value":8,"quote":"8"}}}]}'


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")


def arguments(tmp_path, selection="legacy", profile_retry=None):
    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text("Lamp A uses 8 W.", encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    args = ["extract", "--input", str(source), "--task", str(task)]
    if selection == "preset":
        return args + ["--model-id", "deepseek:deepseek-flash"]
    if selection == "profile":
        profile = tmp_path / "profiles.toml"
        text = '[profiles.test]\nbase_url="https://synthetic.invalid/v1"\nmodel="synthetic"\n'
        if profile_retry is not None:
            text += f"format_retries={profile_retry}\n"
        profile.write_text(text, encoding="utf-8")
        return args + ["--config", str(profile), "--profile", "test"]
    return args + ["--base-url", "https://synthetic.invalid/v1", "--model", "synthetic"]


@pytest.mark.parametrize("selection,profile_retry,cli_retry,expected", [
    ("preset", None, None, 0), ("preset", None, 0, 0), ("preset", None, 1, 1),
    ("legacy", None, None, 0), ("legacy", None, 0, 0), ("legacy", None, 1, 1),
    ("profile", None, None, 0), ("profile", 0, None, 0),
    ("profile", 1, None, 1), ("profile", 0, 1, 1), ("profile", 1, 0, 0),
])
def test_cli_default_is_lower_priority_than_explicit_configuration(
    tmp_path, monkeypatch, capsys, selection, profile_retry, cli_retry, expected,
):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Local check contacted a service"))
    args = arguments(tmp_path, selection, profile_retry)
    if cli_retry is not None:
        args += ["--format-retries", str(cli_retry)]
    assert cli.main(args + ["--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["format_retries"] == expected
    assert plan["network_calls"] == 0
    assert plan["structured_output_capability"]["server_enforcement"] == "not_verified"


@pytest.mark.parametrize("selection,profile_retry,cli_retry,calls_expected,status", [
    ("preset", None, None, 1, 1), ("legacy", None, None, 1, 1),
    ("profile", None, None, 1, 1), ("profile", 0, None, 1, 1),
    ("preset", None, 1, 2, 0), ("legacy", None, 1, 2, 0),
    ("profile", 1, None, 2, 0), ("profile", 1, 0, 1, 1),
])
def test_cli_regenerates_only_when_explicitly_enabled(
    tmp_path, monkeypatch, selection, profile_retry, cli_retry, calls_expected, status,
):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        content = 'malformed JSON' if len(bodies) == 1 else VALID
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kwargs:
                        OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(handler)))
    args = arguments(tmp_path, selection, profile_retry)
    if cli_retry is not None:
        args += ["--format-retries", str(cli_retry)]
    assert cli.main(args) == status
    assert len(bodies) == calls_expected
    run = next((tmp_path / "outputs").iterdir())
    assert (run / "result.json").exists() is (status == 0)
    assert (run / "diagnostics/responses/chunk-0001.txt").read_text(encoding="utf-8") == "malformed JSON"
    if calls_expected == 2:
        assert "previous output failed local format validation" in bodies[1]["messages"][0]["content"]
        assert (run / "diagnostics/responses/chunk-0001.format-2.txt").read_text(encoding="utf-8") == VALID
    else:
        assert not (run / "diagnostics/responses/chunk-0001.format-2.txt").exists()
        checkpoint = json.loads((run / "diagnostics/execution/checkpoint.json").read_text(encoding="utf-8"))["state"]
        assert checkpoint["calls_used"] == 1
        assert checkpoint["nodes"]["chunk-0001"]["format_retries_reserved"] == 0


def test_python_default_stays_opt_in():
    assert resolve_model_config("deepseek:deepseek-flash", environment={}).format_retries == 0
    assert resolve_backend_config(overrides={"base_url": "https://synthetic.invalid", "model": "test"},
                                  environment={}).format_retries == 0
    backend = ReplayBackend(["malformed JSON", VALID])
    with pytest.raises(InvalidResponseError):
        extract("Lamp A uses 8 W.", TASK, backend=backend)

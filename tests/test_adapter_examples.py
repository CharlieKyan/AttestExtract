"""A different protocol reaches the same extraction/CLI and retains D1 evidence."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from attestextract import create_archive, extract
from attestextract.adapter_examples import SyntheticEnvelopeBackend, synthetic_factory
from attestextract.config import BackendConfig
from attestextract.documents import Document
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import ExtractionRequest, GenerationOptions, TaskSpec
from attestextract.registry import default_registry


SCRIPT = Path(__file__).parents[1] / "scripts" / "d2_acceptance.py"
spec = importlib.util.spec_from_file_location("d2_acceptance", SCRIPT)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def request():
    return ExtractionRequest(chunk_id="chunk-0001", messages=[
        {"role": "system", "content": "Return JSON."}, {"role": "user", "content": "Synthetic source."},
    ], generation=GenerationOptions(temperature=0.2, max_output_tokens=512))


def test_prepare_translates_native_protocol_without_io_consumption_or_mutation():
    native = acceptance.mock_envelope("attribute")
    backend = SyntheticEnvelopeBackend([native], model="deployment-a")
    incoming = request()
    before = incoming.model_dump()
    expected = {"deployment": "deployment-a", "input": {
        "system": "Return JSON.", "prompt": "Synthetic source.",
    }, "parameters": {"temperature": 0.2, "output_limit": 512}}
    assert backend.prepare_payload(incoming) == expected
    preview = backend.prepare_payload(incoming)
    preview["input"]["system"] = "changed"
    assert incoming.model_dump() == before and backend.requests == []
    response = backend.generate(incoming)
    assert backend.requests == [expected]
    assert response.text == native["output"]["text"]
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert response.finish_reason == "stop" and response.request_id == "synthetic-request"
    assert response.reported_model == response.model == "synthetic-observed-model"
    assert native == acceptance.mock_envelope("attribute")


@pytest.mark.parametrize("name", acceptance.CASES)
def test_three_native_envelopes_reach_normal_pipeline_and_match_independent_gold(name):
    source_bytes, task_bytes, expected = acceptance.fixture(name)
    source = source_bytes.decode("utf-8")
    task = TaskSpec.model_validate_json(task_bytes)
    registry = default_registry()
    registry.register("synthetic-envelope", synthetic_factory([acceptance.mock_envelope(name)]))
    config = BackendConfig(adapter="synthetic-envelope", base_url="https://synthetic.invalid", model="configured")
    backend = registry.create(config.adapter, config=config)
    document = Document.from_text(source)
    result = extract(document, task, backend=backend)
    checked = acceptance.check_result(result.model_dump(mode="json"), source, expected)
    assert checked["content_values_passed"] and checked["evidence_passed"]
    assert checked["needs_review_count"] == 0
    assert result.run.mode == "replay" and result.run.request_attempts == 1
    assert len(backend.requests) == 1
    # Structural checks and exact spans are not review decisions.
    assert create_archive(document, result).records == []


@pytest.mark.parametrize(("native", "error", "code"), [
    ({"status": "completed", "output": {"text": " "}}, InvalidResponseError, "empty_response"),
    ({"status": "completed", "output": {"text": []}}, InvalidResponseError, "invalid_response"),
    ({"status": "incomplete", "output": {"text": '{"records":'}}, InvalidResponseError, "truncated_response"),
    ({"status": "error", "error": {"message": "private-token"}}, BackendError, "provider_error"),
    ({"status": "mystery"}, InvalidResponseError, "invalid_response"),
])
def test_native_failures_are_explicit_and_never_retried_or_expose_provider_text(native, error, code):
    backend = SyntheticEnvelopeBackend([native, acceptance.mock_envelope("attribute")])
    with pytest.raises(error) as caught:
        backend.generate(request())
    assert caught.value.code == code
    assert not caught.value.retryable and caught.value.attempts == 1
    assert "private-token" not in str(caught.value)
    assert len(backend.requests) == 1


def test_missing_model_and_invalid_native_metrics_remain_unknown():
    native = {"status": "completed", "output": {"text": '{"records": []}'},
              "metrics": {"input_tokens": -1, "output_tokens": True, "total_tokens": "12"}}
    response = SyntheticEnvelopeBackend([native], model="configured").generate(request())
    assert response.model == "configured" and response.reported_model is None
    assert response.request_id is None and response.usage == {}


def test_unsupported_roles_and_modes_fail_explicitly():
    backend = SyntheticEnvelopeBackend([acceptance.mock_envelope("attribute")])
    incoming = request()
    incoming.messages.append({"role": "tool", "content": "not supported"})
    with pytest.raises(ConfigurationError):
        backend.generate(incoming)
    assert not backend.requests
    config = BackendConfig(base_url="https://synthetic.invalid", model="configured", structured_output="json_schema")
    with pytest.raises(ConfigurationError):
        synthetic_factory([])(config)


def test_script_default_mock_uses_toml_registry_cli_without_any_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Mock acceptance must not use a network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    destination = tmp_path / "new-run"
    assert acceptance.main(["--output-dir", str(destination)]) == 0
    summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "mock" and summary["real_endpoint_calls"] == 0
    assert summary["mock_http_calls"] == 3
    assert summary["passed"] and len(summary["cases"]) == 3
    assert [case["candidate_count"] for case in summary["cases"]] == [1, 2, 1]
    assert [case["exact_source_spans"] for case in summary["cases"]] == [3, 10, 3]
    for case in summary["cases"]:
        assert case["exit_code"] == 0 and case["http_attempts"] == 0
        assert case["mock_http_attempts"] == 1
        assert case["format_passed"] and case["content_values_passed"] and case["evidence_passed"]
        logs = destination / "synthetic" / case["case"] / "diagnostics"
        body = json.loads((logs / "requests" / "chunk-0001.request.json").read_text(encoding="utf-8"))
        assert set(body) == {"deployment", "input", "parameters"}
        assert body["parameters"] == {"output_limit": 2048}
        user_data = json.loads(body["input"]["prompt"])
        assert "source" in user_data and "expected" not in user_data and "reviews" not in user_data
        result = json.loads((logs / "result.json").read_text(encoding="utf-8"))
        assert result["run"]["mode"] == "replay"
    original = (destination / "summary.json").read_bytes()
    assert acceptance.main(["--output-dir", str(destination)]) == 1
    assert (destination / "summary.json").read_bytes() == original


def _profiles(path, *, second_key="QWEN_SYNTHETIC_KEY", host="api.deepseek.com"):
    path.write_text(f'[profiles.deepseek]\nbase_url="https://{host}"\nmodel="synthetic"\n'
                    'api_key_env="DEEPSEEK_SYNTHETIC_KEY"\nmax_attempts=8\nformat_retries=1\n'
                    '[profiles.deepseek.generation]\nmax_output_tokens=99999\n'
                    '[profiles.qwen]\nbase_url="https://dashscope.aliyuncs.com/compatible-mode/v1"\n'
                    f'model="synthetic"\napi_key_env="{second_key}"\n', encoding="utf-8")


def test_live_preflight_checks_every_profile_before_any_call(tmp_path, monkeypatch):
    config = tmp_path / "providers.toml"
    _profiles(config)
    monkeypatch.setenv("DEEPSEEK_SYNTHETIC_KEY", "synthetic-one")
    monkeypatch.delenv("QWEN_SYNTHETIC_KEY", raising=False)
    monkeypatch.setattr(acceptance.cli, "main", lambda *a, **k: pytest.fail("No call allowed before all credentials exist"))
    destination = tmp_path / "run"
    assert acceptance.main(["--mode", "live", "--config", str(config), "--output-dir", str(destination)]) == 1
    assert not destination.exists()


@pytest.mark.parametrize("extra", [
    ["--profiles", "deepseek", "deepseek"],
    ["--profiles", "deepseek", "qwen", "third"],
    ["--profiles", "../escape"],
])
def test_live_profile_budget_rejected_before_calls(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(acceptance.cli, "main", lambda *a, **k: pytest.fail("No model call permitted"))
    assert acceptance.main(["--mode", "live", "--config", str(tmp_path / "providers.toml"),
                            "--output-dir", str(tmp_path / "run"), *extra]) == 1


def test_live_acceptance_forces_single_request_budget_and_does_not_retry_failure(tmp_path, monkeypatch):
    config = tmp_path / "providers.toml"
    _profiles(config)
    monkeypatch.setenv("DEEPSEEK_SYNTHETIC_KEY", "synthetic-one")
    monkeypatch.setenv("QWEN_SYNTHETIC_KEY", "synthetic-two")
    calls = []

    def failed_cli(arguments, **kwargs):
        calls.append(arguments)
        for flag, expected in (("--max-attempts", "1"), ("--format-retries", "0"),
                               ("--timeout", "60"), ("--max-output-tokens", "2048")):
            assert arguments[arguments.index(flag) + 1] == expected
        logs = Path(arguments[arguments.index("--log-dir") + 1])
        logs.mkdir()
        (logs / "events.jsonl").write_text('{"stage":"http","event":"started","attempt":1}\n', encoding="utf-8")
        return 1

    monkeypatch.setattr(acceptance.cli, "main", failed_cli)
    destination = tmp_path / "run"
    assert acceptance.main(["--mode", "live", "--config", str(config), "--output-dir", str(destination)]) == 1
    summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert len(calls) == summary["real_endpoint_calls"] == 6
    assert all(case["content_values_passed"] is None for case in summary["cases"])
    assert not summary["passed"]


@pytest.mark.parametrize("host", ["private.example", "api.deepseek.com.evil.example", "maas.aliyuncs.com"])
def test_live_acceptance_rejects_nonofficial_hosts_before_calls(tmp_path, monkeypatch, host):
    config = tmp_path / "providers.toml"
    _profiles(config, host=host)
    monkeypatch.setenv("DEEPSEEK_SYNTHETIC_KEY", "synthetic-one")
    monkeypatch.setattr(acceptance.cli, "main", lambda *a, **k: pytest.fail("No model call permitted"))
    assert acceptance.main(["--mode", "live", "--config", str(config), "--profiles", "deepseek",
                            "--output-dir", str(tmp_path / "run")]) == 1


def test_value_check_rejects_wrong_value_even_with_exact_quote_and_good_offsets():
    source, task, expected = acceptance.fixture("attribute")
    result = extract(source.decode("utf-8"), TaskSpec.model_validate_json(task),
                     backend=SyntheticEnvelopeBackend([acceptance.mock_envelope("attribute")]))
    payload = deepcopy(result.model_dump(mode="json"))
    payload["candidates"][0]["fields"]["rated_power"]["value"] = 80
    checked = acceptance.check_result(payload, source.decode("utf-8"), expected)
    assert not checked["content_values_passed"] and checked["evidence_passed"]

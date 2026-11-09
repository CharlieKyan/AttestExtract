"""Synthetic acceptance for bounded scheduling, interruption and real engine use."""

import json
from pathlib import Path
import sys

import httpx
import pytest

from attestextract.errors import ConfigurationError
from attestextract.registry import default_registry
from attestextract.models import ExtractionRequest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import provider_experiment_bundle as bundle
import provider_experiment_runner as runner


class Clock:
    def __init__(self):
        self.now = 1800000000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "runtime_identity", lambda: {"engine": "synthetic"})
    monkeypatch.setattr(bundle, "network_identity", lambda: {"network": "synthetic"})
    source, task = tmp_path / "paper.md", tmp_path / "task.json"
    source.write_text("\n\n".join(f"Item {i:02} reads 11. " + "Synthetic background. " * 170
                                  for i in range(16)), encoding="utf-8")
    task.write_text(json.dumps({"name": "Synthetic paper", "record_types": {"reading": {
        "fields": {"value": {"type": "integer"}}}}}), encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "synthetic-deepseek", "VLM_API_KEY": "synthetic-qwen",
                   "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    root = tmp_path / "bundle"
    bundle.prepare_bundle(root, source, task, environment)
    return root, environment, bundle.load_bundle(root), Clock()


def endpoint_factory(data, observed, clock, fail=None):
    def endpoint(request):
        assert request.method == "POST", "Mock transport must not be used to claim TLS probing"
        body = json.loads(request.content)
        observed.append(body)
        clock.sleep(0.5)
        if fail:
            failure = fail(request, body, len(observed))
            if failure is not None:
                if isinstance(failure, BaseException):
                    raise failure
                return failure
        responses = request.url.path.endswith("/responses")
        messages = body["input"] if responses else body["messages"]
        content = messages[-1]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        text = json.loads(content)["source"]
        case = next((c for key, c in data["cases"].items() if key.startswith("d1-") and c["source"] == text), None)
        if case:
            records = [{"record_type": record["record_type"],
                        "values": {k: v["value"] for k, v in record["fields"].items()},
                        "quotes": {k: v["quote"] for k, v in record["fields"].items()}}
                       for record in case["gold"]]
        else:
            records = [{"record_type": "reading", "values": {"value": 11},
                        "quotes": {"value": text.split(". ")[0] + "."}}]
        output = json.dumps({"records": records}, ensure_ascii=False)
        if responses:
            return httpx.Response(200, json={"object": "response", "status": "completed", "model": "deepseek-flash",
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": output}]}],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}})
        return httpx.Response(200, json={"model": body["model"],
            "choices": [{"finish_reason": "stop", "message": {"content": output}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    def factory(adapter, *, config, api_key, trace):
        backend = default_registry().create(adapter, config=config, api_key=api_key, trace=trace)
        backend._transport = httpx.MockTransport(endpoint)
        return backend
    return factory


def run(root, environment, stage, clock, factory, round_number=None):
    return runner.run_stage(root, environment, stage, round_number, clock=clock,
                            sleep=clock.sleep, backend_factory=factory, progress=lambda _: None)


def test_all_108_mock_trials_and_final_fresh_extraction(prepared):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock)
    first = run(root, env, "A", clock, factory)
    assert len(observed) == 16
    assert first["full_runs"][0]["passed"] is True
    # A second invocation cannot reuse/duplicate any paid request.
    run(root, env, "A", clock, factory)
    assert len(observed) == 16
    for number in (1, 2):
        run(root, env, "B", clock, factory, number)
    with pytest.raises(ConfigurationError, match="not due"):
        run(root, env, "B", clock, factory, 3)
    assert len(observed) == 40
    clock.sleep(1800)
    run(root, env, "B", clock, factory, 3)
    third = run(root, env, "C", clock, factory)
    assert third["protocol_selection"]["deepseek"]["selected"] == "deepseek-chat-object"
    final = run(root, env, "D", clock, factory)
    assert len(observed) == 108
    assert final["counts"]["sent"] == 108
    assert final["counts"]["reserved"] == 108
    assert final["counts"]["completed"] == 108
    assert len(final["protocol_selection"]["deepseek"]["eligible"]) == 3
    assert all(item["passed"] for item in final["full_runs"])
    for provider in ("qwen", "deepseek"):
        result = json.loads((root / "results/papers" / f"D-{provider}" / "result.json").read_text(encoding="utf-8"))
        assert len(result["candidates"]) == 16
        assert result["schema_version"] == "0.4"
    run(root, env, "D", clock, factory)
    assert len(observed) == 108


def test_whole_paper_stops_at_first_timeout_and_never_resumes(prepared):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock,
        lambda request, body, n: httpx.ReadTimeout("synthetic timeout", request=request) if n == 12 else None)
    summary = run(root, env, "A", clock, factory)
    assert len(observed) == 12
    assert summary["full_runs"][0]["completed_chunks"] == 11
    assert summary["full_runs"][0]["passed"] is False
    results = runner.load_results(root / "results", data["rows"])
    failed = results[data["rows"][11]["id"]]
    assert failed["transport_unknown"] and failed["billing_unknown"]
    assert failed["scores"] is None
    assert sum(x["status"] == "not_run" for x in results.values()) == 4
    run(root, env, "A", clock, factory)
    assert len(observed) == 12


def test_unknown_reservation_and_corrupt_ledger_fail_closed(prepared):
    root, env, data, clock = prepared
    row = data["rows"][16]
    reservation = {"id": row["id"], "row_sha256": runner.digest(row), "provider": row["provider"],
                   "started_epoch": clock(), "reserved_calls": 1}
    directory = runner.trial_root(root / "results", row)
    runner.save_record(directory / "reserved.json", reservation)
    experiment = runner.ExperimentRunner(data, root / "results", env, clock=clock, sleep=clock.sleep)
    with pytest.raises(ConfigurationError, match="reserved"):
        experiment.request(row, ExtractionRequest.model_validate(row["request"]))
    (directory / "reserved.json").write_text('{"result":{},"sha256":"wrong"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="integrity"):
        runner.load_results(root / "results", data["rows"])


def test_network_or_endpoint_changes_rejected_before_new_request(prepared, monkeypatch):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock)
    changed = {**env, "VLM_BASE_URL": "https://other.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    with pytest.raises(ConfigurationError, match="bindings"):
        run(root, changed, "A", clock, factory)
    monkeypatch.setattr(bundle, "network_identity", lambda: {"network": "changed"})
    with pytest.raises(ConfigurationError, match="environment"):
        run(root, env, "A", clock, factory)
    assert observed == []


def test_transport_circuit_is_provider_scoped_and_stays_stopped():
    results = {"a": {"provider": "qwen", "sent": True, "transport_unknown": True, "started_epoch": 1},
               "b": {"provider": "deepseek", "sent": True, "transport_unknown": False, "started_epoch": 2},
               "c": {"provider": "qwen", "sent": True, "transport_unknown": True, "started_epoch": 3},
               "d": {"provider": "qwen", "sent": True, "transport_unknown": True, "started_epoch": 4}}
    assert runner.stop_reason("qwen", results, 5) == "three_consecutive_unknown_transport_outcomes"
    assert runner.stop_reason("deepseek", results, 5) is None


def test_parallel_runner_lock_prevents_double_reservation(tmp_path):
    with runner.exclusive_run(tmp_path):
        with pytest.raises(ConfigurationError, match="Another process"):
            with runner.exclusive_run(tmp_path):
                pytest.fail("Must not acquire an already owned ledger")


@pytest.mark.parametrize("failure,expected_sent,billing_unknown", [
    (httpx.ConnectError, False, False), (httpx.ReadTimeout, None, True)])
def test_attempt_is_not_proof_of_send_or_zero_cost(prepared, failure, expected_sent, billing_unknown):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock,
        lambda request, body, n: failure("synthetic", request=request))
    summary = run(root, env, "A", clock, factory)
    result = runner.load_results(root / "results", data["rows"])[data["rows"][0]["id"]]
    assert len(observed) == 1 and result["model_post_attempts"] == 1
    assert result["sent"] is expected_sent and result["billing_unknown"] is billing_unknown
    assert result["scores"] is None
    assert summary["counts"]["evaluation"]["json_passed"]["failed"] == 0


def test_account_failure_stops_provider_but_not_other_provider(prepared):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock,
        lambda request, body, n: httpx.Response(401, json={"error": {"message": "synthetic"}})
        if "maas.aliyuncs.com" in request.url.host else None)
    run(root, env, "A", clock, factory)
    summary = run(root, env, "B", clock, factory, 1)
    assert len(observed) == 7  # One failed Qwen request; six independent DeepSeek trials.
    assert all(row["status"] == "not_run" for row in
               runner.load_results(root / "results", data["rows"]).values()
               if row["provider"] == "qwen" and row["stage"] == "B")
    assert summary["counts"]["http_complete"] == 7
    assert summary["counts"]["http_success"] == 6


def test_completed_trials_do_not_override_failed_final_pipeline(prepared, monkeypatch):
    root, env, data, clock = prepared
    observed = []
    factory = endpoint_factory(data, observed, clock)
    original = runner.extract

    def fail_after_generation(*args, **kwargs):
        original(*args, **kwargs)
        raise ConfigurationError("synthetic final pipeline failure")

    monkeypatch.setattr(runner, "extract", fail_after_generation)
    with pytest.raises(ConfigurationError):
        run(root, env, "A", clock, factory)
    summary = runner.write_report(root / "results", data)
    assert len(observed) == 16
    assert summary["full_runs"][0]["trials_passed"] is True
    assert summary["full_runs"][0]["passed"] is False


def test_run_requires_previous_stage_and_explicit_live_switch(prepared):
    import provider_experiment
    root, env, data, clock = prepared
    with pytest.raises(ConfigurationError, match="preceding"):
        run(root, env, "B", clock, lambda *a, **kw: pytest.fail("No backend creation"), 1)
    with pytest.raises(SystemExit):
        provider_experiment.main(["run", "--bundle", str(root), "--env-file", "missing.env", "--stage", "A"])

"""Scheduling contracts: visible frozen inputs, bounded sends and failure lineage."""

import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import BackendError, ConfigurationError
from attestextract.models import BackendResponse

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import table_route_runner as runner


def case(name="synthetic"):
    return {"id": name, "source": "| Sample | Mass/kg |\n|---|---|\n| S | 10 |\n",
            "task": {"name": "Mass", "record_types": {"measurement": {"fields": {
                "value": {"type": "string"}}}}}, "gold": [],
            "replays": {"A": {"records": []}}}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "frozen"})
    root = tmp_path / "bundle"
    runner.prepare_bundle(root, [case()])
    return root


def backend_factory(calls, failure=None):
    class Backend:
        def generate(self, request):
            calls.append(request)
            if failure:
                result = failure(request)
                if isinstance(result, BaseException):
                    raise result
                if result is not None:
                    return result
            stage = request.chunk_id.rsplit("-", 1)[-1]
            return BackendResponse(text=json.dumps(case()["replays"][stage]), model="mock-http-not-live")
    return lambda **kwargs: Backend()


def test_frozen_four_cases_has_four_a_slots_and_no_answer_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "frozen"})
    root = tmp_path / "bundle"
    cases = [case(f"case{i}") for i in range(4)]
    for item in cases:
        item["gold"] = [{"secret_answer": "DO_NOT_SEND_REFERENCE_ANSWER"}]
    manifest = runner.prepare_bundle(root, cases)
    bundle = runner.load_bundle(root)
    assert manifest["schema_version"] == "table-a-0.3"
    assert manifest["max_calls"] == 4
    assert len(bundle["requests"]) == 4
    assert "DO_NOT_SEND_REFERENCE_ANSWER" not in json.dumps(bundle["requests"])
    assert all(slot["stage"] == "A" and slot["depends_on"] is None for slot in bundle["slots"])
    assert all(r["generation"]["max_output_tokens"] == 4096 for r in bundle["requests"].values())


def test_offline_replay_records_each_step_and_is_idempotent(prepared):
    assert runner.run_bundle(prepared)["statuses"] == {"success": 1}
    for slot in runner.load_bundle(prepared)["slots"]:
        trial = prepared / "trials" / slot["slot_id"]
        assert all((trial / f"{name}.json").exists() for name in (
            "reservation", "request", "response", "parsed", "evaluation", "outcome"))
        assert (trial / "reconciliation.json").exists()
    assert runner.run_bundle(prepared)["remaining_slots"] == 0
    with pytest.raises(ConfigurationError, match="cannot share"):
        runner.run_bundle(prepared, mode="live", backend_factory=backend_factory([]))


def test_reserved_unknown_is_not_resent(prepared):
    bundle = runner.load_bundle(prepared)
    slot = bundle["slots"][0]
    trial = prepared / "trials" / slot["slot_id"]
    request = bundle["requests"][slot["slot_id"]]
    runner.write_new(trial / "request.json", request)
    runner.write_new(trial / "reservation.json", {"mode": "live", "slot_sha256": runner.digest(slot),
                                                  "request_sha256": runner.digest(request)})
    calls = []
    summary = runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls))
    assert len(calls) == 0
    assert summary["statuses"]["reserved_unknown"] == 1
    runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls))
    assert len(calls) == 0


def test_invalid_a_is_not_retried_or_reallocated(prepared):
    calls = []
    fail = lambda request: BackendResponse(text="{broken")
    result = runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls, fail))
    assert len(calls) == 1
    assert result["statuses"] == {"format_error": 1}
    artifact = runner.read_json(prepared / "trials/r1-synthetic-A/error.json")
    assert artifact["phase"] == "parse"
    runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls))
    assert len(calls) == 1


def test_runner_passes_frozen_table_input_to_reconciliation(prepared, monkeypatch):
    import attestextract.table_routes as core
    original = core.reconcile_results
    received = []
    def reconcile(*args, **kwargs):
        received.append(kwargs["table_input"])
        return original(*args, **kwargs)
    monkeypatch.setattr(core, "reconcile_results", reconcile)
    runner.run_bundle(prepared)
    assert received == [runner.load_bundle(prepared)["table_inputs"]["synthetic"]]


def test_truncation_saved_with_locatable_error(prepared):
    calls = []
    fail = lambda request: BackendResponse(text='{"records": []}', finish_reason="length")
    runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls, fail))
    trial = prepared / "trials/r1-synthetic-A"
    assert runner.read_json(trial / "error.json")["code"] == "output_truncated"
    assert runner.read_json(trial / "response.json")["finish_reason"] == "length"


def test_auth_failure_stops_without_retry_and_redacts_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "frozen"})
    prepared = tmp_path / "auth"
    runner.prepare_bundle(prepared, [case(f"case{i}") for i in range(4)])
    calls = []
    fail = lambda request: BackendError("secret-token-must-not-persist", code="authentication_error", status_code=401)
    result = runner.run_bundle(prepared, mode="live", backend_factory=backend_factory(calls, fail))
    assert len(calls) == 1
    assert result["statuses"] == {"backend_error": 1, "skipped": 3}
    all_text = "".join(p.read_text(encoding="utf-8") for p in prepared.rglob("*.json"))
    assert "secret-token-must-not-persist" not in all_text


def test_frozen_data_runtime_and_saved_evidence_cannot_silently_change(prepared, monkeypatch):
    runner.run_bundle(prepared)
    response = prepared / "trials/r1-synthetic-A/response.json"
    response.write_text('{"text": "edited"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="evidence changed"):
        runner.inspect_bundle(prepared)
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "different"})
    with pytest.raises(ConfigurationError, match="code or Python changed"):
        runner.load_bundle(prepared)


def test_output_in_public_repo_and_unsafe_config_rejected(tmp_path):
    with pytest.raises(ConfigurationError, match="outside"):
        runner.prepare_bundle(Path(__file__).parents[1] / "unsafe-bundle", [case()])
    with pytest.raises(ConfigurationError, match="valid backend"):
        runner.prepare_bundle(tmp_path / "badconfig", [case()], {**runner.DEFAULT_BACKEND, "max_attempts": 2})
    assert not (tmp_path / "badconfig").exists()


def test_user_response_override_creates_offline_fork_without_changing_original(prepared, tmp_path):
    runner.run_bundle(prepared)
    original = (prepared / "trials/r1-synthetic-A/response.json").read_bytes()
    fork = tmp_path / "edited"
    runner.fork_replay(prepared, fork, {"r1-synthetic-A": "{deliberate broken JSON"})
    summary = runner.run_bundle(fork)
    assert summary["statuses"] == {"format_error": 1}
    assert (prepared / "trials/r1-synthetic-A/response.json").read_bytes() == original
    with pytest.raises(ConfigurationError, match="offline-only"):
        runner.run_bundle(fork, mode="live", backend_factory=backend_factory([]))


def test_credential_echo_in_model_metadata_is_redacted(prepared):
    secret = "synthetic-private-key"
    response = BackendResponse(text='{"records": []}', model=secret, reported_model=secret)
    runner.run_bundle(prepared, mode="live", environment={"DEEPSEEK_API_KEY": secret},
                      backend_factory=backend_factory([], lambda request: response))
    for path in (prepared / "trials").rglob("*.json"):
        assert secret not in path.read_text(encoding="utf-8")


def test_report_entrypoint_works_and_refuses_tampered_trial(prepared):
    runner.run_bundle(prepared)
    report = runner.write_report(prepared)
    assert "<html" in report.read_text(encoding="utf-8")
    response = prepared / "trials/r1-synthetic-A/response.json"
    response.write_text('{"text":"changed"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="evidence changed"):
        runner.write_report(prepared)


@pytest.mark.parametrize("schema", ["table-a-0.2", "table-a-0.1"])
def test_previous_a_manifests_are_readonly_but_can_import_saved_responses(prepared, tmp_path, schema):
    runner.run_bundle(prepared)
    manifest = runner.read_json(prepared / "manifest.json")
    manifest["schema_version"] = schema
    manifest.pop("processing_policy", None)
    (prepared / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = {p.relative_to(prepared).as_posix(): p.read_bytes() for p in prepared.rglob("*") if p.is_file()}
    with pytest.raises(ConfigurationError, match="Previous bundles are read-only"):
        runner.run_bundle(prepared)
    imported = tmp_path / "imported"
    new = runner.fork_replay(prepared, imported, {})
    assert new["schema_version"] == "table-a-0.3" and new["replay_only"] is True
    assert runner.run_bundle(imported)["statuses"] == {"success": 1}
    assert before == {p.relative_to(prepared).as_posix(): p.read_bytes() for p in prepared.rglob("*") if p.is_file()}


def test_new_runner_results_are_v3_with_original_fields_and_no_strict_view(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "stable"})
    examples = Path(__file__).parents[1] / "src/attestextract/examples/table_routes/cases.json"
    item = json.loads(examples.read_text(encoding="utf-8"))[0]
    root = tmp_path / "observe"
    runner.prepare_bundle(root, [item])
    summary = runner.run_bundle(root)
    assert summary["statuses"] == {"success": 1}
    assert summary["quality_summary"]["delivery_fidelity"]["changed_records"] == 0
    trial = root / "trials" / f'r1-{item["id"]}-A'
    parsed = runner.read_json(trial / "parsed.json")
    result = runner.read_json(trial / "results.json")
    assert result["schema_version"] == "table-route-v3"
    assert result["processing_policy"] == "observe_only"
    for candidate, raw in zip(result["candidates"], parsed["records"]):
        assert all(candidate[key] == value for key, value in raw.items())
    for name in ("evaluation.json", "reconciliation.json", "results.json"):
        assert not {"current_results", "changes", "all_changes"} & runner.read_json(trial / name).keys()

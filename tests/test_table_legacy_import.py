"""Legacy A/B ledgers remain immutable; only saved A text can be re-evaluated."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import ConfigurationError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import table_route_runner as runner


def sample_case():
    return {"id": "sample", "source": "| Sample | Mass/kg |\n|---|---|\n| S | 10 |\n",
            "task": {"name": "Mass", "record_types": {"measurement": {
                "fields": {"value": {"type": "string"}}}}},
            "gold": [{"name": "reference must remain private"}],
            "replays": {"A": {"records": []}}, "expected_diagnostics": {"A": []}}


def legacy_bundle(root, rounds=2):
    """Write a synthetic old-format ledger without invoking any B implementation."""
    runner.prepare_bundle(root, [sample_case()], rounds=rounds)
    a_request = runner.read_json(root / "requests.json")["r1-sample-A"]
    cases = runner.read_json(root / "cases.json")
    cases[0]["replays"].update(B1={"statements": []}, B2={"records": []})
    cases[0]["expected_diagnostics"].update(B1=["old-b-error"], B2=["old-b-error"])
    slots = []
    for round_number in range(1, rounds + 1):
        stages = ("A", "B1", "B2") if round_number == 1 else ("B1", "B2", "A")
        for stage in stages:
            slots.append({"slot_id": f"r{round_number}-sample-{stage}", "case_id": "sample",
                          "round": round_number, "stage": stage,
                          "depends_on": f"r{round_number}-sample-B1" if stage == "B2" else None})
    requests = {slot["slot_id"]: {**a_request, "chunk_id": slot["slot_id"]}
                for slot in slots if slot["stage"] != "B2"}
    manifest = runner.read_json(root / "manifest.json")
    for name, data in (("cases.json", cases), ("slots.json", slots), ("requests.json", requests)):
        (root / name).write_text(json.dumps(data), encoding="utf-8")
        manifest["artifacts"][name] = runner.digest(data)
    manifest.update(schema_version="table-routes-0.1", max_calls=len(slots))
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for slot in slots:
        trial = root / "trials" / slot["slot_id"]
        request = {**a_request, "chunk_id": slot["slot_id"]}
        runner.write_new(trial / "request.json", request)
        runner.write_new(trial / "reservation.json", {
            "mode": "live", "slot_sha256": runner.digest(slot), "request_sha256": runner.digest(request)})
        raw = '{"records": []}' if slot["stage"] == "A" else "historic B answer, never parsed"
        runner.write_new(trial / "response.json", {"text": raw, "model": "synthetic-history"})
        runner._finish(trial, {**slot, "mode": "live", "status": "success"})
    (root / "REPORT.html").write_text("<html>Saved historic A/B report</html>", encoding="utf-8")
    return cases


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture(autouse=True)
def fixed_runtime(monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "legacy-import"})


@pytest.mark.parametrize("rounds", [1, 2])
def test_import_validates_legacy_and_keeps_only_saved_a_answers(tmp_path, rounds):
    original, fork = tmp_path / "old", tmp_path / "new"
    old_cases = legacy_bundle(original, rounds)
    before = snapshot(original)
    manifest = runner.fork_replay(original, fork, {})
    assert manifest["schema_version"] == "table-a-0.2"
    assert manifest["rounds"] == manifest["max_calls"] == rounds
    assert manifest["replay_only"] is True
    bundle = runner.load_bundle(fork)
    assert bundle["cases"][0]["task"] == old_cases[0]["task"]
    assert bundle["cases"][0]["gold"] == old_cases[0]["gold"]
    assert bundle["cases"][0]["replays"] == {"A": {"records": []}}
    assert bundle["cases"][0]["expected_diagnostics"] == {"A": []}
    assert set(bundle["replay_overrides"]) == {f"r{r}-sample-A" for r in range(1, rounds + 1)}
    assert set(bundle["replay_overrides"].values()) == {'{"records": []}'}
    assert runner.run_bundle(fork)["statuses"] == {"success": rounds}
    assert snapshot(original) == before
    with pytest.raises(ConfigurationError, match="offline-only"):
        runner.run_bundle(fork, mode="live")


@pytest.mark.parametrize("operation", [runner.run_bundle, runner.inspect_bundle,
                                        runner.summarize_bundle, runner.write_report])
def test_legacy_run_and_reports_are_refused_without_mutation(tmp_path, operation):
    original = tmp_path / "old"
    legacy_bundle(original)
    before = snapshot(original)
    with pytest.raises(ConfigurationError, match="saved REPORT.html"):
        operation(original)
    assert snapshot(original) == before


def test_legacy_b_evidence_is_verified_even_though_only_a_is_imported(tmp_path):
    original = tmp_path / "old"
    legacy_bundle(original)
    (original / "trials/r1-sample-B1/response.json").write_text('{"text":"tampered"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="evidence changed"):
        runner.fork_replay(original, tmp_path / "new", {})
    assert not (tmp_path / "new").exists()


def test_legacy_b_override_is_rejected_before_creation(tmp_path):
    original = tmp_path / "old"
    legacy_bundle(original)
    with pytest.raises(ConfigurationError, match="B overrides are not supported"):
        runner.fork_replay(original, tmp_path / "new", {"r1-sample-B2": '{"records": []}'})
    assert not (tmp_path / "new").exists()


def test_missing_saved_a_requires_explicit_offline_override(tmp_path):
    original = tmp_path / "original"
    runner.prepare_bundle(original, [sample_case()])
    with pytest.raises(ConfigurationError, match="Missing saved A responses"):
        runner.fork_replay(original, tmp_path / "missing", {})
    manifest = runner.fork_replay(original, tmp_path / "explicit", {"r1-sample-A": {"records": []}})
    assert manifest["replay_only"] is True


@pytest.mark.parametrize("field", ["replays", "expected_diagnostics"])
def test_new_bundle_rejects_b_metadata(tmp_path, field):
    case = deepcopy(sample_case())
    case[field]["B1"] = {}
    with pytest.raises(ConfigurationError, match="only A"):
        runner.prepare_bundle(tmp_path / "new", [case])
    assert not (tmp_path / "new").exists()

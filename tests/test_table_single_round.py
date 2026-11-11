"""Explicit one-round authorization must stay bounded across execution and forks."""

import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import table_route_experiment as cli
import table_route_runner as runner


def cases():
    return [{"id": f"sample{i}", "source": "| Sample | Mass/kg |\n|---|---|\n| S | 10 |\n",
             "task": {"name": "Mass", "record_types": {"measurement": {
                 "fields": {"value": {"type": "string"}}}}}, "gold": [],
             "replays": {"A": {"records": []}}} for i in range(4)]


@pytest.fixture(autouse=True)
def fixed_runtime(monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "single-round"})


def fake_backend(calls, *, invalid_a=False):
    class Backend:
        def generate(self, request):
            calls.append(request.chunk_id)
            stage = request.chunk_id.rsplit("-", 1)[-1]
            text = "{broken" if invalid_a else json.dumps(cases()[0]["replays"][stage])
            return BackendResponse(text=text, model="offline-test-double")
    return lambda **kwargs: Backend()


def test_single_round_has_four_calls_and_resume_never_adds_second_round(tmp_path):
    root = tmp_path / "one"
    runner.prepare_bundle(root, cases(), rounds=1)
    calls = []
    result = runner.run_bundle(root, mode="live", backend_factory=fake_backend(calls))
    assert result["planned_slots"] == result["live_backend_invocations_this_run"] == 4
    assert result["statuses"] == {"success": 4}
    assert len(calls) == len(set(calls)) == 4
    assert all(name.startswith("r1-") for name in calls)
    assert result["execution"]["confirmed_complete_live_http_stages"] == 0
    resumed = runner.run_bundle(root, mode="live", backend_factory=fake_backend(calls))
    assert resumed["live_backend_invocations_this_run"] == 0
    assert len(calls) == 4


def test_one_round_a_failure_does_not_add_replacement_calls(tmp_path):
    root = tmp_path / "failed"
    runner.prepare_bundle(root, cases(), rounds=1)
    calls = []
    result = runner.run_bundle(root, mode="live", backend_factory=fake_backend(calls, invalid_a=True))
    assert len(calls) == 4
    assert result["planned_slots"] == 4
    assert result["statuses"] == {"format_error": 4}
    assert all(name.endswith("-A") for name in calls)


@pytest.mark.parametrize("rounds,slots", [(1, 4), (2, 8)])
def test_fork_preserves_original_rounds_and_is_offline_only(tmp_path, monkeypatch, rounds, slots):
    root, fork = tmp_path / "original", tmp_path / "fork"
    runner.prepare_bundle(root, cases(), rounds=rounds)
    runner.run_bundle(root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "later"})
    assert runner.summarize_bundle(root)["execution"]["completed_stages"] == slots
    manifest = runner.fork_replay(root, fork, {})
    assert manifest["rounds"] == rounds and manifest["max_calls"] == slots
    assert manifest["replay_only"] is True
    assert runner.run_bundle(fork)["statuses"] == {"success": slots}
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ConfigurationError, match="offline-only"):
        runner.run_bundle(fork, mode="live", backend_factory=fake_backend([]))


def test_cli_default_one_round_and_explicit_two_rounds(tmp_path, capsys):
    source = tmp_path / "cases.json"
    source.write_text(json.dumps(cases()), encoding="utf-8")
    for name, options, expected in [("two", ["--rounds", "2"], 8), ("default", [], 4)]:
        root = tmp_path / name
        assert cli.main(["prepare", "--cases", str(source), "--output", str(root), *options]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["planned_slots"] == expected and result["network_calls"] == 0
        assert len(runner.load_bundle(root)["slots"]) == expected


@pytest.mark.parametrize("rounds", [0, 3, True, 1.0, None, "1"])
def test_unsupported_rounds_rejected_before_creating_bundle(tmp_path, rounds):
    root = tmp_path / "invalid"
    with pytest.raises(ConfigurationError, match="1 or 2 rounds"):
        runner.prepare_bundle(root, cases(), rounds=rounds)
    assert not root.exists()


@pytest.mark.parametrize("mutation", ["dependency", "round", "extra", "missing", "order"])
def test_rehashed_schedule_must_still_match_frozen_round_count(tmp_path, mutation):
    root = tmp_path / "invalid"
    runner.prepare_bundle(root, cases(), rounds=1)
    manifest = runner.read_json(root / "manifest.json")
    slots = runner.read_json(root / "slots.json")
    if mutation == "dependency":
        slots[2]["depends_on"] = "r1-sample1-A"
    elif mutation == "round":
        slots[0]["round"] = 2
    elif mutation == "extra":
        slots.append({**slots[0], "slot_id": "r2-sample0-A", "round": 2})
    elif mutation == "missing":
        slots.pop()
    else:
        slots[0], slots[1] = slots[1], slots[0]
    manifest["artifacts"]["slots.json"] = runner.digest(slots)
    manifest["max_calls"] = len(slots)
    (root / "slots.json").write_text(json.dumps(slots), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="bounded slot schedule"):
        runner.load_bundle(root)

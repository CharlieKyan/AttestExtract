"""Synthetic regression for reviewed selection without resetting call budgets."""

import json
from pathlib import Path
import shutil
import sys

import pytest

from attestextract.errors import ConfigurationError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import provider_experiment_bundle as bundle_module
import provider_experiment_runner as runner
from provider_review_smoke import (Clock, complete_abc, execute, historical_snapshot,
                                   make_review, prepare_synthetic, review_factory, sha256)


@pytest.fixture(scope="module")
def completed_abc(tmp_path_factory):
    location = tmp_path_factory.mktemp("review") / "run"
    root, env, data, clock = prepare_synthetic(location)
    observed, _ = complete_abc(root, env, data, clock)
    assert len(observed) == 76
    return root, env, data, clock.now


@pytest.fixture
def ready(completed_abc, tmp_path):
    original, env, data, now = completed_abc
    root = tmp_path / "bundle"
    shutil.copytree(original, root)
    clock = Clock()
    clock.now = now
    review = make_review(root, data)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return root, env, data, clock, review, path


def test_attachment_selects_both_chat_paths_and_preserves_all_history(ready):
    root, env, data, clock, review, path = ready
    observed = []
    factory = review_factory(data, observed, clock)
    before = historical_snapshot(root)
    attached = runner.attach_review(root, path)
    assert not observed
    assert attached["reserved_calls"] == 76
    assert attached["remaining_call_budget"] == attached["pending_D_trials"] == 32
    assert all(v["selected"] is None for v in attached["strict_protocol_selection"].values())
    assert attached["protocol_selection"]["qwen"]["selected"] == "qwen-chat-object"
    assert attached["protocol_selection"]["deepseek"]["selected"] == "deepseek-chat-object"
    assert len(attached["protocol_selection"]["deepseek"]["eligible"]) == 3
    attachment = root / "results/semantic-review.json"
    attached_bytes = attachment.read_bytes()
    runner.attach_review(root, path)
    assert attachment.read_bytes() == attached_bytes
    assert runner.inspect_experiment(root)["reserved_calls"] == 76
    final = execute(root, env, "D", clock, factory)
    assert len(observed) == 32
    assert final["counts"]["reserved"] == final["counts"]["completed"] == 108
    assert final["protocol_selection_basis"] == "semantic_review"
    assert all(item["passed"] for item in final["full_runs"])
    for name, digest in before.items():
        assert sha256(root / name) == digest, name
    assert attachment.read_bytes() == attached_bytes
    for provider in ("qwen", "deepseek"):
        outcome = runner.read_record(root / "results/papers" / f"D-{provider}" / "outcome.json")
        assert outcome["fresh"] and outcome["completed_chunks"] == 16 and outcome["reused_chunks"] == 0
    execute(root, env, "D", clock, factory)
    assert len(observed) == 32


def test_unreviewed_d_fails_before_recording_skipped_trials(ready):
    root, env, data, clock, _, _ = ready
    observed = []
    with pytest.raises(ConfigurationError):
        execute(root, env, "D", clock, review_factory(data, observed, clock))
    assert observed == []
    assert not list((root / "results/trials").glob("D-*"))
    assert runner.inspect_experiment(root)["remaining_call_budget"] == 32


@pytest.mark.parametrize("change", ["duplicate", "missing", "foreign", "response_hash", "path", "candidate_count"])
def test_invalid_attachment_rejected_without_spending_or_saving(ready, change):
    root, _, _, _, review, path = ready
    if change == "duplicate":
        review["findings"][1] = dict(review["findings"][0])
    elif change == "missing":
        review["findings"].pop()
    elif change == "foreign":
        review["findings"][0]["trial_id"] = "C-999"
    elif change == "response_hash":
        review["findings"][0]["response_sha256"] = "0" * 64
    elif change == "path":
        review["findings"][0]["response_file"] = "../outside.json"
    else:
        review["findings"][0]["candidate_count"] = 999
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    before = historical_snapshot(root)
    with pytest.raises(ConfigurationError):
        runner.attach_review(root, path)
    assert not (root / "results/semantic-review.json").exists()
    assert historical_snapshot(root) == before
    assert runner.inspect_experiment(root)["reserved_calls"] == 76


@pytest.mark.parametrize("artifact", ["response", "review"])
def test_changed_artifact_after_attachment_blocks_d_before_any_post(ready, artifact):
    root, env, data, clock, review, path = ready
    runner.attach_review(root, path)
    target = (root / review["findings"][0]["response_file"] if artifact == "response"
              else root / "results/semantic-review.json")
    if artifact == "response":
        target.write_bytes(target.read_bytes() + b" ")
    else:
        target.write_text("{}", encoding="utf-8")
    observed = []
    with pytest.raises(ConfigurationError):
        execute(root, env, "D", clock, review_factory(data, observed, clock))
    assert not observed
    assert not list((root / "results/trials").glob("D-*"))


def test_attachment_cannot_be_added_after_a_d_reservation(ready):
    root, _, data, clock, _, path = ready
    row = next(row for row in data["rows"] if row["stage"] == "D")
    runner.save_record(root / "results/trials" / row["id"] / "reserved.json", {
        "id": row["id"], "row_sha256": runner.digest(row), "provider": row["provider"],
        "stage": "D", "started_epoch": clock(), "reserved_calls": 1})
    with pytest.raises(ConfigurationError):
        runner.attach_review(root, path)
    assert not (root / "results/semantic-review.json").exists()
    result = runner.load_results(root / "results", data["rows"])[row["id"]]
    assert result["status"] == "reserved_unknown"


def test_offline_attachment_does_not_disable_live_network_identity(ready, monkeypatch):
    root, env, data, clock, _, path = ready
    monkeypatch.setattr(bundle_module, "network_identity", lambda: {"changed": True})
    checked = runner.attach_review(root, path)
    assert checked["network_identity_matches"] is False
    observed = []
    with pytest.raises(ConfigurationError, match="(?i)network"):
        execute(root, env, "D", clock, review_factory(data, observed, clock))
    assert not observed
    assert not list((root / "results/trials").glob("D-*"))


@pytest.mark.parametrize("payload", [[], None, "untrusted", True])
def test_non_object_ledger_payload_is_a_configuration_error(tmp_path, payload):
    path = tmp_path / "semantic-review.json"
    path.write_text(json.dumps({"result": payload, "sha256": runner.digest(payload)}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="integrity"):
        runner.read_record(path)


def test_duplicate_ledger_keys_are_rejected(tmp_path):
    path = tmp_path / "semantic-review.json"
    path.write_text('{"result":null,"result":{},"sha256":"' + runner.digest({}) + '"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="integrity"):
        runner.read_record(path)

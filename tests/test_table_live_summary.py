"""Execution completion must not hide content errors or absent reference evidence."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import ConfigurationError
from attestextract.table_reporting import (render_table_report, score_table_records,
                                           summarize_table_bundle)

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import table_route_runner as runner
import table_route_experiment as cli


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def record(value="12", quote="12", ref="value-cell"):
    return {"record_type": "measurement", "fields": {
        "value": {"value": value, "sources": [{"ref": ref, "quote": quote}]}}}


def fake_bundle(root, *, gold=True):
    expected = {"record_type": "measurement", "values": {"value": "12"},
                "expected_sources": {"value": [{"ref": "value-cell", "quote": "12"}]}}
    case = {"id": "synthetic", "source": "synthetic", "task": {}}
    if gold:
        case["gold"] = [expected]
    write(root / "cases.json", [{**case, "id": f"synthetic-{index}"} for index in range(3)])
    write(root / "manifest.json", {})
    slots = [{"slot_id": f"r1-synthetic-{index}-A", "case_id": f"synthetic-{index}", "stage": "A",
              "round": 1} for index in range(3)]
    write(root / "slots.json", slots)
    for index, slot in enumerate(slots):
        trial = root / "trials" / slot["slot_id"]
        write(trial / "reservation.json", {})
        write(trial / "outcome.json", {"status": "success", "mode": "live"})
        write(trial / "response.json", {"text": "synthetic saved response"})
        write(trial / "parsed.json", {"records": [record()]})
        problems = [] if index == 2 else [{"code": "synthetic_problem", "record_id": "r1"}]
        write(trial / "evaluation.json", {"issues": problems, "current_results": [record("999" if index == 0 else "12")]})
    return root


def test_all_success_can_include_content_errors_and_harmful_changes(tmp_path):
    root = fake_bundle(tmp_path)
    result = summarize_table_bundle(root)
    assert result["execution"]["completed_stages"] == 3
    assert result["execution"]["saved_responses"] == 3
    quality = result["quality_summary"]
    assert quality["status"] == "issues_found"
    assert quality["stages_with_issues"] == 2
    assert quality["issue_counts"] == {"synthetic_problem": 2}
    assert quality["routes"]["A"]["raw"]["matched"] == 3
    assert quality["routes"]["A"]["current"]["matched"] == 2
    assert quality["routes"]["A"]["current"]["matched_sources"] == 3
    assert quality["affected_records"] == 2
    assert list(quality["routes"]) == ["A"]
    html = render_table_report(root)
    assert "success 不是抽取正确率" in html
    assert "严格字符串未匹配不等于科学内容错误" in html


def test_missing_gold_and_response_never_become_zero_over_zero_pass(tmp_path):
    root = fake_bundle(tmp_path, gold=False)
    for index in range(3):
        write(root / f"trials/r1-synthetic-{index}-A/evaluation.json", {"issues": [], "current_results": []})
    result = summarize_table_bundle(root)["quality_summary"]
    assert result["status"] == "reference_unassessed"
    assert result["routes"]["A"]["current"]["matched"] is None
    assert result["routes"]["A"]["current"]["trials_without_gold"] == 3
    assert result["routes"]["A"]["current"]["matched_sources"] is None


def test_complete_live_http_needs_saved_body_evidence_and_does_not_count_probes(tmp_path):
    root = fake_bundle(tmp_path)
    observed = {"request_kind": "model_post", "observation_status": "observed", "custom_transport": False,
                "status_code": 200, "body_complete": True}
    write(root / "trials/r1-synthetic-0-A/transport/http/a.connection.json", observed)
    write(root / "trials/r1-synthetic-1-A/transport/http/a.connection.json", {**observed, "request_kind": "tls_probe"})
    write(root / "trials/r1-synthetic-2-A/transport/http/a.connection.json", {**observed, "body_complete": False})
    result = summarize_table_bundle(root)["execution"]
    assert result["confirmed_complete_live_http_stages"] == 1
    assert result["live_stages_without_complete_http_evidence"] == 2
    write(root / "trials/r1-synthetic-0-A/outcome.json", {"status": "success", "mode": "replay"})
    assert summarize_table_bundle(root)["execution"]["confirmed_complete_live_http_stages"] == 0


def test_cell_identity_metric_is_separate_from_quote_and_display_value():
    gold = [{"record_type": "measurement", "values": {"value": "12"},
             "expected_sources": {"value": [{"ref": "value-cell", "quote": "12"}]}}]
    score = score_table_records([record(value="twelve", quote="value: 12")], gold)
    assert score["matched"] == 0 and score["matched_sources"] == 0
    assert score["matched_cell_references"] == 1
    assert score_table_records([record(ref="different-cell")], gold)["matched_cell_references"] == 0
    assert score_table_records([record(), record()], gold)["matched_cell_references"] == 1
    missing = deepcopy(gold)
    missing[0].pop("expected_sources")
    assert score_table_records([record()], missing)["matched_cell_references"] is None


def test_missing_field_cannot_match_cell_reference(tmp_path):
    gold = [{"record_type": "measurement", "values": {"value": "12", "unit": None},
             "expected_sources": {"value": [{"ref": "value-cell", "quote": "12"}], "unit": []}}]
    assert score_table_records([record()], gold)["matched_cell_references"] == 0


def fixture_case():
    return {"id": "synthetic", "source": "| Label | Value |\n|---|---|\n| A | 12 |\n",
            "task": {"name": "synthetic", "record_types": {"measurement": {"fields": {"value": {"type": "string"}}}}},
            "gold": [], "replays": {"A": {"records": []}}}


def test_summarize_old_runtime_is_readonly_but_still_checks_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"version": "first"})
    root = tmp_path / "bundle"
    runner.prepare_bundle(root, [fixture_case()])
    runner.run_bundle(root)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"version": "later"})
    assert cli.main(["summarize", "--bundle", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_calls"] == 0
    assert result["execution"]["completed_stages"] == 1
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    response = root / "trials/r1-synthetic-A/response.json"
    response.write_text('{"text": "edited"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="evidence changed"):
        runner.summarize_bundle(root)


def test_write_report_saves_summary_without_mutating_trial_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "stable"})
    root = tmp_path / "bundle"
    runner.prepare_bundle(root, [fixture_case()])
    runner.run_bundle(root)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "trials").rglob("*") if p.is_file()}
    runner.write_report(root)
    assert json.loads((root / "SUMMARY.json").read_text(encoding="utf-8"))["execution"]["completed_stages"] == 1
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "trials").rglob("*") if p.is_file()}


def test_cli_import_without_overrides_preserves_saved_a_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "stable"})
    root, imported = tmp_path / "bundle", tmp_path / "imported"
    runner.prepare_bundle(root, [fixture_case()])
    runner.run_bundle(root)
    assert cli.main(["fork-replay", "--bundle", str(root), "--output", str(imported)]) == 0
    saved = json.loads((root / "trials/r1-synthetic-A/response.json").read_text(encoding="utf-8"))
    actual = json.loads((imported / "replay-overrides.json").read_text(encoding="utf-8"))
    assert actual == {"r1-synthetic-A": saved["text"]}
    assert json.loads((imported / "manifest.json").read_text(encoding="utf-8"))["replay_only"] is True


def test_interrupted_live_response_uses_saved_reservation_mode(tmp_path):
    root = fake_bundle(tmp_path)
    trial = root / "trials/r1-synthetic-0-A"
    (trial / "outcome.json").unlink()
    write(trial / "reservation.json", {"mode": "live"})
    write(trial / "transport/http/a.connection.json", {"request_kind": "model_post",
          "observation_status": "observed", "custom_transport": False, "status_code": 200,
          "body_complete": True})
    summary = summarize_table_bundle(root)["execution"]
    assert summary["statuses"]["reserved_unknown"] == 1
    assert summary["live_reserved_stages"] == summary["saved_live_responses"] == 3
    assert summary["confirmed_complete_live_http_stages"] == 1


def test_value_matches_do_not_hide_strict_source_mismatches(tmp_path):
    root = fake_bundle(tmp_path)
    for index in range(3):
        write(root / f"trials/r1-synthetic-{index}-A/evaluation.json",
              {"issues": [], "current_results": [record(quote="full header 12")]})
    result = summarize_table_bundle(root)["quality_summary"]
    assert result["status"] == "reference_mismatches"
    assert result["routes"]["A"]["current"]["matched"] == 3
    assert result["routes"]["A"]["current"]["matched_sources"] == 0


def test_unrun_source_targets_are_unassessed_not_mismatched(tmp_path):
    root = fake_bundle(tmp_path)
    for index in range(3):
        write(root / f"trials/r1-synthetic-{index}-A/evaluation.json",
              {"issues": [], "current_results": [record()]})
    (root / "trials/r1-synthetic-2-A/evaluation.json").unlink()
    result = summarize_table_bundle(root)["quality_summary"]
    assert result["status"] == "reference_unassessed"
    score = result["routes"]["A"]["current"]
    assert score["expected_source_records"] == 3
    assert score["matched_sources"] == 2
    assert score["unmatched_sources_in_evaluated_trials"] == 0
    assert score["unassessed_source_records"] == 1

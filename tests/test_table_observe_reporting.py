"""Observe-only delivery is distinct from literal and format-equivalent scoring."""

from copy import deepcopy
import json

import pytest

from attestextract.table_reporting import (delivery_fidelity, render_table_report,
                                           score_table_records, summarize_table_bundle)


TASK = {"record_types": {"observation": {"measurement_groups": {
    "reading": {"value_field": "value", "unit_field": "unit"}}}}}


def record(value="3", unit="m", identity="S"):
    return {"record_id": "r1", "record_type": "observation", "fields": {
        "object": {"value": identity, "sources": [{"ref": "sample", "quote": "S", "offset": 0}], "relation": "row"},
        "value": {"value": value, "sources": [{"ref": "reading", "quote": "depth 3m", "offset": 0}], "relation": "cell"},
        "unit": {"value": unit, "sources": [{"ref": "reading", "quote": "depth 3m", "offset": 0}], "relation": "cell"}},
        "anchors": [{"ref": "reading", "quote": "depth 3m", "offset": 0}],
        "scope": "individual", "scope_sources": []}


def gold():
    return {"record_type": "observation", "values": {"object": "S", "value": "3", "unit": "m"},
            "expected_sources": {"value": [{"ref": "reading", "quote": "3", "offset": 6}]}}


def test_format_score_is_additive_task_scoped_and_does_not_modify_anything():
    actual, expected = record(" $3$ ", " $m$ "), gold()
    original = deepcopy((actual, expected, TASK))
    score = score_table_records([actual], [expected], task=TASK)
    assert score["matched"] == score["matched_without_aliases"] == 0
    assert score["matched_format_equivalent"] == 1
    assert score["matched_sources"] == 0
    assert (actual, expected, TASK) == original
    assert score_table_records([actual], [expected])["matched_format_equivalent"] is None
    actual["fields"]["object"]["value"] = "$S$"
    assert score_table_records([actual], [expected], task=TASK)["matched_format_equivalent"] == 0


@pytest.mark.parametrize("value,unit", [
    ("$$3$$", "m"), ("$3", "m"), ("3$", "m"), ("$3\\$", "m"),
    ("$3$ $4$", "m"), ("3", "cm"), (3, "m"), (True, "m"),
    ("3e-3", "m"), ("3±1", "m"), ("3–4", "m"), ("3", "m2"),
])
def test_format_comparison_does_not_change_scientific_values_or_json_types(value, unit):
    assert score_table_records([record(value, unit)], [gold()], task=TASK)["matched_format_equivalent"] == 0


def test_format_comparison_still_matches_one_to_one_and_honors_frozen_aliases():
    expected = gold()
    expected["aliases"] = {"unit": ["metre"]}
    actual = record("$3$", "$metre$")
    result = score_table_records([actual, actual], [expected], task=TASK)
    assert result["matched_format_equivalent"] == 1
    assert score_table_records(None, [expected], task=TASK)["matched_format_equivalent"] is None


def test_delivery_fidelity_catches_value_source_type_order_and_collection_changes():
    raw = [record(), {**record("4"), "record_id": "r2"}]
    delivered = deepcopy(raw)
    delivered[0]["field_checks"] = {"value": {"literal_check": "passed"}}
    assert delivery_fidelity(raw, delivered)["unchanged_records"] == 2
    delivered[0]["fields"]["value"]["sources"][0]["offset"] = 6
    assert delivery_fidelity(raw, delivered)["changed_indices"] == [0]
    assert delivery_fidelity(raw, list(reversed(raw)))["changed_indices"] == [0, 1]
    assert delivery_fidelity(raw, raw[:1])["changed_indices"] == [1]
    assert delivery_fidelity(raw, raw + [record()])["extra_records"] == 1
    integer = record(1)
    boolean = record(True)
    assert delivery_fidelity([integer], [boolean])["unchanged_records"] == 0
    assert delivery_fidelity([integer], [record(1.0)])["unchanged_records"] == 0
    projection = {**deepcopy(integer), "values": {"object": "S", "value": True, "unit": "m"}}
    assert delivery_fidelity([integer], [projection])["unchanged_records"] == 0
    assert delivery_fidelity(raw, None)["assessed"] is False


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_v3_report_has_fidelity_checks_and_no_repair_or_strict_result_branch(tmp_path):
    write(tmp_path / "manifest.json", {"schema_version": "table-a-0.3"})
    write(tmp_path / "slots.json", [{"slot_id": "r1-case-A", "case_id": "case", "stage": "A", "round": 1}])
    write(tmp_path / "cases.json", [{"id": "case", "source": "synthetic", "task": TASK, "gold": [gold()]}])
    trial = tmp_path / "trials/r1-case-A"
    raw = record("$3$", "$m$")
    candidate = {**deepcopy(raw), "field_checks": {}, "semantic_verification": "not_run"}
    write(trial / "parsed.json", {"records": [raw]})
    write(trial / "evaluation.json", {"schema_version": "table-route-v3", "candidates": [candidate], "issues": []})
    write(trial / "outcome.json", {"status": "success", "mode": "replay"})
    summary = summarize_table_bundle(tmp_path)["quality_summary"]
    assert set(summary["routes"]["A"]) == {"raw", "candidates"}
    assert summary["routes"]["A"]["candidates"]["matched_format_equivalent"] == 1
    assert summary["delivery_fidelity"]["unchanged_records"] == 1
    html = render_table_report(tmp_path)
    assert "交付保真" in html and "格式等价匹配" in html
    assert "旧严格规则通过结果" not in html and "工作副本修正" not in html
    candidate["fields"]["value"]["value"] = "depth 3m"
    write(trial / "evaluation.json", {"schema_version": "table-route-v3", "candidates": [candidate], "issues": []})
    summary = summarize_table_bundle(tmp_path)["quality_summary"]
    assert summary["status"] == "delivery_changed"
    assert summary["delivery_fidelity"]["changed_records"] == 1

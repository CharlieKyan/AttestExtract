"""Reports must expose failed stages without fabricating accuracy or executing HTML."""

import json

import pytest

from attestextract.table_reporting import render_table_report, score_table_records


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def record(value="12"):
    return {"record_type": "observation", "values": {"object": "A", "value": value}}


def bundle(path):
    source = '<table><tr><td>A</td><td>12<script>alert(1)</script></td></tr></table>'
    write(path / "manifest.json", {"max_calls": 1})
    write(path / "cases.json", [{"id": "test", "source": source, "task": {}, "gold": [record()]}])
    write(path / "slots.json", [{"slot_id": "r1-test-" + stage, "case_id": "test", "round": 1,
                                "stage": stage} for stage in ("A",)])
    write(path / "table-inputs.json", {"test": {"entries": [
        {"ref": "c1", "row": 0, "column": 0, "quote": "A", "start": 16, "end": 17},
        {"ref": "c2", "row": 0, "column": 1, "quote": '12<script>alert(1)</script>',
         "start": 26, "end": 53},
    ]}})
    write(path / "requests.json", {"r1-test-A": {"messages": [{"role": "user", "content": "Extract A"}]}})
    return path


def test_no_response_is_not_an_empty_success():
    unknown = score_table_records(None, [record()])
    empty = score_table_records([], [record()])
    assert unknown["evaluated"] is False and unknown["matched"] is None
    assert empty["evaluated"] is True and empty["missing"] == 1


def test_duplicate_output_cannot_match_the_same_target_twice():
    score = score_table_records([record(), record()], [record()])
    assert score["matched"] == 1 and score["extra"] == 1


def test_only_frozen_aliases_are_accepted_and_extra_content_is_not_hidden():
    gold = record()
    gold["aliases"] = {"object": ["sample A"]}
    candidate = record()
    candidate["values"]["object"] = "sample A"
    assert score_table_records([candidate], [gold])["matched"] == 1
    candidate["values"]["condition"] = "invented"
    assert score_table_records([candidate], [gold])["matched"] == 0


def test_unexecuted_report_labels_frozen_request_and_missing_dynamic_input(tmp_path):
    html = render_table_report(bundle(tmp_path))
    assert "冻结草案，尚未发送" in html
    assert "尚无该步骤产物" in html
    assert "未评估" in html
    assert "参考值匹配 0/0" not in html


def test_all_stages_raw_outputs_source_links_and_repair_are_visible(tmp_path):
    root = bundle(tmp_path)
    trial = root / "trials/r1-test-A"
    wrong = {"record_type": "observation", "fields": {
        "object": {"value": "A", "sources": [{"ref": "c1", "quote": "A"}]},
        "value": {"value": "999", "sources": [{"ref": "c2", "quote": "12"}]}}}
    write(trial / "request.json", {"messages": [{"role": "user", "content": "actual request"}]})
    write(trial / "response.json", {"text": '{"records": ["RAW RESPONSE"]}', "usage": {"output_tokens": 20}})
    write(trial / "parsed.json", {"records": [wrong]})
    write(trial / "evaluation.json", {"issues": [{"record_id": "r1", "field": "value", "code": "source_value_mismatch"}]})
    write(trial / "reconciliation.json", {"current_results": [record()], "changes": [{"before": "999", "after": "12"}]})
    write(trial / "outcome.json", {"status": "success", "mode": "replay"})
    html = render_table_report(root)
    assert "RAW RESPONSE" in html and "actual request" in html
    assert "source_value_mismatch" in html and "模型原始输出" in html and "检查后的可用结果" in html
    assert "参考值匹配 0/1" in html and "参考值匹配 1/1" in html
    assert 'href="#source-' in html and 'id="source-' in html
    assert "trials/r1-test-A/response.json" in html
    assert "replay" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_failed_response_has_error_location_not_false_accuracy(tmp_path):
    root = bundle(tmp_path)
    trial = root / "trials/r1-test-A"
    write(trial / "response.json", {"text": '{"records": [}'})
    write(trial / "error.json", {"phase": "parse", "code": "invalid_json", "offset": 14})
    write(trial / "outcome.json", {"status": "format_error", "mode": "replay"})
    html = render_table_report(root)
    assert "invalid_json" in html and "offset" in html and "format_error" in html
    assert "没有可评分的解析结果" in html


def test_report_refuses_trial_path_outside_bundle(tmp_path):
    root = bundle(tmp_path)
    write(root / "slots.json", [{"slot_id": "../../../outside", "case_id": "test", "round": 1, "stage": "A"}])
    with pytest.raises(ValueError):
        render_table_report(root)


def test_injected_fault_expectation_and_actual_diagnostic_are_both_visible(tmp_path):
    root = bundle(tmp_path)
    cases = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    cases[0]["expected_diagnostics"] = {"A": ["unknown_source_ref"]}
    write(root / "cases.json", cases)
    trial = root / "trials/r1-test-A"
    write(trial / "evaluation.json", {"issues": [{"record_id": "r1", "field": "value",
                                                  "code": "unknown_source_ref",
                                                  "source": {"ref": "absent", "quote": "12"}}]})
    html = render_table_report(root)
    assert "故障样例预设应出现的问题代码" in html
    assert "需要定位的问题" in html and "unknown_source_ref" in html
    assert "未知来源：absent" in html


def sourced_record(value="12", source="c2"):
    return {"record_type": "observation", "fields": {
        "object": {"value": "A", "sources": [{"ref": "c1", "quote": "A"}]},
        "value": {"value": value, "sources": [{"ref": source, "quote": "12", "offset": None}]}}}


def sourced_gold():
    return {**record(), "expected_sources": {
        "object": [{"ref": "c1", "quote": "A"}], "value": [{"ref": "c2", "quote": "12"}]}}


def test_same_value_from_wrong_cell_fails_source_comparison():
    score = score_table_records([sourced_record(source="other-object-cell")], [sourced_gold()])
    assert score["matched"] == 1
    assert score["source_checked"] is True and score["matched_sources"] == 0
    assert score["unmatched_sources"] == 1
    assert score["source_rows"][0]["status"] == "value_matches_but_source_differs"
    assert score["source_rows"][0]["differences"][0]["field"] == "value"


def test_exact_original_sources_match_independently_of_value_accuracy():
    score = score_table_records([sourced_record(value="999")], [sourced_gold()])
    assert score["matched"] == 0 and score["matched_sources"] == 1
    good = score_table_records([sourced_record()], [sourced_gold()])
    assert good["matched"] == good["matched_sources"] == 1


def test_missing_source_gold_and_disabled_source_check_are_not_reported_as_passed():
    no_gold = score_table_records([sourced_record()], [record()])
    assert no_gold["source_checked"] is False and no_gold["matched_sources"] is None
    assert no_gold["source_score_reason"] == "missing_gold_sources"
    disabled = score_table_records([record()], [sourced_gold()], check_sources=False)
    assert disabled["source_checked"] is False and disabled["matched_sources"] is None
    assert disabled["source_score_reason"] == "source_check_disabled"


def test_missing_actual_sources_and_duplicate_outputs_cannot_pass_source_targets():
    missing = score_table_records([record()], [sourced_gold()])
    assert missing["matched"] == 1 and missing["matched_sources"] == 0
    duplicate = score_table_records([sourced_record(), sourced_record()], [sourced_gold()])
    assert duplicate["matched_sources"] == 1 and duplicate["extra"] == 1


def test_only_prefrozen_source_quote_aliases_are_accepted():
    candidate = sourced_record()
    candidate["fields"]["object"]["sources"][0]["quote"] = "Sample A"
    assert score_table_records([candidate], [sourced_gold()])["matched_sources"] == 0
    gold = sourced_gold()
    gold["expected_sources"]["object"][0]["quote_aliases"] = ["Sample A"]
    assert score_table_records([candidate], [gold])["matched_sources"] == 1
    candidate["fields"]["object"]["sources"][0]["ref"] = "another"
    assert score_table_records([candidate], [gold])["matched_sources"] == 0


def test_report_shows_separate_value_and_source_scores_and_escapes_round(tmp_path):
    root = bundle(tmp_path)
    cases = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    cases[0]["gold"] = [sourced_gold()]
    write(root / "cases.json", cases)
    slots = json.loads((root / "slots.json").read_text(encoding="utf-8"))
    slots[0]["round"] = '<img src=x onerror="alert(1)">'
    write(root / "slots.json", slots)
    trial = root / "trials/r1-test-A"
    write(trial / "parsed.json", {"records": [sourced_record(source="other")]})
    write(trial / "evaluation.json", {"current_results": [sourced_record()], "issues": []})
    html = render_table_report(root)
    assert "参考值匹配 1/1" in html and "原格来源匹配 0/1" in html and "原格来源匹配 1/1" in html
    assert "value_matches_but_source_differs" in html
    assert '<img src=x' not in html and '&lt;img src=x' in html


def test_diagnostics_group_by_record_and_field_and_keep_pending_value(tmp_path):
    root = bundle(tmp_path)
    trial = root / "trials/r1-test-A"
    issues = [{"record_id": rid, "field": field, "code": code}
              for rid in ("first", "second") for field in ("value", "unit", "condition")
              for code in ("source_offset_mismatch", "source_binding_unresolved", "missing_field")]
    pending = {"record_id": "first", "status": "unresolved", "record": record("404"),
               "changes": [{"code": "source_offset_corrected", "before": 7, "after": 8}]}
    write(trial / "evaluation.json", {"records": [pending], "issues": issues, "current_results": []})
    write(trial / "reconciliation.json", {"unresolved": issues, "current_results": []})
    html = render_table_report(root)
    assert "受影响记录 2 条；受影响字段 6 个；诊断 18 条" in html
    assert "待复核候选" in html and "404" in html and "source_offset_corrected" in html
    assert "不代表原值变成 null" in html


def test_report_does_not_execute_or_rerender_removed_stages(tmp_path):
    root = bundle(tmp_path)
    write(root / "slots.json", [{"slot_id": "legacy", "case_id": "test", "stage": "B1", "round": 1}])
    with pytest.raises(ValueError, match="saved historical REPORT"):
        render_table_report(root)


def test_reconciliation_conflict_remains_visible_in_pending_candidates(tmp_path):
    root = bundle(tmp_path)
    trial = root / "trials/r1-test-A"
    new = {**record("13"), "record_id": "new", "status": "source_checked"}
    old = {**record("12"), "record_id": "old", "status": "source_checked"}
    write(trial / "evaluation.json", {"records": [new], "current_results": [new], "issues": []})
    write(trial / "reconciliation.json", {"current_results": [old], "history": [old], "unresolved": [
        {"code": "conflicting_checked_records", "record_id": "old", "candidate_record_id": "new"}]})
    html = render_table_report(root)
    assert "待复核候选</h3><p>2 条" in html


def test_successfully_replaced_history_is_not_pending_again(tmp_path):
    root = bundle(tmp_path)
    trial = root / "trials/r1-test-A"
    new = {**record("12"), "record_id": "new", "status": "source_checked"}
    old = {**record("404"), "record_id": "old", "status": "unresolved"}
    write(trial / "evaluation.json", {"records": [new], "current_results": [new], "issues": []})
    write(trial / "reconciliation.json", {"current_results": [new], "history": [old], "unresolved": []})
    assert "待复核候选</h3><p>0 条" in render_table_report(root)

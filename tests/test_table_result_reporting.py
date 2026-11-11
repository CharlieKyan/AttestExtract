"""Public synthetic checks for complete delivery without invented verification."""

from copy import deepcopy
import json

from attestextract.table_reporting import render_table_report, summarize_table_bundle


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def result_bundle(root):
    record = {
        "record_id": "m1", "record_type": "measurement", "values": {"object": "Sample S", "value": "12"},
        "fields": {"value": {"value": "12", "sources": [{"ref": "v", "quote": "12"}]}},
        "status": "unresolved", "semantic_verification": "not_run",
        "field_checks": {"value": {"quote_location": "located", "literal_check": "passed",
                                     "relation_check": "not_confirmed", "semantic_verification": "not_run"}},
        "mechanical_errors": [], "risk_diagnostics": [],
        "relationship_warnings": [{"record_id": "m1", "field": "object", "code": "relation_geometry_mismatch"}],
    }
    gold = {"record_type": "measurement", "values": {"object": "S", "value": "12"},
            "aliases": {"object": ["Sample S"]}}
    write(root / "manifest.json", {"schema_version": "table-a-0.2", "replay_only": True})
    write(root / "cases.json", [{"id": "synthetic", "source": "| S | 12 |", "task": {}, "gold": [gold]}])
    write(root / "slots.json", [{"slot_id": "r1-synthetic-A", "case_id": "synthetic", "round": 1, "stage": "A"}])
    write(root / "table-inputs.json", {"synthetic": {"entries": [
        {"ref": "v", "quote": "12", "start": 6, "end": 8, "row": 0, "column": 1}]}})
    trial = root / "trials/r1-synthetic-A"
    write(trial / "outcome.json", {"status": "success", "mode": "replay"})
    write(trial / "request.json", {"messages": [{"content": "NEW UNSENT DRAFT"}]})
    write(trial / "parsed.json", {"records": [record], "rejections": []})
    evaluation = {
        "schema_version": "table-route-v2", "records": [record], "candidates": [record], "current_results": [],
        "issues": record["relationship_warnings"], "relationship_warnings": record["relationship_warnings"],
        "mechanical_errors": [], "risk_diagnostics": [],
        "all_changes": [{"record_id": "m1", "reason": "source_offset_corrected", "before": 5, "after": 0}],
        "evidence": {"table_source": "| S | 12 |", "entries": [{"kind": "caption", "quote": "S measurements"}]},
    }
    write(trial / "evaluation.json", evaluation)
    write(trial / "reconciliation.json", {**evaluation, "unresolved": evaluation["issues"]})
    return root, trial, record, evaluation


def test_complete_result_not_hidden_by_strict_failure_and_not_called_verified(tmp_path):
    root, _, _, _ = result_bundle(tmp_path)
    summary = summarize_table_bundle(root)
    quality = summary["quality_summary"]
    assert quality["routes"]["A"]["candidates"]["matched"] == 1
    assert quality["routes"]["A"]["candidates"]["matched_without_aliases"] == 0
    assert quality["routes"]["A"]["current"]["matched"] == 0
    assert quality["check_categories"]["relationship_warnings"] == {
        "affected_records": 1, "affected_fields": 1, "diagnostics": 1}
    assert quality["check_categories"]["mechanical_errors"]["diagnostics"] == 0
    assert quality["semantic_verification"] == "not_run"
    html = render_table_report(root)
    for phrase in ("抽取结果（附检查状态）", "旧严格规则通过结果", "关联未自动确认", "未进行独立核验",
                   "全部工作副本修正", "source_offset_corrected", "实际输入的共享表格证据包",
                   "不使用名称别名的严格写法匹配：0/1", "完整交付不等于全部科学关系已核验"):
        assert phrase in html
    assert "待复核候选" not in html
    assert "旧严格规则诊断（含关联未确认）" in html
    assert '<h3 class="bad">需要定位的问题</h3>' not in html
    assert '<td class="bad">relation_geometry_mismatch' not in html
    assert html.index("记录 m1") < html.index("模型原始输出与旧严格结果对照")


def test_mechanical_errors_and_risks_remain_visible_and_separate(tmp_path):
    root, trial, record, evaluation = result_bundle(tmp_path)
    mechanical = {"record_id": "m1", "field": "value", "code": "literal_incomplete"}
    risk = {"record_id": "m1", "field": "object", "code": "group_identity_narrowed"}
    record["mechanical_errors"] = [mechanical]
    record["risk_diagnostics"] = [risk]
    evaluation.update(mechanical_errors=[mechanical], risk_diagnostics=[risk])
    evaluation["issues"] = [*evaluation["issues"], mechanical, risk]
    write(trial / "evaluation.json", evaluation)
    write(trial / "reconciliation.json", {**evaluation, "unresolved": evaluation["issues"]})
    quality = summarize_table_bundle(root)["quality_summary"]
    assert all(value["affected_records"] == value["diagnostics"] == 1
               for value in quality["check_categories"].values())
    html = render_table_report(root)
    assert "格式与原文字面检查" in html and "需要优先检查的具体风险" in html
    assert "未确认不等于数值错误" in html
    assert "literal_incomplete" in html and "group_identity_narrowed" in html


def test_partial_format_error_shows_raw_index_error_and_is_never_full_success(tmp_path):
    root, trial, record, _ = result_bundle(tmp_path)
    rejected = {"index": 1, "raw": {"record_id": "broken", "value": "<script>bad()</script>"},
                "errors": [{"loc": ["fields", "value", "relation"], "msg": "Missing relation", "type": "missing"}]}
    write(trial / "parsed.json", {"records": [record], "record_indices": [0], "rejections": [rejected]})
    write(trial / "outcome.json", {"status": "partial_format_error", "mode": "replay"})
    result = summarize_table_bundle(root)
    assert result["execution"]["completed_stages"] == 0
    assert result["execution"]["isolated_records"] == result["execution"]["parse_diagnostics"] == 1
    assert result["quality_summary"]["status"] == "partial_format_error"
    html = render_table_report(root)
    assert "部分格式错误" in html and "原始数组位置 1" in html and "Missing relation" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "记录 m1" in html


def test_historical_request_is_separate_from_current_unsent_draft(tmp_path):
    root, _, _, _ = result_bundle(tmp_path)
    provenance = root / "replay-provenance/r1-synthetic-A"
    write(provenance / "origin.json", {"mode": "live", "overridden": False})
    write(provenance / "request.json", {"messages": [{"content": "OLD ACTUAL REQUEST"}]})
    write(provenance / "response.json", {"text": "OLD RESPONSE"})
    html = render_table_report(root)
    assert "产生原回答的历史实际请求" in html and "OLD ACTUAL REQUEST" in html
    assert "本轮回放请求草案（未发送，不冒充历史实际请求）" in html and "NEW UNSENT DRAFT" in html
    assert 'href="replay-provenance/r1-synthetic-A/request.json"' in html
    assert summarize_table_bundle(root)["execution"]["confirmed_complete_live_http_stages"] == 0
    write(provenance / "origin.json", {"mode": "replay", "overridden": True})
    html = render_table_report(root)
    assert "历史回放请求（未实际发送）" in html and "本轮使用人为替换的回答" in html
    assert "产生原回答的历史实际请求" not in html


def test_all_records_isolated_is_format_error_even_with_frozen_gold(tmp_path):
    root, trial, _, _ = result_bundle(tmp_path)
    write(trial / "parsed.json", {"records": [], "record_indices": [], "rejections": [
        {"index": 0, "raw": 42, "errors": [{"loc": [], "msg": "Expected record", "type": "model_type"}]}]})
    write(trial / "outcome.json", {"status": "format_error", "mode": "replay"})
    for name in ("evaluation.json", "reconciliation.json"):
        write(trial / name, {"records": [], "candidates": [], "current_results": [], "issues": []})
    result = summarize_table_bundle(root)
    assert result["quality_summary"]["routes"]["A"]["raw"]["evaluated_trials"] == 1
    assert result["quality_summary"]["status"] == "format_error"
    assert result["execution"]["parsed_records"] == 0
    assert result["execution"]["partial_format_error_stages"] == 0
    assert result["execution"]["completed_stages"] == 0
    assert "格式错误：不能将无法解析的内容视为空成功" in render_table_report(root)


def test_same_record_id_in_two_trials_counts_as_two_affected_records(tmp_path):
    root, trial, record, evaluation = result_bundle(tmp_path)
    slots = json.loads((root / "slots.json").read_text(encoding="utf-8"))
    slots.append({**slots[0], "slot_id": "r2-synthetic-A", "round": 2})
    write(root / "slots.json", slots)
    second = root / "trials/r2-synthetic-A"
    write(second / "evaluation.json", deepcopy(evaluation))
    write(second / "parsed.json", {"records": [record]})
    summary = summarize_table_bundle(root)["quality_summary"]
    assert summary["check_categories"]["relationship_warnings"] == {
        "affected_records": 2, "affected_fields": 2, "diagnostics": 2}

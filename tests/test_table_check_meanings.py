"""Position support must not appear to prove membership or scientific meaning."""

from copy import deepcopy
import json

import pytest

from attestextract.table_reporting import render_table_report, table_check_label
from attestextract.table_routes import build_table_input, evaluate_route, parse_route_response_partial


def cumulative_case(scope="group", scope_text="Combined volume/L"):
    source = f"| Item | Volume/L |\n| --- | --- |\n| {scope_text} | 12 |\n"
    table = build_table_input(source)

    def cite(text):
        entry = next(item for item in table["entries"] if item["quote"] == text)
        return {"ref": entry["ref"], "quote": text, "offset": None}

    raw = {"record_id": "total", "record_type": "measurement", "scope": scope,
           "scope_sources": [cite(scope_text)], "anchors": [cite("12")], "fields": {
               "object": {"value": "A and B combined", "sources": [cite(scope_text)], "relation": "row"},
               "value": {"value": "12", "sources": [cite("12")], "relation": "anchor"},
               "unit": {"value": "L", "sources": [cite("Volume/L")], "relation": "column"},
           }}
    task = {"name": "Synthetic cumulative membership", "record_types": {"measurement": {
        "fields": {key: {"type": "string", "required": True} for key in ("object", "value", "unit")},
        "identity_fields": ["object"],
        "measurement_groups": {"volume": {"value_field": "value", "unit_field": "unit"}},
    }}}
    return source, table, task, raw


def evaluate(table, task, raw):
    parsed = parse_route_response_partial("A", json.dumps({"records": [raw]}))
    snapshot = deepcopy(parsed)
    result = evaluate_route("A", parsed, table, task)
    candidate = result["candidates"][0]
    assert parsed == snapshot
    assert {key: candidate[key] for key in parsed["records"][0]} == parsed["records"][0]
    return result


@pytest.mark.parametrize("scope,text", [
    ("group", "Combined volume/L"), ("group", "A and B"), ("each", "each A and B"),
])
def test_located_total_or_member_names_do_not_prove_membership(scope, text):
    _, table, task, raw = cumulative_case(scope, text)
    candidate = evaluate(table, task, raw)["candidates"][0]
    assert candidate["scope_check"] == {
        "declared_scope": scope, "scope_quote_location": "located", "member_evidence_check": "not_run"}
    assert candidate["field_checks"]["object"]["relation_check"] == "locally_supported"
    assert candidate["field_checks"]["object"]["relation_check_basis"] == "local_position_rules"
    assert candidate["field_checks"]["value"]["literal_check"] == "passed"
    assert all(check["semantic_verification"] == "not_run" for check in candidate["field_checks"].values())


@pytest.mark.parametrize("mutation,expected", [("empty", "missing"), ("unknown", "missing"), ("repeat", "ambiguous")])
def test_missing_and_ambiguous_scope_quotes_are_independent_of_member_verification(mutation, expected):
    _, table, task, raw = cumulative_case(scope_text="total total")
    if mutation == "empty":
        raw["scope_sources"] = []
    elif mutation == "unknown":
        raw["scope_sources"][0]["ref"] = "unknown-cell"
    else:
        raw["scope_sources"][0]["quote"] = "total"
    candidate = evaluate(table, task, raw)["candidates"][0]
    assert candidate["scope_check"]["scope_quote_location"] == expected
    assert candidate["scope_check"]["member_evidence_check"] == "not_run"
    assert candidate["values"]["value"] == "12"


def test_individual_scope_is_a_model_declaration_not_verified_membership():
    _, table, task, raw = cumulative_case(scope="individual")
    raw["scope_sources"] = []
    candidate = evaluate(table, task, raw)["candidates"][0]
    assert candidate["scope_check"] == {
        "declared_scope": "individual", "scope_quote_location": "not_applicable",
        "member_evidence_check": "not_applicable"}
    assert candidate["semantic_verification"] == "not_run"
    assert "未验证该声明" in table_check_label("not_applicable", check="member_evidence_check")


def test_report_separates_position_scope_and_scientific_checks_and_handles_old_results(tmp_path):
    source, table, task, raw = cumulative_case()
    evaluation = evaluate(table, task, raw)

    def write(relative, value):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    write("manifest.json", {"schema_version": "table-a-0.3"})
    write("slots.json", [{"slot_id": "r1-total-A", "case_id": "total", "stage": "A", "round": 1}])
    write("cases.json", [{"id": "total", "source": source, "task": task}])
    write("table-inputs.json", {"total": table})
    write("trials/r1-total-A/evaluation.json", evaluation)
    html = render_table_report(tmp_path)
    for phrase in ("局部位置规则符合（非科学核验）", "范围引文定位：已定位", "未逐项核验成员及其出处",
                   "科学关系独立核验", "数值／单位字面检查", "未进行独立核验"):
        assert phrase in html
    assert "本地关联规则支持" not in html
    for candidate in evaluation["candidates"]:
        del candidate["scope_check"]
    write("trials/r1-total-A/evaluation.json", evaluation)
    old_html = render_table_report(tmp_path)
    assert "此产物未记录成员逐项出处核验状态" in old_html
    assert "不能视为成员依据完整" in old_html

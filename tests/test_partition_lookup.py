"""A source-declared partition adds lookups only to the participating occurrences."""

import pytest

from niceextract import assemble_dataset, extract, query_dataset
from niceextract.backends import ReplayBackend


def _claim(field, subject, value, subject_quote, value_quote):
    return {"record_type": field, "name_handling": "as_reported",
            "values": {"entity": subject, "value": value, "unit": None, "conditions": None},
            "quotes": {"entity": subject_quote, "value": value_quote, "unit": "", "conditions": ""}}


@pytest.mark.parametrize("parent,transition,supported", [
    ("东厂", "", True),
    ("东厂和东站", "", False),
    ("东厂", "转向其他批组；", False),
])
def test_raw_partition_alias_is_an_additional_lookup_on_one_original_fact(parent, transition, supported):
    first, second, elsewhere = "东早班使用蓝签", "东晚班使用红签", "东早班使用绿签"
    opening = "按照工艺将" + parent + "分为早班和晚班，" + first
    text = opening + "；" + transition + second + "。另一个厂有独立的记录。" + elsewhere + "。"
    claims = [_claim("n", "东早班", "东早班", first, opening), _claim("n", "东晚班", "东晚班", second, second),
              _claim("tag", "东早班", "使用蓝签", first, first), _claim("tag", "东晚班", "使用红签", second, second),
              _claim("tag", "东早班", "使用绿签", elsewhere, elsewhere)]
    run = extract(text, prompt_description="登记各班次报告的作业内容。", field_list={"n": "班次", "tag": "作业内容"},
                  table_subject="每个班次", backend=ReplayBackend([{"records": claims}], output_protocol="flat-v1"))
    dataset = assemble_dataset(text, run.model_dump(mode="json"), None, subject_label_field="n")
    for member, expected in [("早班", "使用蓝签"), ("晚班", "使用红签")]:
        by_short = query_dataset(dataset, "东" + member, field="tag")
        by_full = query_dataset(dataset, parent + member, field="tag")
        assert {o["value"] for o in by_short} >= {expected}
        assert [o["value"] for o in by_full] == ([expected] if supported else [])
        if supported:
            assert by_full[0]["observation_id"] in {o["observation_id"] for o in by_short}
            assert all(text[e["start"]:e["end"]] == e["quote"] for e in by_full[0]["direct"]["partition_evidence"])


@pytest.mark.parametrize("predicate,confirmed", [("的标签为", True), ("使用", False)])
def test_member_name_can_be_found_before_its_requested_field_is_confirmed(predicate, confirmed):
    first, second = "东早班" + predicate + "蓝签", "东晚班" + predicate + "红签"
    intro = "车间将东厂分为早班和晚班，" + first
    source = intro + "；" + second + "。"
    claims = [_claim("n", "东早班", "东早班", first, intro), _claim("n", "东晚班", "东晚班", second, second),
              _claim("tag", "东早班", "蓝签", first, first), _claim("tag", "东晚班", "红签", second, second)]
    run = extract(source, prompt_description="登记班次和标签。", field_list={"n": "班次", "tag": "标签"},
                  table_subject="每个班次", backend=ReplayBackend([{"records": claims}], output_protocol="flat-v1"))
    dataset = assemble_dataset(source, run.model_dump(mode="json"), None, subject_label_field="n")
    for name, value in [("早班", "蓝签"), ("晚班", "红签")]:
        assert [o["value"] for o in query_dataset(dataset, "东厂" + name, field="tag")] == ([value] if confirmed else [])
        obs, = query_dataset(dataset, "东厂" + name, exploratory=True, field="tag")
        assert obs["entity"] == "东" + name and obs["value"] == value


@pytest.mark.parametrize("definition,has_alias", [("试验将新竹批分为早期和晚期", True), ("另有两个阶段", False)])
def test_verified_partition_restores_local_member_without_separate_identity_records(definition, has_alias):
    first, second = "新早期表现稳定", "新晚期表现松散"
    source = definition + "，" + first + "；" + second + "。"
    records = [_claim("state", "新早期", "表现稳定", first, first),
               _claim("state", "新晚期", "表现松散", second, second)]
    run = extract(source, prompt_description="登记阶段和观察结果。", field_list={"name": "阶段", "state": "观察结果"},
                  table_subject="各个阶段", backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))
    archive = run.analysis
    owner_evidence = archive["observations"][0]["evidence_ids"][0]
    archive["evidence"].append({"evidence_id": "definition", "start": 0, "end": len(definition), "quote": definition})
    archive["objects"] = [
        {"object_id": key, "name": label, "kind": "instance", "scope": None, "is_table_subject": member,
         "mention_evidence_ids": [citation], "semantic_status": "model_proposed"}
        for key, label, member, citation in [("parent", "新竹批", True, "definition"),
                                             ("early", "新早期", False, owner_evidence)]]
    archive["relations"] = [{"relation_id": "proposal", "predicate": "part_of", "source_id": "parent",
                             "target_id": "early", "evidence_ids": ["definition"], "semantic_status": "model_proposed"}]
    dataset = assemble_dataset(source, run.model_dump(mode="json"), archive, subject_label_field="name")
    for name in ("早期", "晚期"):
        full = query_dataset(dataset, "新竹批" + name)
        assert bool(full) == has_alias
        if full:
            assert full == query_dataset(dataset, "新" + name)
    if not has_alias:
        assert not query_dataset(dataset, "新早期")

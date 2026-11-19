import json

from attestextract.jev_field import field_request


def test_field_check_sees_literal_cell_and_source_without_the_ownership_verdict():
    source = "Vessel A has a bronze lining, in two bands."
    state = {"definition": "Material used for the vessel lining.", "row_subject": "vessels",
             "observation": {"value": "two bands", "unit": None, "entity": "Vessel A"},
             "subjects": {"bind_1": "some proposed owner"}, "guard_reasons": [],
             "source": [{"start": 0, "end": len(source), "quote": source}]}
    original = {"request": {"state": json.dumps(state)}, "decision": {"choice": "bind_1", "confidence": 0.9}}
    request = field_request(original)
    assert request["state"] == {"字段定义": "Material used for the vessel lining.", "候选值": "two bands",
                                "单位": None, "原文引文": [source]}
    assert list(request["questions"]) == ["disposition"]
    question = request["questions"]["disposition"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == ["supported", "mismatch", "uncertain"]
    assert "bind_1" not in json.dumps(request)


def test_numeric_cell_retains_unit_and_exact_ordered_table_evidence():
    state = {"definition": "Initial yield in kilograms.", "observation": {"value": "3.20", "unit": "kg"},
             "source": [{"quote": "Batch 02"}, {"quote": "Initial yield / kg"}, {"quote": "3.20"}]}
    request = field_request({"request": {"state": json.dumps(state)}})
    assert request["state"]["候选值"] == "3.20" and request["state"]["单位"] == "kg"
    assert request["state"]["原文引文"] == ["Batch 02", "Initial yield / kg", "3.20"]

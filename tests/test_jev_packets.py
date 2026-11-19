import json
from copy import deepcopy

import pytest

from attestextract.jev_packets import make_packets
from test_dataset_admission import assemble


def pending():
    source = "S2 underwent testing and its measured length was 30 cm."
    data = assemble(source, source, [("S2", "S2")], None, match="unknown",
                    definition="Measured length of a specimen")
    return source, data


def test_value_statement_and_registration_support_a_bounded_choice():
    source, data = pending()
    unknown = data["observations"][0]
    assert unknown["direct"]["status"] == unknown["fit"]["status"] == "unknown"
    confirmed = dict(unknown, delivery="confirmed", observation_id="already-confirmed")
    data["observations"].append(confirmed)
    original = deepcopy(data)
    [packet] = make_packets(data, source)
    assert packet["observation_id"] == unknown["observation_id"] and packet["guard_reasons"] == []
    options = packet["options"]
    assert options["bind_1"]["label"] == "S2" and options["bind_1"]["id"] == "o0"
    assert [s["quote"] for s in options["bind_1"]["evidence"]] == [source, "S2"]
    question = packet["request"]["questions"]["disposition"]
    assert question["type"] == "choice" and list(question["criteria"]) == ["bind_1", "review", "reject"]
    assert json.loads(packet["request"]["state"])["definition"] == "Measured length of a specimen"
    assert data == original


@pytest.mark.parametrize("source,names,entity", [
    ("S2: length never reached 30 cm.", [("S2", "S2")], "S2"),
    ("S2 and T1 were observed. One unidentified sample had length = 30 cm.",
     [("S2", "S2"), ("T1", "T1")], None),
    ("S2 and T1 were observed; one unidentified sample had length = 30 cm.",
     [("S2", "S2"), ("T1", "T1")], None),
    ("Either S2 or T3 length = 30 cm.", [("S2", "S2"), ("T3", "T3")], None),
    ("First S2 was observed. Its shell length was 30 cm.", [("S2", "S2")], "S2 shell"),
])
def test_negative_ambiguous_and_indirect_assertions_still_reach_review(source, names, entity):
    data = assemble(source, source, names, entity)
    [packet] = make_packets(data, source)
    assert not packet["options"] and packet["guard_reasons"]
    assert list(packet["request"]["questions"]["disposition"]["criteria"]) == ["review", "reject"]


@pytest.mark.parametrize("change,reason", [
    ({"target_match": "unsupported"}, "field_contradicted"),
    ({"fit_blockers": ["allowed_units"]}, "allowed_units"),
    ({"fit": {"status": "unknown", "reason": "field_qualifier_unverified"}}, "unreported_field_qualification"),
    ({"purpose": "classification_threshold"}, "collective_or_classification"),
    ({"source_kind": "table", "table_context": {"gap": "owner_cell_unresolved"}}, "table_scope_unresolved"),
    ({"value": "300"}, "value_without_unique_source"),
])
def test_hard_limits_cannot_be_overridden_by_a_model_choice(change, reason):
    source, data = pending()
    data["observations"][0].update(change)
    [packet] = make_packets(data, source)
    assert reason in packet["guard_reasons"] and packet["options"] == {}


def test_grounded_owner_can_use_original_name_when_registered_name_has_scope_note():
    source, data = pending()
    obs = data["observations"][0]
    obs.update(entity="S2", subject_id="o0", binding_status="supported")
    data["lineage"]["objects"][0]["name"] = "S2 (first test run)"
    [packet] = make_packets(data, source)
    assert packet["options"]["bind_1"]["label"] == "S2"
    data["lineage"]["objects"][0]["mention_evidence_ids"] = []
    [packet] = make_packets(data, source)
    assert not packet["options"]


def test_overlong_task_can_be_scored_without_accepting_truncated_evidence():
    source, data = pending()
    data["field_definitions"]["reading"] += " extended definition" * 2500
    [packet] = make_packets(data, source)
    assert packet["options"] == {} and "evidence_exceeds_prompt_limit" in packet["guard_reasons"]
    assert len(json.dumps(packet["request"], ensure_ascii=False).encode()) < 8192


def test_a_changed_source_or_duplicate_observation_id_fails_before_review():
    source, data = pending()
    with pytest.raises(ValueError, match="source differs"):
        make_packets(data, source + " ")
    data["observations"].append(data["observations"][0])
    with pytest.raises(ValueError, match="unique observation"):
        make_packets(data, source)

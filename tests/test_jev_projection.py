"""Exercise the delivered CSV and strict query after closed-choice review."""

import csv
from copy import deepcopy

import pytest

from attestextract import query_dataset, save_dataset
from attestextract.jev_projection import final_dataset
from test_dataset_admission import assemble


def _choice(obs, subject_id="o0", label="S2"):
    anchors = [{**e["span"], "quote": e["quote"]} for e in obs["source_evidence"] if e.get("span")]
    return {"observation_id": obs["observation_id"], "guard_reasons": [],
            "options": {"subject_1": {"id": subject_id, "label": label, "scope": "East", "evidence": anchors}}}


def _run(data, packet, choice):
    decisions = {} if choice is None else {packet["observation_id"]: {"choice": choice, "confidence": 0.83}}
    return final_dataset(data, [packet], decisions)


def _ambiguous():
    statement = "S2 length = 30 cm."
    source = "East: S2. West: S2. " + statement
    return assemble(source, statement, [("S2", "East"), ("S2", "West")], "S2")


@pytest.mark.parametrize("choice,state,present", [(None, "not_scored", True), ("review", "review", True),
                                                  ("reject", "rejected", False)])
def test_unanswered_and_abstained_keep_provisional_rejection_keeps_detail(choice, state, present):
    data = _ambiguous()
    original = deepcopy(data)
    output = _run(data, _choice(data["observations"][0]), choice)
    assert data == original and not query_dataset(output)
    assert output["rows"] == (data["rows"] if present else [])
    [obs] = query_dataset(output, "S2", exploratory=True)
    assert obs["value"] == "30" and obs["jev_review"]["state"] == state
    assert bool(obs["row_ids"]) == present


def test_scored_binding_exports_one_measure_with_attribution_and_exact_sources(tmp_path):
    data = _ambiguous()
    packet = _choice(data["observations"][0])
    output = _run(data, packet, "subject_1")
    [obs] = query_dataset(output, "o0", field="reading")
    assert not query_dataset(output, "o1")
    assert obs["value"] == "30" and obs["unit"] == "cm"
    assert obs["jev_review"]["evidence"] == packet["options"]["subject_1"]["evidence"]
    assert obs["jev_review"]["prior_axes"]["direct"]["status"] == "unknown"
    [proof] = output["lineage"]["rows"]
    assert proof["jev_observations"] == [obs["observation_id"]]
    assert proof["row_id"] in obs["row_ids"]
    save_dataset(output, tmp_path / "final")
    with (tmp_path / "final/main.csv").open(encoding="utf-8-sig", newline="") as stream:
        [row] = csv.DictReader(stream)
    assert row["name"] == "S2" and row["reading"] == "30" and "Jev" in row["记录状态"]
    assert not query_dataset(data)


def test_wrong_option_guard_or_transfer_cannot_overwrite_a_binding():
    data = _ambiguous()
    obs = data["observations"][0]
    packet = _choice(obs)
    with pytest.raises(ValueError, match="absent"):
        _run(data, packet, "nonexistent")
    with pytest.raises(ValueError, match="constraint"):
        _run(data, {**packet, "guard_reasons": ["excluded_by_policy"]}, "subject_1")
    obs["direct"].update(status="supported", id="part", label="S2 component")
    with pytest.raises(ValueError, match="transfers"):
        _run(data, packet, "subject_1")


@pytest.mark.parametrize("other_guard,state,rows", [
    ([], "rejected_as_confirmed", 1), (["field_contradicted"], "rejected", 0),
])
def test_qualification_alone_preserves_reported_value_after_model_refuses_confirmation(other_guard, state, rows):
    source = "S2: mean length = 30 cm."
    data = assemble(source, source, [("S2", "S2")], "S2", definition="Usable length of the specimen.",
                    property_name="mean length")
    [baseline] = data["observations"]
    packet = {**_choice(baseline), "options": {}, "guard_reasons": ["unreported_field_qualification", *other_guard]}
    output = _run(data, packet, "reject")
    assert len(output["rows"]) == rows and not query_dataset(output)
    [obs] = query_dataset(output, exploratory=True)
    assert obs["jev_review"]["state"] == state and obs["value"] == "30"
    if rows:
        assert output["rows"] == data["rows"] and obs["delivery"] == "provisional"
        [proof] = output["lineage"]["rows"]
        assert proof["status"].startswith(data["lineage"]["rows"][0]["status"]) and "Jev" in proof["status"]


def test_model_cannot_supply_a_missing_composite_identity():
    data = _ambiguous()
    data["identity_fields"].append("serial")
    data["schema"].append("serial")
    data["rows"][0]["serial"] = None
    output = _run(data, _choice(data["observations"][0]), "subject_1")
    assert output["rows"] == data["rows"] and not query_dataset(output)
    [obs] = output["observations"]
    assert obs["jev_review"]["state"] == "selected_without_row" and obs["delivery"] == "provisional"


def test_exact_source_repeat_keeps_single_strict_fact_and_its_original_row():
    data = assemble("S2 length = 30 cm.", "S2 length = 30 cm.", [("S2", "S2")], "S2")
    [confirmed] = data["observations"]
    proposed = deepcopy(confirmed)
    proposed.update(observation_id="duplicate-proposal", delivery="detail", row_ids=[])
    proposed["source_evidence"] = [{"fields": ["value"], "quote": "30", "span": {"start": 12, "end": 14}}]
    data["observations"].append(proposed)
    output = _run(data, _choice(proposed), "subject_1")
    assert output["rows"] == data["rows"] and output["lineage"] == data["lineage"]
    assert query_dataset(output) == [confirmed]
    assert output["observations"][1]["jev_review"]["same_observation_as"] == confirmed["observation_id"]


def test_single_original_table_group_enriched_without_replacing_confirmed_cell():
    data = assemble("S2 length = 30 cm.", "S2 length = 30 cm.", [("S2", "S2")], "S2")
    [confirmed] = data["observations"]
    data["schema"].append("width")
    data["rows"][0]["width"] = None
    confirmed["record_group"] = "source-row-A"
    proposed = deepcopy(confirmed)
    proposed.update(observation_id="pending-width", field="width", value="14", delivery="detail", row_ids=[])
    quote = "S2 width = 14 cm."
    proposed["source_evidence"] = [{"fields": ["value"], "quote": quote,
                                    "span": {"start": 20, "end": 20 + len(quote), "quote": quote}}]
    data["observations"].append(proposed)
    output = _run(data, _choice(proposed), "subject_1")
    assert output["rows"] == [{"name": "S2", "reading": "30", "width": "14"}]
    [proof] = output["lineage"]["rows"]
    assert proof["row_id"] == data["lineage"]["rows"][0]["row_id"]
    assert proof["cells"]["width"] == ["pending-width"] and proof["jev_observations"] == ["pending-width"]
    assert data["rows"][0]["width"] is None


def test_model_assignments_to_two_subjects_at_one_value_do_not_duplicate_the_reading():
    data = _ambiguous()
    first = data["observations"][0]
    second = deepcopy(first)
    second.update(observation_id="independent-candidate", delivery="detail", row_ids=[])
    data["observations"].append(second)
    packets = [_choice(first), _choice(second, subject_id="o1")]
    decisions = {p["observation_id"]: {"choice": "subject_1", "confidence": 0.9} for p in packets}
    output = final_dataset(data, packets, decisions)
    assert output["rows"] == data["rows"] and not query_dataset(output)
    assert {o["jev_review"]["state"] for o in output["observations"]} == {"conflicting_assignment"}


def test_sourced_short_and_long_names_find_original_and_model_reviewed_facts():
    data = assemble("S2 length = 30 cm.", "S2 length = 30 cm.", [("S2", "S2")], "S2")
    [confirmed] = data["observations"]
    proposed = deepcopy(confirmed)
    proposed.update(observation_id="later-reading", delivery="detail", row_ids=[], value="40")
    quote = "East S2 length = 40 cm."
    proposed["source_evidence"] = [{"fields": ["value"], "quote": quote,
                                    "span": {"start": 20, "end": 20 + len(quote)}}]
    for axis in ("direct", "target"):
        proposed[axis].update(status="unknown", id=None, label=None)
    data["observations"].append(proposed)
    output = _run(data, _choice(proposed, label="East S2"), "subject_1")
    for name in ("S2", "East S2", "o0"):
        assert {o["value"] for o in query_dataset(output, name)} == {"30", "40"}
    assert query_dataset(output, "S2")[0]["direct"]["jev_alias_sources"]["East S2"]["observation_id"] == "later-reading"

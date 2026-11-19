"""A second lookup can resolve a pending owner without erasing the first proposal."""

from attestextract import extract
from test_entity_first_engine import Backend, card, cite, fact, run


def revisit_answers(packet):
    window = packet["windows"][0]
    stage = packet["stage"]
    if stage == "inventory":
        return {"objects": []}
    if stage == "confirm":
        return {"decisions": [{"key": "B", "status": "new_object", "evidence": cite(window, "Unit B"),
                               "reason": "Named study unit."}]}
    if "installed" in window["text"]:
        return {"nominations": [card(window, name="B")], "facts": []}
    owner = packet["objects"][0]["object_id"] if stage == "revisit" else None
    claim = fact(window, owner, name="B", value="60", line="B load 60.")
    if stage != "revisit":
        return {"nominations": [], "facts": [claim]}
    return {"nominations": [], "facts": [claim, dict(claim, owner="not-shown"), claim,
                                          dict(claim, purpose="model_input"),
                                          dict(claim, value="600", value_source=cite(window, "600"))]}


def test_revisit_history_links_the_original_replacement_and_each_rejection():
    report = run("B load 60.\n\nUnit B was installed.", Backend(revisit_answers), max_chars=32)
    result, = report["observations"]
    assert result["qualified"] and result["owner_id"] == report["objects"][0]["object_id"]
    oid = result["observation_id"]
    decisions = report["calls"][-1]["revisit_decisions"]
    assert [d["fact_index"] for d in decisions] == list(range(5))
    assert [d["action"] for d in decisions] == ["replaced", "rejected", "coalesced", "rejected", "rejected"]
    assert [d["observation_id"] for d in decisions] == [oid, oid, oid, oid, None]
    previous = decisions[0]["previous_observation"]
    assert previous["observation_id"] == oid and previous["model_claim"]["owner"] is None
    assert previous["field_supported"] and not previous["owner_supported"]
    rejected = [i for i in report["issues"] if "rejected_fact" in i]
    assert [(i["fact_index"], i["observation_id"], i["reason"]) for i in rejected] == [
        (1, oid, "revisit_owner_not_shown"), (3, oid, "revisit_changes_measurement"),
        (4, None, decisions[4]["reason"])]
    assert rejected[-1]["rejected_fact"]["value"] == "600"


def audited(reply):
    return extract("B load 60.\n\nUnit B was installed.", field_list={"unit": "Unit identifier", "load": "Load"},
                   prompt_description="Extract measured loads.", table_subject="individual units",
                   subject_strategy="entity-first", dataset_options={"subject_label_field": "unit"},
                   max_chunk_chars=32, backend=Backend(reply)).dataset


def test_audited_dataset_uses_only_the_successful_revisit_for_the_formal_row():
    data = audited(revisit_answers)
    assert data["rows"] == [{"unit": "B", "load": "60"}]
    assert len(data["observations"]) == 1
    original, accepted, wrong_owner, duplicate, changed, invented = data["proposals"]
    assert original["disposition"] == "superseded" and not original["row_ids"]
    assert original["observation_id"] == accepted["observation_id"] == data["observations"][0]["observation_id"]
    assert accepted["row_ids"] == duplicate["row_ids"] and len(accepted["row_ids"]) == 1
    assert duplicate["prior_disposition"] == "coalesced"
    assert original["prior_decisions"]["field_supported"] and not original["prior_decisions"]["qualified"]
    for rejected in (wrong_owner, changed, invented):
        assert rejected["prior_disposition"] == "rejected" and not rejected["row_ids"]


def test_repeated_original_proposal_cannot_displace_its_verified_revisit():
    def reply(packet):
        answer = revisit_answers(packet)
        if packet["stage"] == "attributes" and answer["facts"]:
            answer["facts"] *= 2
        return answer

    data = audited(reply)
    assert data["rows"] == [{"unit": "B", "load": "60"}]
    original, repeat = data["proposals"][:2]
    assert original["disposition"] == repeat["disposition"] == "superseded"
    assert not repeat["row_ids"]
    assert original["observation_id"] == repeat["observation_id"]

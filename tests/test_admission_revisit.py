"""A second lookup can resolve a pending owner without erasing the first proposal."""

import json
from pathlib import Path

from attestextract import assemble_dataset


ARCHIVE = Path(__file__).parent / "fixtures/entity_first_v2/revisit.json"
SOURCE = "B load 60.\n\nUnit B was installed."


def test_archived_revisit_preserves_the_original_replacement_and_each_rejection():
    report = json.loads(ARCHIVE.read_bytes())["entity_first"]
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


def test_archived_dataset_uses_only_the_successful_revisit_for_the_formal_row():
    data = assemble_dataset(SOURCE, json.loads(ARCHIVE.read_bytes()))
    assert data["rows"] == [{"unit": "B", "load": "60"}]
    assert len(data["observations"]) == 1
    original, repeated, accepted, wrong_owner, duplicate, changed, invented = data["proposals"]
    assert repeated["disposition"] == "superseded" and repeated["observation_id"] == original["observation_id"]
    assert original["disposition"] == "superseded" and not original["row_ids"]
    assert original["observation_id"] == accepted["observation_id"] == data["observations"][0]["observation_id"]
    assert accepted["row_ids"] == duplicate["row_ids"] and len(accepted["row_ids"]) == 1
    assert duplicate["prior_disposition"] == "coalesced"
    assert original["prior_decisions"]["field_supported"] and not original["prior_decisions"]["qualified"]
    for rejected in (wrong_owner, changed, invented):
        assert rejected["prior_disposition"] == "rejected" and not rejected["row_ids"]

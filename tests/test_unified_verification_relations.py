"""A single model-facing relation list preserves observation-local and object-level claims."""

import json

import pytest

from niceextract.verification_protocol import UnifiedStep, VerificationValidationError, apply_decision, parse_unified_step
from test_verification_protocol import decision, inputs, quote


@pytest.mark.parametrize("marker,admitted", [("S1", True), ("81", False)])
def test_explicit_handle_in_long_registration_quote_for_a_composed_display_name(marker, admitted):
    introduction = "In run 81 the plate was labelled S1."
    statement = "S1 was cut from block P."
    proof = [quote(introduction), quote(statement)]
    objects = [{"object_id": "plate", "name": "East run 81 coupon S1", "kind": "sample", "scope": "east",
                "is_table_subject": True, "mentions": [proof[0]]},
               {"object_id": "block", "name": "block P", "kind": "instance", "scope": "east",
                "is_table_subject": True, "mentions": [quote("block P")]}]
    wire = decision()
    wire["bindings"][0].update(subject_id=None, status="unknown", evidence=[])
    wire.update(objects=objects, relations=[{"source_id": "plate", "target_id": "block", "predicate": "sample_of",
        "evidence": proof, "mention": quote(marker, occurrence=0), "target_mention": quote("block P")}])
    reviews, contexts = [], {}
    _, edges, _, entries = apply_decision(parse_unified_step(json.dumps(wire)),
        **inputs(introduction + " " + statement), review_items=reviews, evidence_contexts=contexts)
    if admitted:
        assert reviews == []
        edge, = edges
        assert [entries[edge[key]]["quote"] for key in ("source_mention_id", "target_mention_id")] == ["S1", "block P"]
        assert edge["source_mention_id"] in contexts and edge["target_mention_id"] in contexts
    else:
        assert not edges and reviews[0]["code"] == "relation_endpoint_unanchored"


def test_relation_identity_ignores_optional_anchors_across_occurrences_and_batches():
    statement = "Probe P belongs to unit U; probe P is labelled Probe P."
    answer = decision()
    answer["objects"] = [{"object_id": key, "name": name, "kind": "instance", "scope": "",
                          "is_table_subject": True, "mentions": [quote(name, occurrence=0)]}
                         for key, name in (("probe", "Probe P"), ("unit", "unit U"))]
    answer["bindings"][0].update(subject_id=None, status="unknown", evidence=[])
    plain = {"source_id": "probe", "target_id": "unit", "predicate": "part_of", "evidence": [quote(statement)]}
    marked = {**plain, "mention": quote("Probe P", occurrence=0), "target_mention": quote("unit U")}

    def run(rows, prior=None):
        state = inputs(statement)
        request = {**answer, "relations": rows}
        if prior:
            state.update(objects=prior[0], relations=prior[1], entries=prior[3])
            request["objects"] = []
            request["relations"] = [{**row, "source_id": prior[0][0]["object_id"],
                                     "target_id": prior[0][1]["object_id"]} for row in rows]
        reviews = []
        output = apply_decision(parse_unified_step(json.dumps(request)), **state, review_items=reviews)
        return output, reviews

    plain_output, _ = run([plain])
    in_batch, reviews = run([plain, marked])
    later, later_reviews = run([marked], prior=plain_output)
    assert not reviews and not later_reviews and len(later[1]) == 1
    assert later[1] == in_batch[1]
    assert later[1][0]["relation_id"] == plain_output[1][0]["relation_id"]
    assert "source_mention_id" in later[1][0] and "source_mention_id" not in plain_output[1][0]
    conflict, issues = run([{**marked, "mention": quote("Probe P", occurrence=1)}], prior=later)
    assert conflict[1] == later[1] and issues[0]["code"] == "relation_anchor_conflict"


@pytest.mark.parametrize("which_parent,expected", [("introduced", True), ("elsewhere", False)])
def test_model_supplied_antecedent_citation_can_be_outside_the_relationship_clause(which_parent, expected):
    intro = "Acquisition ledgers identify folio F."
    clause = "Its adhesive strip was deposited as AS8."
    source = intro + " " + clause + "\n\nFolio G is displayed at the other museum."
    parent = quote(intro) if which_parent == "introduced" else quote("Folio G is displayed at the other museum.", "p2")
    answer = decision()
    answer["objects"] = [
        {"object_id": "strip", "name": "Registered adhesive strip AS8", "kind": "sample", "scope": "binder",
         "is_table_subject": False, "mentions": [quote(clause)]},
        {"object_id": "folio", "name": "folio F" if expected else "Folio G", "kind": "instance",
         "scope": "document", "is_table_subject": True, "mentions": [parent]}]
    answer["bindings"][0].update(subject_id="strip", evidence=[quote(clause)])
    answer["relations"] = [{"source_id": "strip", "target_id": "folio", "predicate": "sample_of",
                            "evidence": [quote(clause)], "mention": quote(clause), "target_mention": parent}]
    issues = []
    _, edges, _, entries = apply_decision(parse_unified_step(json.dumps(answer)), **inputs(source), review_items=issues)
    assert bool(edges) == expected
    if expected:
        assert entries[edges[0]["target_mention_id"]]["quote"] == intro
        assert edges[0]["target_mention_id"] in edges[0]["evidence_ids"]
    else:
        assert issues[0]["code"] == "relation_endpoint_unanchored"


@pytest.mark.parametrize("protocol,predicate,same_name,admitted", [
    ("source-v1", "part_of", False, True), ("source-v1", "member_of", False, True),
    ("source-v2", "part_of", False, True), ("source-v2", "part_of", True, False),
    (None, "part_of", False, False), ("source-v1", "part_of", True, False),
    ("source-v1", "same_as", False, False)])
def test_overlapping_model_named_endpoints_need_no_duplicate_occurrence(protocol, predicate, same_name, admitted):
    source = "The Amber shale has porosity 13%."
    child, parent = "Amber shale", "Amber shale" if same_name else "Amber"
    answer = decision()
    answer["objects"] = [
        {"object_id": key, "name": label, "kind": kind, "scope": kind,
         "is_table_subject": key == "p", "mentions": [quote(source)]}
        for key, label, kind in (("c", child, "sample"), ("p", parent, "instance"))]
    answer["bindings"][0].update(subject_id="c", evidence=[quote(source)])
    answer["relations"] = [{"source_id": "c", "target_id": "p", "predicate": predicate, "evidence": [quote(source)]}]
    issues = []
    objects, edges, _, entries = apply_decision(parse_unified_step(json.dumps(answer)),
        **inputs(source), statement_protocol=protocol, review_items=issues)
    assert bool(edges) == admitted
    if admitted:
        edge, = edges
        assert (edge["source_id"], edge["target_id"]) == tuple(obj["object_id"] for obj in objects)
        for field, label in (("source_mention_id", child), ("target_mention_id", parent)):
            span = entries[edge[field]]
            assert source[span["start"]:span["end"]] == span["quote"] == label
    else:
        assert issues[0]["code"] == "relation_endpoint_unanchored"


@pytest.mark.parametrize("protocol", ["source-v1", "source-v2"])
def test_source_protocol_accepts_two_explicit_endpoint_markers_in_different_shown_paragraphs(protocol):
    intro = "Field log: Station Hythe was established in 1972."
    child = "The disc was inventoried as D9."
    link = "It was collected from that station during the return visit."
    source = "\n\n".join((intro, child, link))
    answer = decision()
    answer["objects"] = [
        {"object_id": "d", "name": "Disc D9", "kind": "sample", "scope": "visit",
         "is_table_subject": False, "mentions": [quote(child, "p2")]},
        {"object_id": "s", "name": "Station Hythe", "kind": "instance", "scope": "1972",
         "is_table_subject": True, "mentions": [quote(intro, "p1")]}]
    answer["bindings"][0].update(subject_id="d", evidence=[quote(child, "p2")])
    answer["relations"] = [{"source_id": "d", "target_id": "s", "predicate": "sample_of",
                            "evidence": [quote(link, "p3")], "mention": quote(child, "p2"),
                            "target_mention": quote(intro, "p1")}]
    issues = []
    _, edges, _, entries = apply_decision(parse_unified_step(json.dumps(answer)), **inputs(source),
                                          statement_protocol=protocol, review_items=issues)
    assert not issues and len(edges) == 1
    assert {entries[key]["quote"] for key in edges[0]["evidence_ids"]} == {intro, child, link}


def local_name(target="sample-a"):
    return {"source_kind": "mention", "source_id": "obs1", "predicate": "local_identity", "target_id": target,
            "mention": quote("Sample A"), "target_mention": quote("sample A."),
            "evidence": [quote("East reactor R contains sample A."), quote("Sample A has a mass of 17 g.")],
            "reason": "The mass statement names the sample introduced immediately beforehand."}


def with_reactor():
    answer = decision()
    answer["objects"].append({"object_id": "reactor", "name": "East reactor R", "kind": "instance", "scope": "East",
                              "is_table_subject": False, "mentions": [quote("East reactor R")]})
    return answer


def verify(answer):
    reviews = []
    result = apply_decision(parse_unified_step(json.dumps(answer)), **inputs(),
                            review_items=reviews)
    return *result, reviews


def test_new_contract_exposes_no_second_relation_field_and_rejects_a_legacy_binding():
    schema = UnifiedStep.model_json_schema()
    assert "relations" in schema["properties"] and "name_resolution" not in json.dumps(schema)
    answer = decision()
    answer["bindings"][0]["name_resolution"] = None
    with pytest.raises(VerificationValidationError):
        parse_unified_step(json.dumps(answer))


def test_single_list_can_apply_a_scoped_mention_and_oriented_edge_together():
    answer = with_reactor()
    answer["relations"] = [local_name(),
        {"source_kind": "object", "source_id": "sample-a", "target_id": "reactor",
         "predicate": "located_in", "evidence": [quote()]}]
    objects, edges, updates, evidence, reviews = verify(answer)
    assert reviews == []
    assert len(edges) == 1
    assert (edges[0]["source_id"], edges[0]["predicate"], edges[0]["target_id"]) == (
        objects[0]["object_id"], "located_in", objects[1]["object_id"])
    receipt = updates["obs1"]["name_resolution"]
    assert receipt["relation"] == "local_identity" and receipt["target_id"] == objects[0]["object_id"]
    assert evidence[receipt["mention_id"]]["quote"] == "Sample A"


@pytest.mark.parametrize("bad", ["related", "uncertain", "bad quote", "new predicate", "scalar"])
def test_isolating_one_graph_claim_does_not_publish_it_or_withhold_the_independent_fact(bad):
    answer = with_reactor()
    declaration = {"source_kind": "object", "source_id": "reactor", "target_id": "sample-a",
                   "predicate": bad, "evidence": [quote()]}
    if bad == "bad quote":
        declaration.update(predicate="part_of", evidence=[quote("An invented ownership statement.")])
    if bad == "scalar":
        declaration = "not a relation"
    answer["relations"] = [declaration, local_name()]
    objects, edges, updates, _, reviews = verify(answer)
    assert len(objects) == 2 and edges == []
    assert updates["obs1"]["subject_id"] == objects[0]["object_id"]
    assert updates["obs1"]["name_resolution"]["relation"] == "local_identity"
    review, = reviews
    assert (review["proposal_type"], review["path"], review["observation_ids"]) == ("relation", "relations[0]", [])
    assert review["raw_proposal"] == declaration


def test_two_competing_claims_for_the_same_mention_never_choose_a_winner_by_order():
    answer = with_reactor()
    answer["relations"] = [local_name(), local_name("reactor")]
    for ordered in (answer["relations"], list(reversed(answer["relations"]))):
        objects, edges, updates, _, reviews = verify({**answer, "relations": ordered})
        assert edges == [] and updates["obs1"]["subject_id"] == objects[0]["object_id"]
        assert "name_resolution" not in updates["obs1"]
        assert [review["raw_proposal"] for review in reviews] == ordered
        assert all(review["observation_ids"] == ["obs1"] for review in reviews)


def test_locatable_operating_setting_does_not_establish_a_part_of_relation():
    setting = "The design assumed 3000 revolutions per minute."
    source = "Caster A contains probe B. Probe B measured 80 volts. " + setting
    answer = decision()
    part = {"object_id": "probe", "name": "probe B", "kind": "instance",
            "scope": "this machine", "is_table_subject": False, "mentions": [quote("probe B")]}
    caster = {**part, "object_id": "caster", "name": "Caster A",
              "is_table_subject": True, "mentions": [quote("Caster A")]}
    answer["objects"] = [part, caster]
    answer["bindings"][0].update(subject_id="probe", property_name="voltage",
                                 evidence=[quote("Probe B measured 80 volts.")])
    relation = {"source_kind": "object", "source_id": "probe", "predicate": "part_of", "target_id": "caster"}
    for statement, supported in [(setting, False), ("Caster A contains probe B.", True)]:
        answer["relations"] = [{**relation, "evidence": [quote(statement)]}]
        state = inputs(source)
        state["batch"][0].update(value="80", unit="V")
        reviews = []
        objects, edges, updates, _ = apply_decision(parse_unified_step(json.dumps(answer)),
                                                   **state, review_items=reviews)
        assert updates["obs1"]["subject_id"] == objects[0]["object_id"]
        if supported:
            assert not reviews
            assert [(edge["source_id"], edge["predicate"], edge["target_id"]) for edge in edges] == [
                (objects[0]["object_id"], "part_of", objects[1]["object_id"])]
        else:
            assert not edges
            assert [review["raw_proposal"] for review in reviews] == answer["relations"]

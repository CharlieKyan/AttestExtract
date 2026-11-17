"""Proposal-level acceptance with independent synthetic source expectations."""

from copy import deepcopy
import hashlib
import json

import pytest

from attestextract.verification_protocol import apply_decision, parse_step, VerificationValidationError
from attestextract.verification_sources import SourceIndex


SOURCE = ("Specimen A was prepared in batch East. A weighs 10 g.\n\n"
          "Specimen B was prepared in batch West. B weighs 20 g.\n\n"
          "Specimen C was prepared in batch North. C weighs 30 g.\n\n"
          "A and B were compared. Another measurement has no identified owner.")


def quoted(text, passage=1):
    return {"passage_id": f"passage-{passage:06d}", "quote": text, "occurrence": None}


def sample(count=6):
    index = SourceIndex(SOURCE)
    state = {"source_text": SOURCE, "source_hash": hashlib.sha256(SOURCE.encode()).hexdigest(),
             "batch": [{"observation_id": f"obs-{n}", "value": str(n), "unit": "g", "blockers": []}
                       for n in range(count)], "objects": [], "relations": [], "entries": {},
             "visible_passages": index.passages, "source_index": index}
    payload = {"action": "decide", "question": "Which specimen owns each measurement?",
               "objects": [{"object_id": "a", "name": "Specimen A", "kind": "sample", "scope": "East",
                            "is_table_subject": True, "mentions": [quoted("Specimen A was prepared in batch East.")]}],
               "relations": [], "bindings": [
                   {"observation_id": f"obs-{n}", "subject_id": "a", "status": "supported",
                    "evidence": [quoted("A weighs 10 g.")], "reason": "The source names the direct owner.",
                    "target_match": "supported", "property_name": "mass", "statistic": None}
                   for n in range(count)]}
    return payload, state


def assess(payload, state):
    reviews, contexts = [], {}
    result = apply_decision(parse_step(json.dumps(payload)), **state,
                            review_items=reviews, evidence_contexts=contexts)
    return result, reviews, contexts


def add_object(payload, key, name, scope, passage):
    payload["objects"].append({"object_id": key, "name": name, "kind": "sample", "scope": scope,
        "is_table_subject": True, "mentions": [quoted(f"{name} was prepared in batch {scope}.", passage)]})


def test_three_supported_one_unknown_two_contradictions_are_not_all_discarded():
    payload, state = sample()
    payload["bindings"][3].update(subject_id=None, status="unknown", evidence=[],
                                  reason="The owner is unresolved.", target_match="unsupported")
    for index in (4, 5):
        payload["bindings"][index]["subject_id"] = None
    before = deepcopy(payload)
    (objects, relations, updates, entries), reviews, contexts = assess(payload, state)
    assert [updates[f"obs-{n}"]["binding_status"] for n in range(6)] == [
        "supported", "supported", "supported", "unknown", "review_required", "review_required"]
    assert all(updates[f"obs-{n}"]["subject_id"] == objects[0]["object_id"] for n in range(3))
    assert updates["obs-3"]["target_match"] == "unsupported"
    assert [r["observation_ids"] for r in reviews] == [["obs-4"], ["obs-5"]]
    assert [r["code"] for r in reviews] == ["invalid_binding_status"] * 2
    assert all(r["raw_proposal"]["status"] == "supported" for r in reviews)
    assert all(updates[f"obs-{n}"]["binding_evidence_ids"] == [] for n in (4, 5))
    assert set(contexts) <= set(entries) and not relations
    assert payload == before
    assert state["objects"] == [] and state["entries"] == {}


@pytest.mark.parametrize("damage", ["absent", "unseen", "bad_occurrence", "unknown_passage", "blank_name"])
def test_rejected_object_holds_its_bindings_and_relation_but_not_independent_owner(damage):
    payload, state = sample(4)
    add_object(payload, "b", "Specimen B", "West", 2)
    bad = payload["objects"][1]
    if damage == "absent":
        bad["mentions"][0]["quote"] = "Invented source text."
    elif damage == "unseen":
        state["visible_passages"] = [p for p in state["visible_passages"] if p["passage_id"] != "passage-000002"]
    elif damage == "bad_occurrence":
        bad["mentions"][0]["occurrence"] = 10
    elif damage == "unknown_passage":
        bad["mentions"][0]["passage_id"] = "missing"
    else:
        bad["name"] = " "
    for number in (1, 2):
        payload["bindings"][number].update(subject_id="b", evidence=[quoted("B weighs 20 g.", 2)])
    payload["relations"] = [{"source_id": "b", "target_id": "a", "predicate": "other",
                             "evidence": [quoted("A and B were compared.", 4)]}]
    (objects, relations, updates, entries), reviews, contexts = assess(payload, state)
    assert [obj["name"] for obj in objects] == ["Specimen A"] and not relations
    assert [updates[f"obs-{n}"]["binding_status"] for n in range(4)] == [
        "supported", "review_required", "review_required", "supported"]
    assert reviews[0]["proposal_type"] == "object" and reviews[0]["observation_ids"] == ["obs-1", "obs-2"]
    assert reviews[1]["proposal_type"] == "relation" and reviews[1]["dependencies"] == ["b"]
    assert reviews[1]["observation_ids"] == []
    assert all(r["dependencies"] == ["b"] for r in reviews[2:])
    assert {item["quote"] for item in entries.values()} == {
        "Specimen A was prepared in batch East.", "A weighs 10 g."}
    assert set(contexts) == set(entries)


def test_bad_binding_collects_status_reference_and_quote_issues_without_losing_other_binding():
    payload, state = sample(2)
    payload["bindings"][1].update(status="unknown", subject_id="invented",
                                  evidence=[quoted("Not in source.")])
    (_, _, updates, _), reviews, _ = assess(payload, state)
    assert updates["obs-0"]["binding_status"] == "supported"
    assert updates["obs-1"]["binding_status"] == "review_required"
    assert [issue["code"] for issue in reviews[0]["issues"]] == [
        "invalid_binding_status", "invalid_object_reference", "quote_not_found"]
    assert all(set(("code", "path", "phase", "message")) <= issue.keys() for issue in reviews[0]["issues"])


def test_relation_rejection_cannot_invalidate_independent_binding_or_leak_evidence():
    payload, state = sample(1)
    payload["relations"] = [{"source_id": "a", "target_id": "a", "predicate": "same_as",
                             "evidence": [quoted("A and B were compared.", 4)]}]
    (_, relations, updates, entries), reviews, contexts = assess(payload, state)
    assert not relations and updates["obs-0"]["binding_status"] == "supported"
    assert reviews[0]["proposal_type"] == "relation" and reviews[0]["observation_ids"] == []
    assert "A and B were compared." not in {item["quote"] for item in entries.values()}
    assert set(contexts) <= set(entries)


def test_unknown_ownership_with_supported_field_is_valid_and_needs_no_object():
    payload, state = sample(1)
    payload["objects"] = []
    payload["bindings"][0].update(status="unknown", subject_id=None, evidence=[])
    (objects, _, updates, _), reviews, _ = assess(payload, state)
    assert not objects and not reviews
    assert updates["obs-0"]["binding_status"] == "unknown" and updates["obs-0"]["target_match"] == "supported"


def test_ambiguous_binding_retains_all_locations_but_does_not_hold_independent_binding():
    payload, state = sample(2)
    payload["bindings"][1]["evidence"] = [quoted("A")]
    (_, _, updates, entries), reviews, contexts = assess(payload, state)
    assert updates["obs-0"]["binding_status"] == "supported"
    assert updates["obs-1"]["binding_status"] == "review_required"
    issue = reviews[0]["issues"][0]
    assert issue["code"] == "quote_ambiguous" and issue["path"] == "bindings[1].evidence[0]"
    assert issue["total_match_count"] == issue["visible_match_count"] == 2
    # The first sentence including its trailing space occupies 39 characters.
    assert [match["start"] for match in issue["matches"]] == [9, 39]
    assert all(match["context"]["model_visible"] for match in issue["matches"])
    assert "A" not in {item["quote"] for item in entries.values()}
    assert set(contexts) == set(entries)


def test_rejected_binding_does_not_publish_its_valid_quote_or_context():
    payload, state = sample(1)
    payload["bindings"][0].update(subject_id=None, evidence=[quoted("A and B were compared.", 4)])
    (_, _, _, entries), reviews, contexts = assess(payload, state)
    assert reviews and {item["quote"] for item in entries.values()} == {"Specimen A was prepared in batch East."}
    assert set(contexts) == set(entries)


@pytest.mark.parametrize("damage", ["visible", "scope", "duplicate_object", "duplicate_identity"])
def test_global_errors_publish_nothing_even_after_a_rejectable_proposal(damage):
    payload, state = sample(2)
    payload["bindings"][1]["subject_id"] = None
    if damage == "visible":
        state["visible_passages"][-1] = {**state["visible_passages"][-1], "text": "Corrupt unseen-by-quotes fragment"}
    elif damage == "scope":
        payload["bindings"][0]["observation_id"] = "foreign"
    else:
        payload["objects"].append(deepcopy(payload["objects"][0]))
        if damage == "duplicate_identity":
            payload["objects"][1]["object_id"] = "another-local-id"
    reviews, contexts = [], {}
    with pytest.raises(VerificationValidationError):
        apply_decision(parse_step(json.dumps(payload)), **state, review_items=reviews, evidence_contexts=contexts)
    assert reviews == [] and contexts == {} and state["objects"] == [] and state["entries"] == {}


def test_partial_validation_is_deterministic_and_does_not_modify_original_payload():
    payload, state = sample(3)
    payload["bindings"][1].update(subject_id=None, evidence=[])
    before = deepcopy(payload)
    first, second = assess(payload, state), assess(payload, state)
    assert first == second and payload == before


def test_model_schema_explains_local_ids_and_independent_ownership_contract():
    payload, _ = sample(1)
    schema = parse_step(json.dumps(payload)).model_json_schema()["$defs"]
    assert "framework creates the stable ID" in schema["ProposedObject"]["properties"]["object_id"]["description"]
    assert "supported" in schema["Binding"]["properties"]["subject_id"]["description"]
    assert "Independent" in schema["Binding"]["properties"]["target_match"]["description"]

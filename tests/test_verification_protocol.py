"""Independent source/identity contracts for the model-guided verification loop."""

from copy import deepcopy
import hashlib
import json

import pytest

from niceextract.verification_protocol import (Step, VerificationValidationError,
                                                apply_decision, parse_step)


TEXT = ("East reactor R contains sample A. Sample A has a mass of 17 g.\n\n"
        "West reactor R contains sample B. Sample B has a mass of 29 g.")


class Index:
    def __init__(self, text=TEXT):
        self.passages = {}
        start = 0
        for number, part in enumerate(text.split("\n\n"), 1):
            key = f"p{number}"
            self.passages[key] = {"passage_id": key, "start": start,
                                  "end": start + len(part), "text": part}
            start += len(part) + 2

    def get(self, key):
        if key not in self.passages:
            raise ValueError("Unknown passage")
        return self.passages[key]


def quote(text="East reactor R contains sample A.", passage="p1", occurrence=None):
    return {"passage_id": passage, "quote": text, "occurrence": occurrence}


def decision():
    return {
        "action": "decide", "question": "Which specimen owns the reported mass?",
        "query": None, "search_offset": 0, "passage_ids": [], "neighbors": 0,
        "objects": [{"object_id": "sample-a", "name": "Sample A in East reactor R",
                     "kind": "sample", "scope": "East reactor R", "is_table_subject": True,
                     "mentions": [quote()]}],
        "relations": [],
        "bindings": [{"observation_id": "obs1", "subject_id": "sample-a", "status": "supported",
                      "evidence": [quote("Sample A has a mass of 17 g.")],
                      "reason": "The source identifies sample A as the measured specimen.",
                      "target_match": "supported", "property_name": "mass", "statistic": None}],
    }


def inputs(text=TEXT):
    index = Index(text)
    return {"source_text": text, "source_hash": hashlib.sha256(text.encode()).hexdigest(),
            "batch": [{"observation_id": "obs1", "value": "17", "unit": "g",
                       "conditions": None, "blockers": [{"code": "original_warning"}]}],
            "objects": [], "relations": [], "entries": {},
            "visible_passages": list(index.passages.values()), "source_index": index}


def test_composed_display_label_has_exact_source_mentions_and_immutable_observation():
    state = inputs()
    before = deepcopy({k: v for k, v in state.items() if k != "source_index"})
    objects, relations, updates, evidence = apply_decision(parse_step(json.dumps(decision())), **state)
    assert objects[0]["name"] not in TEXT
    assert objects[0]["name"] == "Sample A in East reactor R"
    assert updates["obs1"]["subject_id"] == objects[0]["object_id"]
    assert updates["obs1"]["blockers"] == [{"code": "original_warning"}]
    assert updates["obs1"]["target_match"] == "supported"
    assert all(TEXT[e["start"]:e["end"]] == e["quote"] for e in evidence.values())
    assert not relations
    assert {k: v for k, v in state.items() if k != "source_index"} == before
    assert "value" not in updates["obs1"] and "unit" not in updates["obs1"]


@pytest.mark.parametrize("mutation", ["hallucinated", "unseen", "foreign_passage", "duplicate_quote"])
def test_evidence_requires_actual_seen_source(mutation):
    payload, state = decision(), inputs()
    if mutation == "hallucinated":
        payload["objects"][0]["mentions"] = [quote("Not reported in the document.")]
    elif mutation == "unseen":
        state["visible_passages"] = [state["source_index"].get("p2")]
    elif mutation == "foreign_passage":
        payload["objects"][0]["mentions"][0]["passage_id"] = "p900"
    else:
        state = inputs("A has mass 1 g. A has mass 1 g.")
        payload["objects"][0]["mentions"] = [quote("A has mass 1 g.")]
    with pytest.raises(ValueError):
        apply_decision(Step.model_validate(payload), **state)


def test_repeated_quote_occurrence_is_relative_to_the_indexed_passage():
    payload = decision()
    text = "A has mass 1 g. A has mass 1 g."
    payload["objects"][0]["mentions"] = [quote("A has mass 1 g.", occurrence=1)]
    payload["bindings"][0]["evidence"] = [quote("A has mass 1 g.", occurrence=1)]
    _, _, _, evidence = apply_decision(Step.model_validate(payload), **inputs(text))
    assert len(evidence) == 1
    assert next(iter(evidence.values()))["start"] == text.rindex("A has mass 1 g.")


def test_fragments_cannot_be_joined_to_invent_a_seen_quote():
    state = inputs()
    passage = state["source_index"].get("p1")
    middle = 12
    state["visible_passages"] = [
        {**passage, "end": middle, "text": TEXT[:middle]},
        {**passage, "start": middle, "text": TEXT[middle:passage["end"]]},
    ]
    with pytest.raises(ValueError, match="supplied"):
        apply_decision(Step.model_validate(decision()), **state)


def ambiguous_decision():
    text = "Sample A has mass 17 g. Sample A was sampled in the east."
    payload = decision()
    payload["objects"][0]["mentions"] = [quote("Sample A")]
    payload["bindings"][0]["evidence"] = [quote(text)]
    return payload, inputs(text)


def test_ambiguous_quote_preserves_alternatives_and_commits_nothing():
    payload, state = ambiguous_decision()
    original = deepcopy({key: value for key, value in state.items() if key != "source_index"})
    contexts = {}
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(Step.model_validate(payload), **state, evidence_contexts=contexts)
    error = caught.value
    assert error.code == "quote_ambiguous" and error.summary["ambiguity_count"] == 1
    ambiguity = error.details["ambiguities"][0]
    assert ambiguity["path"] == "objects[0].mentions[0]"
    assert ambiguity["total_match_count"] == ambiguity["visible_match_count"] == 2
    assert [(m["start"], m["occurrence"]) for m in ambiguity["matches"]] == [(0, 0), (24, 1)]
    assert all(m["context"]["model_visible"] for m in ambiguity["matches"])
    assert contexts == {} and {key: value for key, value in state.items() if key != "source_index"} == original


@pytest.mark.parametrize("mutation,code", [("missing_quote", "quote_not_found"),
    ("bad_subject", "invalid_decision"), ("bad_status", "invalid_binding_status"),
    ("out_of_range", "occurrence_out_of_range"), ("foreign_passage", "unknown_passage")])
def test_later_hard_error_is_not_hidden_by_earlier_ambiguity(mutation, code):
    payload, state = ambiguous_decision()
    binding = payload["bindings"][0]
    if mutation == "missing_quote":
        binding["evidence"][0]["quote"] = "The source does not contain this."
    elif mutation == "bad_subject":
        binding["subject_id"] = "not-declared"
    elif mutation == "bad_status":
        binding["status"] = "unknown"
    elif mutation == "out_of_range":
        binding["evidence"][0]["occurrence"] = 2
    else:
        binding["evidence"][0]["passage_id"] = "missing"
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(Step.model_validate(payload), **state)
    assert caught.value.code == code and state["objects"] == state["relations"] == []


def test_only_one_visible_occurrence_resolves_without_selecting_unseen_text():
    payload, state = ambiguous_decision()
    passage = state["source_index"].get("p1")
    state["visible_passages"] = [{**passage, "start": 24, "text": passage["text"][24:]}]
    payload["bindings"][0]["evidence"] = [quote("Sample A was sampled in the east.")]
    contexts = {}
    _, _, _, entries = apply_decision(Step.model_validate(payload), **state, evidence_contexts=contexts)
    mention = next(item for item in entries.values() if item["quote"] == "Sample A")
    assert mention["start"] == 24
    assert contexts[mention["evidence_id"]]["model_visible"] is False
    payload["objects"][0]["mentions"][0]["occurrence"] = 0
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(Step.model_validate(payload), **state)
    assert caught.value.code == "not_visible"


def test_display_context_does_not_change_proof_or_object_identity():
    payload = Step.model_validate(decision())
    state = inputs()
    baseline = apply_decision(payload, **state)
    contexts = {}
    enriched = apply_decision(payload, **state, evidence_contexts=contexts)
    assert enriched == baseline and contexts
    for key, context in contexts.items():
        evidence = enriched[-1][key]
        assert context["text"] == TEXT[context["start"]:context["end"]]
        assert context["start"] <= evidence["start"] < evidence["end"] <= context["end"]


def test_invalid_visible_source_ranges_are_rejected():
    state = inputs()
    state["visible_passages"][0] = {**state["visible_passages"][0], "text": "Changed source"}
    with pytest.raises(ValueError, match="Visible passage"):
        apply_decision(Step.model_validate(decision()), **state)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "raw_value", "undeclared_subject", "unknown_with_subject"])
def test_invalid_binding_contract_never_applies(mutation):
    payload, state = decision(), inputs()
    if mutation == "missing":
        payload["bindings"] = []
    elif mutation == "duplicate":
        payload["bindings"] *= 2
    elif mutation == "foreign":
        payload["bindings"][0]["observation_id"] = "obs900"
    elif mutation == "raw_value":
        payload["bindings"][0]["value"] = "999"
    elif mutation == "undeclared_subject":
        payload["bindings"][0]["subject_id"] = "invented"
    else:
        payload["bindings"][0]["status"] = "unknown"
    with pytest.raises(ValueError):
        apply_decision(Step.model_validate(payload), **state)
    assert state["objects"] == [] and state["entries"] == {} and state["relations"] == []
    assert state["batch"][0]["value"] == "17"


@pytest.mark.parametrize("match", ["unsupported", "unknown"])
def test_target_mismatch_keeps_original_fact_and_adds_blocker(match):
    payload, state = decision(), inputs()
    payload["bindings"][0].update(target_match=match, property_name="reported total mass")
    _, _, updates, _ = apply_decision(Step.model_validate(payload), **state)
    update = updates["obs1"]
    assert update["binding_status"] == "supported" and update["subject_id"]
    assert [x["code"] for x in update["blockers"]] == ["original_warning", "target_field_" + match]
    assert state["batch"][0]["blockers"] == [{"code": "original_warning"}]


def test_unknown_attribution_can_finish_without_fabricated_objects():
    payload = decision()
    payload["objects"] = []
    payload["bindings"][0].update(subject_id=None, status="unknown", evidence=[],
                                 reason="The checked passages do not determine the owner.")
    objects, _, updates, _ = apply_decision(Step.model_validate(payload), **inputs())
    assert objects == []
    assert updates["obs1"]["subject_id"] is None
    assert updates["obs1"]["binding_status"] == "unknown"


def test_existing_stable_object_can_be_referenced_but_not_replaced():
    state = inputs()
    objects, relations, _, evidence = apply_decision(Step.model_validate(decision()), **state)
    state.update(objects=objects, relations=relations, entries=evidence)
    payload = decision()
    payload["objects"] = []
    payload["bindings"][0]["subject_id"] = objects[0]["object_id"]
    assert apply_decision(Step.model_validate(payload), **state)[2]["obs1"]["subject_id"] == objects[0]["object_id"]
    payload["objects"] = decision()["objects"]
    payload["objects"][0]["object_id"] = objects[0]["object_id"]
    with pytest.raises(ValueError, match="replace"):
        apply_decision(Step.model_validate(payload), **state)


def test_exact_object_redeclaration_reuses_identity_without_name_based_merging():
    state = inputs()
    objects, relations, _, entries = apply_decision(Step.model_validate(decision()), **state)
    state.update(objects=objects, relations=relations, entries=entries)
    payload = decision()
    payload["objects"][0]["object_id"] = "another-local-id"
    payload["bindings"][0]["subject_id"] = "another-local-id"
    repeated, _, updates, _ = apply_decision(Step.model_validate(payload), **state)
    assert repeated == objects
    assert updates["obs1"]["subject_id"] == objects[0]["object_id"]
    payload["objects"][0]["is_table_subject"] = False
    with pytest.raises(ValueError, match="disagrees"):
        apply_decision(Step.model_validate(payload), **state)
    payload["objects"][0]["is_table_subject"] = True
    payload["objects"][0]["scope"] = "Another experimental run"
    distinct, _, updates, _ = apply_decision(Step.model_validate(payload), **state)
    assert len(distinct) == 2 and distinct[1]["name"] == distinct[0]["name"]
    assert updates["obs1"]["subject_id"] != objects[0]["object_id"]


@pytest.mark.parametrize("duplicate", ["local", "stable"])
def test_duplicate_object_identifiers_rejected(duplicate):
    payload = decision()
    other = deepcopy(payload["objects"][0])
    if duplicate == "stable":
        other["object_id"] = "second-local-name"
    payload["objects"].append(other)
    with pytest.raises(ValueError):
        apply_decision(Step.model_validate(payload), **inputs())


def test_relations_need_known_distinct_endpoints_and_cannot_repeat():
    payload = decision()
    relation = {"source_id": "sample-a", "target_id": "missing", "predicate": "sample_of", "evidence": [quote()]}
    payload["relations"] = [relation]
    with pytest.raises(ValueError, match="undeclared"):
        apply_decision(Step.model_validate(payload), **inputs())
    relation["target_id"] = "sample-a"
    with pytest.raises(ValueError, match="distinct"):
        apply_decision(Step.model_validate(payload), **inputs())
    parent = deepcopy(payload["objects"][0])
    parent.update(object_id="reactor", name="East reactor R", kind="instance", is_table_subject=False)
    payload["objects"].append(parent)
    relation["target_id"] = "reactor"
    objects, relations, _, _ = apply_decision(Step.model_validate(payload), **inputs())
    assert relations[0]["source_id"] == objects[0]["object_id"]
    assert relations[0]["target_id"] == objects[1]["object_id"]
    payload["relations"].append(deepcopy(relation))
    with pytest.raises(ValueError, match="repeated"):
        apply_decision(Step.model_validate(payload), **inputs())


def test_identical_relation_can_be_redeclared_across_decisions():
    state, payload = inputs(), decision()
    parent = deepcopy(payload["objects"][0])
    parent.update(object_id="reactor", name="East reactor R", kind="instance", is_table_subject=False)
    payload["objects"].append(parent)
    payload["relations"] = [{"source_id": "sample-a", "target_id": "reactor", "predicate": "sample_of", "evidence": [quote()]}]
    objects, relations, _, entries = apply_decision(Step.model_validate(payload), **state)
    state.update(objects=objects, relations=relations, entries=entries)
    repeated_objects, repeated_relations, _, _ = apply_decision(Step.model_validate(payload), **state)
    assert repeated_objects == objects and repeated_relations == relations


@pytest.mark.parametrize("action", ["search", "read"])
def test_tool_step_cannot_modify_semantic_registry(action):
    payload = decision()
    payload.update(action=action, query="sample" if action == "search" else None,
                   passage_ids=["p1"] if action == "read" else [])
    with pytest.raises(ValueError, match="cannot submit"):
        parse_step(json.dumps(payload))


@pytest.mark.parametrize("payload", [
    {"action": "search", "question": "Which sample?", "query": " "},
    {"action": "search", "question": "Which sample?", "query": "sample", "passage_ids": ["p1"]},
    {"action": "read", "question": "Which sample?", "passage_ids": []},
    {"action": "read", "question": "Which sample?", "passage_ids": ["p1", "p1"]},
    {"action": "read", "question": "Which sample?", "passage_ids": ["p1"], "query": "sample"},
    {"action": "decide", "question": "Which sample?", "query": "sample"},
])
def test_actions_have_unambiguous_arguments(payload):
    with pytest.raises(ValueError):
        parse_step(json.dumps(payload))


def test_free_form_question_and_query_do_not_require_a_predefined_gap_category():
    step = parse_step(json.dumps({"action": "search", "question": "Does the specimen identifier refer to a rerun?",
                                 "query": "rerun", "search_offset": 6}))
    assert step.question == "Does the specimen identifier refer to a rerun?"
    assert step.search_offset == 6
    assert step.objects == []


def test_duplicate_json_keys_are_not_silently_selected():
    with pytest.raises(ValueError):
        parse_step('{"action":"read","action":"decide","question":"why"}')


def test_six_targets_and_twelve_readonly_references_are_rejected_transactionally():
    state, payload = inputs(), decision()
    targets = [f"target-{n}" for n in range(6)]
    references = [f"reference-{n}" for n in range(12)]
    state["batch"] = [{**deepcopy(state["batch"][0]), "observation_id": key} for key in targets]
    payload["bindings"] = [{**deepcopy(payload["bindings"][0]), "observation_id": key}
                           for key in [*targets, *references]]
    before = deepcopy({k: v for k, v in state.items() if k != "source_index"})
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(parse_step(json.dumps(payload)), **state, reference_ids=references)
    failure = caught.value
    assert failure.summary == {
        "category": "model_response", "phase": "binding_scope", "code": "binding_scope_mismatch",
        "expected_count": 6, "returned_count": 18, "missing_count": 0, "duplicate_count": 0,
        "unexpected_count": 12, "reference_count": 12,
    }
    assert set(failure.details["reference_ids"]) == set(references)
    assert failure.details["foreign_ids"] == failure.details["missing_ids"] == failure.details["duplicate_ids"] == []
    assert all(key not in str(failure) for key in [*targets, *references])
    assert {k: v for k, v in state.items() if k != "source_index"} == before


def test_scope_diagnostic_separates_missing_duplicates_references_and_foreign_ids():
    state, payload = inputs(), decision()
    state["batch"].append({**deepcopy(state["batch"][0]), "observation_id": "obs2"})
    payload["bindings"] = [{**deepcopy(payload["bindings"][0]), "observation_id": key}
                           for key in ["obs1", "obs1", "reference-only", "invented"]]
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(parse_step(json.dumps(payload)), **state, reference_ids=["reference-only"])
    details = caught.value.details
    assert details["expected_count"] == 2 and details["returned_count"] == 4
    assert details["missing_ids"] == ["obs2"]
    assert details["duplicate_ids"] == ["obs1"]
    assert details["unexpected_ids"] == ["invented", "reference-only"]
    assert details["reference_ids"] == ["reference-only"] and details["foreign_ids"] == ["invented"]


@pytest.mark.parametrize("damage", ["duplicate_batch", "duplicate_objects", "index", "visible"])
def test_framework_invariant_failures_are_not_model_errors(damage):
    state, payload = inputs(), decision()
    if damage == "duplicate_batch":
        state["batch"] *= 2
    elif damage == "duplicate_objects":
        state["objects"] = [{"object_id": "existing"}, {"object_id": "existing"}]
    elif damage == "index":
        state["source_index"].passages["p1"]["text"] = "Corrupted indexed data"
    else:
        state["visible_passages"][0] = {**state["visible_passages"][0], "text": "Corrupted visible data"}
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(parse_step(json.dumps(payload)), **state)
    assert caught.value.summary == {"category": "internal_state", "phase": "internal_state",
                                    "code": "verification_state_error"}
    assert state["relations"] == [] and state["entries"] == {}


def test_unknown_binding_error_identifies_observation_without_changing_it():
    state, payload = inputs(), decision()
    payload["bindings"][0]["status"] = "unknown"
    with pytest.raises(VerificationValidationError) as caught:
        apply_decision(parse_step(json.dumps(payload)), **state)
    assert caught.value.code == "invalid_binding_status"
    assert caught.value.details["observation_id"] == "obs1"
    assert "observation_id" not in caught.value.summary
    assert state["objects"] == [] and state["entries"] == {}


def test_unbound_but_source_supported_object_is_not_silently_removed_or_forced_to_bind():
    state, payload = inputs(), decision()
    payload["bindings"][0].update(status="unknown", subject_id=None, evidence=[], target_match="unknown",
                                  reason="The specimen exists, but this attribution is unresolved.")
    objects, _, updates, _ = apply_decision(parse_step(json.dumps(payload)), **state)
    assert len(objects) == 1 and updates["obs1"]["subject_id"] is None
    assert updates["obs1"]["binding_status"] == "unknown"


def test_oversized_json_integer_is_a_response_decoding_error():
    text = '{"action":"search","query":"sample","question":"Which sample?","search_offset":' + "9" * 5000 + '}'
    with pytest.raises(VerificationValidationError) as caught:
        parse_step(text)
    assert caught.value.code == "invalid_analysis_json"
    assert caught.value.summary["category"] == "model_response"

"""A first-pass evidence reference locates original words; its authority is local to the request."""

from copy import deepcopy
import json

import pytest

from niceextract import RunTrace, verification
from niceextract.analysis import _evidence, _input_observations
from niceextract.models import BackendResponse, GenerationOptions
from niceextract.verification_protocol import apply_decision, parse_unified_step, VerificationValidationError
from niceextract.verification_sources import SourceIndex
from test_verification_loop import MEASUREMENT, SHORT, frozen_extraction, run
from test_verification_protocol import decision, inputs, quote


def citation_case():
    sentence = r"Probe A logged a maximum of 30 V in slot 1\~3."
    state = inputs(sentence + " " + sentence)
    left = len(sentence) + 1
    span = {"start": left, "end": left + len(sentence)}
    key = _evidence(state["source_text"], {**span, "quote": sentence}, state["entries"])
    state["batch"][0].update(evidence_ids=[key], source_evidence=[
        {"location_status": "located", "span": span, "quote": sentence}])
    verification._attach_source_citations(state["batch"], state["entries"], state["visible_passages"])
    form = decision()
    form["objects"][0].update(name="Probe A", mentions=[{"evidence_id": key}])
    form["bindings"][0].update(evidence=[{"evidence_id": key}], target_match="unsupported",
                               reason="The field requests a mean, the source gives a maximum.")
    return key, state, form


def test_id_and_exact_quote_select_the_same_occurrence_and_leave_field_fit_independent():
    key, state, form = citation_case()
    stored = state["entries"][key]
    exact = deepcopy(form)
    expanded = quote(stored["quote"], occurrence=1)
    exact["objects"][0]["mentions"] = exact["bindings"][0]["evidence"] = [expanded]
    by_id = apply_decision(parse_unified_step(json.dumps(form)), **state)
    assert by_id == apply_decision(parse_unified_step(json.dumps(exact)), **state)
    objects, _, updates, entries = by_id
    assert objects[0]["mention_evidence_ids"] == updates["obs1"]["binding_evidence_ids"] == [key]
    assert entries[key] == stored and updates["obs1"]["target_match"] == "unsupported"


@pytest.mark.parametrize("mistake", ["invented", "not_displayed", "from_an_earlier_step", "another_observation"])
def test_an_unoffered_or_foreign_id_cannot_bind_an_observation(mistake):
    key, state, form = citation_case()
    # Keep the proposed object independent from the binding under test.
    form["objects"][0]["mentions"] = [quote("Probe A", occurrence=1)]
    if mistake == "invented":
        form["bindings"][0]["evidence"] = [{"evidence_id": "unknown"}]
    elif mistake == "not_displayed":
        state["visible_passages"][0].pop("source_citations")
    elif mistake == "from_an_earlier_step":
        state["citation_passages"] = []
    else:
        first = state["batch"][0]
        state["batch"].append({**first, "observation_id": "obs2"})
        first.update(evidence_ids=[], source_evidence=[])
        state["visible_passages"][0]["source_citations"][0]["observation_ids"] = ["obs2"]
        form["bindings"].append({**form["bindings"][0], "observation_id": "obs2"})
    reviews = []
    _, _, updates, _ = apply_decision(parse_unified_step(json.dumps(form)), **state, review_items=reviews)
    assert updates["obs1"]["binding_status"] == "review_required"
    assert [r["code"] for r in reviews] == ["source_citation_unavailable"]
    if mistake == "another_observation":
        assert updates["obs2"]["binding_evidence_ids"] == [key]


def test_ambiguous_first_pass_positions_or_partial_visible_fragments_never_become_offers():
    key, state, _ = citation_case()
    fragment, = state["visible_passages"]
    fragment.pop("source_citations")
    first = state["batch"][0]["source_evidence"][0]
    alternate = {"start": 0, "end": len(first["quote"])}
    for status in ("ambiguous", "normalized"):
        first.update(location_status=status, source_matches=[{"span": alternate}])
        verification._attach_source_citations(state["batch"], state["entries"], [fragment])
        assert "source_citations" not in fragment
    first.update(location_status="located", source_matches=[])
    fragment["end"] = state["entries"][key]["end"] - 1
    verification._attach_source_citations(state["batch"], state["entries"], [fragment])
    assert "source_citations" not in fragment


def test_a_corrupted_registry_reference_is_a_state_error():
    key, state, form = citation_case()
    state["entries"][key]["quote"] = "Another reading"
    with pytest.raises(VerificationValidationError) as exc:
        apply_decision(parse_unified_step(json.dumps(form)), **state, review_items=[])
    assert exc.value.details["category"] == "internal_state"


@pytest.mark.parametrize("owns_clause,offered,expected", [(True, True, True), (False, True, False),
                                                          (True, False, False)])
def test_bound_parent_must_be_registered_separately_and_own_the_cited_membership(owns_clause, offered, expected):
    intro, clause = "东坡渠是本次观察对象。", "东坡渠沉积物含水率为13%。"
    state = inputs(intro + clause)
    span = {"start": len(intro), "end": len(intro + clause)}
    key = _evidence(state["source_text"], {**span, "quote": clause}, state["entries"])
    state["batch"][0].update(entity="东坡渠沉积物", evidence_ids=[key],
                             source_evidence=[{"location_status": "located", "span": span}])
    state["batch"].append({"observation_id": "obs2", "entity": "东坡渠"})
    if offered:
        verification._attach_source_citations(state["batch"], state["entries"], state["visible_passages"])
    form = decision()
    form["objects"] = [
        {"object_id": obj, "kind": "instance", "name": name, "scope": "", "is_table_subject": row,
         "mentions": [quote(mention)]} for obj, name, mention, row in
        [("sediment", "东坡渠沉积物", clause, False), ("canal", "试验区东坡渠", intro, True)]]
    form["bindings"][0].update(subject_id="sediment", evidence=[quote(clause)])
    form["bindings"].append({**form["bindings"][0], "observation_id": "obs2", "subject_id": "canal",
                             "evidence": [quote(clause if owns_clause else intro)]})
    form["relations"] = [{"source_id": "sediment", "target_id": "canal", "predicate": "part_of",
                          "evidence": [{"evidence_id": key}]}]
    reviews = []
    _, edges, _, entries = apply_decision(parse_unified_step(json.dumps(form)), **state, review_items=reviews)
    assert bool(edges) == expected
    if expected:
        assert not reviews
        edge, = edges
        assert [entries[edge[field]]["quote"] for field in ("source_mention_id", "target_mention_id")] == [
            "东坡渠沉积物", "东坡渠"]
    else:
        assert reviews[0]["code"] in {"relation_endpoint_unanchored", "source_citation_unavailable"}


def test_input_budget_drops_optional_citations_before_their_source_passages():
    result = frozen_extraction()
    entries = {}
    batch, _ = _input_observations(SHORT, result, entries)
    def request(budget):
        return verification._request({"job_id": "verify-0001"}, 1, result, "specimens", batch, batch, [],
                                     entries, SourceIndex(SHORT), [], GenerationOptions(), 4, budget)
    _, original, full_size = request(48000)
    assert any(fragment.get("source_citations") for fragment in original)
    constrained, compact, size = request(full_size - 1)
    assert [p["text"] for p in compact] == [p["text"] for p in original]
    assert not any(p.get("source_citations") for p in compact)
    assert size < full_size and json.loads(constrained.messages[-1]["content"])["omitted"]["source_citations"] == 1


def test_live_entry_and_checkpoint_replay_use_the_same_offered_id(tmp_path):
    class CitationBackend:
        name, max_attempts = "synthetic-citation", 1
        def __init__(self):
            self.calls = 0
        def generate(self, request):
            self.calls += 1
            payload = json.loads(request.messages[-1]["content"])
            offers = [c for p in payload["initial_context"]["passages"] for c in p.get("source_citations", [])]
            citation, = [c for c in offers if c["quote"] == MEASUREMENT]
            out = decision()
            out["objects"][0].update(name="Sample A", mentions=[{"evidence_id": citation["evidence_id"]}])
            out["bindings"][0].update(observation_id=payload["observations"][0]["observation_id"],
                                      evidence=[{"evidence_id": citation["evidence_id"]}])
            return BackendResponse(text=json.dumps(out), model="synthetic-citation")

    first_backend, second_backend = CitationBackend(), CitationBackend()
    original = run(backend=first_backend, trace=RunTrace(tmp_path / "first"))
    restored = run(backend=second_backend, trace=RunTrace(tmp_path / "second"),
                   resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert first_backend.calls == 1 and second_backend.calls == 0
    assert original["status"] == restored["status"] == "complete"
    assert original["observations"] == restored["observations"]

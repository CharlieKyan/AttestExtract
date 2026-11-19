"""Distinguish a located source claim, a linked proposal and a publishable field."""
import json

import pytest

from attestextract.entity_first import run_entity_first
from attestextract.entity_first_protocol import Entity, Fact, entity_record, fact_record, windows
from attestextract.models import BackendResponse


def block(text):
    return {"block_id": "body", "text": text, "start": 0, "end": len(text), "scope": "section",
            "context": [], "kind": "text"}


def citation(text, window="body"):
    return {"window": window, "quote": text}


def card(name, text, window="body"):
    return {"key": "local", "label": name, "mention": citation(text, window),
            "qualification": citation(text, window)}


def assignment(text, value_quote, *, value="80", window="body"):
    return {"field": "x", "value": value, "owner": "local", "purpose": "measurement",
            "direct": True, "fit": "supported", "reason": "The report gives this reading.",
            "value_source": citation(value_quote, window), "property_source": citation("load", window),
            "owner_source": citation("U7", window), "link_source": citation(text, window),
            "fit_source": citation(text, window)}


def observation(text, value_quote, definition="load", **changes):
    b = block(text)
    shown = windows([b])
    owner = entity_record(Entity(**card("U7", text)), shown, "section", "document", "inventory")
    fact = Fact(**{**assignment(text, value_quote), **changes})
    return fact_record(fact, shown, b, text, "document", {"x": definition}, {"local": owner}, None)


def test_broader_source_quote_retains_claim_and_unicode_coordinates():
    text = "🧰 U7 applied load 80 kg."
    fact = observation(text, "load 80 kg")
    assert fact["qualified"] and fact["grounding"]["method"] == "unique_subspan"
    assert fact["owner_source"]["quote"] == "U7"
    assert fact["grounding"]["context"]["quote"] == "load 80 kg"
    assert fact["value_source"]["start"] == text.index("80")
    assert text[fact["value_source"]["start"]:fact["value_source"]["end"]] == "80"
    assert fact["model_claim"]["value_source"]["quote"] == "load 80 kg"
    assert fact["raw_owner_ref"] == "local"


@pytest.mark.parametrize("text,quote,reason", [
    ("U7 recorded a load of 80 kg.", "80", "source readout"),
    ("U7 never reached a load of 80 kg.", "80", "source readout"),
    ("U7 had a hypothetical load of 80 kg.", "80", "source readout"),
    ("U7 recorded a load of 80 kg.", "80", "This is not a measured load."),
])
def test_nonaffirmative_statements_or_counterevidence_remain_details(text, quote, reason):
    expected = "recorded" in text and reason == "source readout"
    fact = observation(text, quote, reason=reason)
    assert fact["qualified"] is expected
    if not expected:
        assert fact["owner_source"]["quote"] == "U7" and fact["value"] == "80"
        assert any(e["reason"] == "assertion_or_rationale_requires_review" for e in fact["evidence_issues"])


def test_unique_coordinate_is_not_evidence_for_a_stricter_field_definition():
    fact = observation("U7 showed an average load of 80 kg.", "average load of 80 kg", "peak applied load")
    assert not fact["qualified"] and fact["owner_supported"]
    assert fact["evidence_issues"][0]["reason"] == "recovered_value_requires_field_review"


def test_field_cue_and_separate_fit_span_can_corroborate_a_recovered_value():
    fact = observation("U7 applied load 80 kg.", "80 kg", "Applied load. Preserve the reported unit.")
    assert fact["qualified"] and fact["numeric_component"]["quote"] == "80"
    missing = observation("U7 average load 80 kg.", "80 kg", "Applied load. Preserve the reported unit.")
    assert not missing["qualified"]


def test_recovered_qualified_phrase_keeps_literal_number_without_reclassifying_it():
    text = "U7 load reached at most 80 kg."
    result = observation(text, text, "peak load", value="at most 80", fit="unsupported", direct=False)
    assert not result["qualified"] and result["value"] == "at most 80"
    component = result["numeric_component"]
    assert text[component["start"]:component["end"]] == component["quote"] == "80"


def test_explicitly_excluded_alternative_can_support_the_affirmed_measurement():
    fact = observation("U7 measured load 80.", "80", "Measured load", reason="Measured, not simulated.")
    assert fact["qualified"]


def test_omitted_negation_visible_in_same_source_sentence_prevents_publication():
    text = "It is not true that U7 applied load 80."
    fact = observation(text, "80", link_source=citation("U7 applied load 80."), fit_source=citation("load"))
    assert fact["owner_supported"] and not fact["qualified"]


def test_scope_for_another_occurrence_cannot_ground_the_narrowed_entity():
    text = "East U7. West U7."
    proposal = card("U7", "East U7.")
    proposal["qualification"] = citation("West U7.")
    with pytest.raises(ValueError, match="outside_identity_proof"):
        entity_record(Entity(**proposal), windows([block(text)]), "section", "document", "inventory")


@pytest.mark.parametrize("mention", ["U7", "U70 passed inspection."])
def test_short_identifier_cannot_take_the_prefix_of_another_object(mention):
    text = "U70 passed inspection."
    proposal = card("U7", mention)
    with pytest.raises(ValueError, match="partial_identifier"):
        entity_record(Entity(**proposal), windows([block(text)]), "section", "doc", "inventory")


def test_owner_proof_cropped_inside_another_identifier_cannot_bind():
    text = "U7 was inspected. U70 load 80."
    b = block(text)
    shown = windows([b])
    owner = entity_record(Entity(**card("U7", "U7 was inspected.")), shown, "section", "doc", "inventory")
    raw = assignment("U70 load 80.", "80")
    raw["owner_source"] = {**citation("U7"), "occurrence": 1}
    result = fact_record(Fact(**raw), shown, b, text, "doc", {"x": "load"}, {"local": owner}, None)
    assert not result["qualified"] and not result["owner_supported"]


def test_qualifying_tail_in_task_cannot_be_ignored_on_recovery():
    fact = observation("U7 average load 80 kg.", "load 80 kg", "Load, limited to peak readings.")
    assert not fact["qualified"]


@pytest.mark.parametrize("source_unit,proposed,valid", [
    ("mm", "m", False), ("kPa", "Pa", False), ("mm/s", "mm", False),
    ("kg/m", "m", False), ("mm", "mm", True), ("µm", "µm", True),
])
def test_unit_must_be_an_attested_complete_unit(source_unit, proposed, valid):
    text = f"U7 applied load 80{source_unit}."
    result = observation(text, f"80{source_unit}", unit=proposed)
    assert result["qualified"] is valid
    if not valid:
        assert {"role": "unit", "reason": "unit_not_locally_attested"} in result["evidence_issues"]


def test_invalid_supporting_quote_never_erases_anchored_value_or_becomes_evidence():
    fact = observation("U7 load 80.", "80", property_source=citation("load of U7"), unit="wrong")
    assert fact["value"] == "80" and fact["property_source"] is None and not fact["qualified"]
    assert fact["model_claim"]["property_source"]["quote"] == "load of U7"
    assert all(s["quote"] != "load of U7" for s in fact["evidence"])


@pytest.mark.parametrize("text,quote,expected", [
    ("U7 load 80 or 80.", "load 80 or 80", "fragment_not_unique"),
    ("U7 load 180.", "load 180", "numeric_substring"),
])
def test_ambiguous_or_partial_numeric_anchor_cannot_be_recovered(text, quote, expected):
    with pytest.raises(ValueError, match=expected):
        observation(text, quote)


def test_failed_nomination_preserves_original_link_and_value_without_publication():
    class Model:
        def generate(self, req):
            packet = json.loads(req.messages[1]["content"])
            win = packet["windows"][0]
            proposal = card("U7", win["text"], win["id"])
            proposal["qualification"] = citation("unattested combined quote U7", win["id"])
            if packet["stage"] == "inventory":
                result = {"objects": [proposal]}
            else:
                result = {"nominations": [proposal],
                          "facts": [assignment(win["text"], win["text"], window=win["id"])]}
            return BackendResponse(text=json.dumps(result))

    report = run_entity_first("U7 load 80.", {"x": "load"}, "devices", Model(), max_calls=2)
    item, = report["observations"]
    assert item["raw_owner_ref"] == item["nomination_key"] == "local"
    assert item["nomination_label"] == "U7" and item["nomination_status"] == "pending"
    assert not item["qualified"] and item["owner_id"] is None
    assert report["nominations"][0]["status"] == "unresolved"
    assert not any(i["reason"] == "unknown_owner_reference" for i in report["issues"])

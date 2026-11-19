"""Observable decisions for production runs, material samples and environmental stations."""

import pytest

from attestextract.entity_first_facts import V3Fact, assess_fact, finalize_target
from attestextract.entity_first_protocol import attach_table_cells, windows
from attestextract.structure import build_structure


def cite(text, cell=None):
    return {"window": ("w@" + cell) if cell else "w", "quote": text}


def card(key, label, qualifier=None, *, kind="instance", aliases=()):
    return {"object_id": key, "label": label, "qualifier": qualifier, "kind": kind,
            "scope": "earlier-section", "section": "earlier-section", "evidence": [],
            "aliases": [{"name": a} for a in aliases]}


def decide(source, claim, cards=(), *, fields=None, nominations=None):
    block = {"block_id": "w", "start": 0, "end": len(source), "text": source, "scope": "later-section", "context": []}
    tables = build_structure(source).tables
    table = tables[0] if tables else None
    block["kind"] = "table" if table else "text"
    attach_table_cells(block, table, source)
    roster = {obj["object_id"]: obj for obj in cards}
    return assess_fact(V3Fact.model_validate(claim), windows([block]), block, source, "doc",
                       fields or {"x": "Mass of the material specimen"}, roster, set(roster), nominations or {}, table)


def prose(source, label, prop, value, **overrides):
    return dict({"field": "x", "source_subject": label, "subject_source": cite(label),
                 "target_id": "sample", "value": value, "value_source": cite(value),
                 "property_source": cite(prop), "link_source": cite(source), "fit_source": cite(source),
                 "fit": "supported", "reason": "Value directly reported in the source.", "purpose": "measurement"},
                **overrides)


def test_subject_column_has_an_independent_grounded_raw_mention():
    src = "The coating of specimen C had a thickness of 12 μm."
    claim = prose(src, "coating of specimen C", "thickness", "12", target_id=None, unit="μm")
    obs = decide(src, claim, [card("c", "specimen C")], fields={"x": "Coating thickness"})
    assert obs["entity"] == "coating of specimen C" and obs["direct"]["status"] == "supported"
    assert obs["fit"]["status"] == "supported" and not obs["eligible"] and obs["candidates"] == []
    claim["target_id"] = "c"
    assert decide(src, claim, [card("c", "specimen C")])["target"]["id"] is None


def test_later_section_alias_and_second_pass_nomination_are_allowed():
    src = "S14 had mass 8 kg."
    claim = prose(src, "S14", "mass", "8", unit="kg")
    obj = card("sample", "third batch", aliases=["S14"])
    assert decide(src, claim, [obj])["eligible"]
    assert decide(src, dict(claim, target_id=None, nomination_key="s"), [obj], nominations={"s": "sample"})["eligible"]


def test_homonyms_keep_all_competing_ids_and_explicit_scope_distinguishes():
    src = "Station 2 recorded flux 38 W."
    claim = prose(src, "Station 2", "flux", "38", target_id="east", unit="W")
    cards = [card("east", "Station 2", "East"), card("west", "Station 2", "West")]
    obs = decide(src, claim, cards, fields={"x": "Local flux"})
    assert obs["direct"]["label"] == "Station 2" and not obs["eligible"]
    assert {c["id"] for c in obs["candidates"]} == {"east", "west"}
    src = "West Station 2 recorded flux 38 W."
    claim.update(target_id="west", subject_qualifier="West", fit_source=cite(src), link_source=cite(src))
    assert decide(src, claim, cards)["eligible"]
    assert not decide(src, dict(claim, target_id="east"), cards[:1])["eligible"]


def test_unlocated_value_remains_a_visible_proposal_with_source_mention():
    src = "Specimen C had mass 81 kg."
    obs = decide(src, prose(src, "Specimen C", "mass", "8", unit="kg"), [card("sample", "Specimen C")])
    assert not obs["eligible"] and obs["value_source"] is None and obs["observation_id"] is None
    assert obs["owner_source"]["quote"] == "Specimen C" and obs["model_claim"]["value"] == "8"


@pytest.mark.parametrize("prefix,loses_binding", [("", True), ("West ", False)])
def test_late_homonym_uses_only_scope_proof_from_original_assignment(prefix, loses_binding):
    src = prefix + "Station 2 recorded flux 38 W."
    claim = prose(src, "Station 2", "flux", "38", target_id="west", unit="W")
    west = card("west", "Station 2")
    obs = decide(src, claim, [west], fields={"x": "Flux"})
    assert obs["eligible"]
    west.update(scope="West", qualifier="West")
    assert finalize_target(obs, {"west": west, "east": card("east", "Station 2", "East")}) is loses_binding
    assert obs["eligible"] is not loses_binding and obs["direct"]["label"] == "Station 2"
    assert {o["id"] for o in obs["candidates"]} == {"west", "east"}


@pytest.mark.parametrize("sentence,reason", [
    ("Specimen C never reached mass 8 kg.", "Measured mass."),
    ("Specimen C had predicted mass 8 kg.", "Measured mass."),
    ("Specimen C had mass 8 kg.", "This is raw mass, not calibrated mass."),
])
def test_explicit_field_conflict_goes_only_to_details(sentence, reason):
    obs = decide(sentence, prose(sentence, "Specimen C", "mass", "8", unit="kg", reason=reason),
                 [card("sample", "Specimen C")], fields={"x": "Calibrated mass"})
    assert obs["entity"] == "Specimen C" and obs["value_source"]["quote"] == "8" and not obs["eligible"]


@pytest.mark.parametrize("transpose", [False, True])
def test_table_statistics_and_local_axes_survive_transposition(transpose):
    if transpose:
        src = ('<table><tr><td colspan="2">Specimen</td><td>S7</td></tr>'
               '<tr><td rowspan="2">Mass/kg</td><td>Mean</td><td>8</td></tr>'
               '<tr><td>Max</td><td>10</td></tr></table>')
        s, p, v, leaf, wrong = "r0c2", "r1c0", "r1c2", "r1c1", "r2c1"
    else:
        src = ('<table><tr><td rowspan="2">Specimen</td><td colspan="2">Mass/kg</td></tr>'
               '<tr><td>Mean</td><td>Max</td></tr><tr><td>S7</td><td>8</td><td>10</td></tr></table>')
        s, p, v, leaf, wrong = "r2c0", "r0c1", "r2c1", "r1c1", "r1c2"
    f = {"source_subject": "S7", "subject_source": cite(None, s), "target_id": "sample",
         "field": "x", "property_source": cite(None, p), "value": "8", "unit": "kg", "value_source": cite(None, v),
         "link_source": cite(None, s), "fit_source": cite(None, p), "qualifier_sources": [cite(None, leaf)],
         "statistic": "Mean", "fit": "supported", "purpose": "measurement", "reason": "Reported specimen mass."}
    cards = [card("sample", "S7")]
    obs = decide(src, f, cards)
    assert obs["eligible"] and obs["source_property"] == "Mass/kg"
    for changes in ({"qualifier_sources": [cite(None, wrong)], "statistic": "Max"},
                    {"statistic": None}, {"unit": "g"}):
        held = decide(src, dict(f, **changes), cards)
        assert not held["eligible"] and held["direct"]["label"] == "S7"


def test_group_in_parallel_parameters_is_kept_once_without_a_member_assignment():
    src = ("<table><tr><td>Key</td><td>Value</td><td>Key</td><td>Value</td></tr>"
           "<tr><td>Line</td><td>A, B</td><td>Temperature/°C</td><td>36</td></tr></table>")
    f = {"source_subject": "A, B", "subject_source": cite(None, "r1c1"), "target_id": None,
         "field": "x", "property_source": cite(None, "r1c2"), "value": "36", "unit": "°C",
         "value_source": cite(None, "r1c3"), "link_source": cite(None, "r1c1"), "fit_source": cite(None, "r1c2"),
         "members": ["A", "B"], "fit": "supported", "purpose": "measurement", "reason": "Reported group value."}
    obs = decide(src, f, [card("a", "A"), card("b", "B")], fields={"x": "Temperature"})
    assert obs["direct"]["status"] == "supported" and obs["members"] == ["A", "B"]
    assert not obs["eligible"] and obs["target"]["id"] is None
    assert decide(src, dict(f, target_id="a"), [card("a", "A")])["observation_id"] == obs["observation_id"]
    other = decide(src.replace("Temperature/", "Room temperature/"), f, [], fields={"x": "Temperature"})
    assert other["direct"]["status"] == "unknown" and other["local_mention"]["quote"] == "A, B"


def test_reported_threshold_field_works_but_a_threshold_cannot_be_exported_as_measured_mass():
    src = "Specimen A has acceptance threshold 8 kg."
    claim = prose(src, "Specimen A", "threshold", "8", purpose="classification_threshold", unit="kg")
    assert decide(src, claim, [card("sample", "Specimen A")], fields={"x": "Acceptance threshold of specimen"})["eligible"]
    assert not decide(src, claim, [card("sample", "Specimen A")], fields={"x": "Mass of specimen, exclude thresholds"})["eligible"]

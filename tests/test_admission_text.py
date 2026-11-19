"""Prose admission treats source mentions, roster binding and field fit independently."""

import pytest

from attestextract.admission_text import assess_text


def span(source, word):
    i = source.index(word)
    return {"start": i, "end": i + len(word), "quote": word}


def assessment(source, clause, *, claim_changes=None, nomination=False, scope="section-1",
               label="Batch 7", definition="Registered load", other_objects=(), alias=None, prior=None):
    def cite(quote):
        return {"window": "b", "quote": quote}

    ref = "next" if nomination else "o"
    packet = {"windows": [{"id": "b", "start": 0, "text": source, "role": "primary",
                            "kind": "text", "scope": "section-1"}],
              "objects": [] if nomination else [{"object_id": ref, "label": label,
                                                 "aliases": [alias[0]] if alias else [], "scope": scope}],
              "task": {"fields": {"f": definition}}}
    packet["objects"].extend(other_objects)
    answer = {"nominations": [{"key": ref, "label": label, "kind": "instance"}] if nomination else []}
    obj = {"object_id": "o", "label": label, "section": scope, "scope": scope, "kind": "instance",
           "qualifier": None, "aliases": [], "evidence": [span(source, label)]}
    if alias:
        obj["aliases"] = [{"name": alias[0], "evidence": span(source, alias[1])}]
    report = {"source_sha256": "doc", "objects": [obj], "nominations": []}
    fact = {"field": "f", "value": "80", "owner": ref, "owner_source": cite(label),
            "property_source": cite("load"), "value_source": cite("80"),
            "link_source": cite(clause), "fit": "supported", "fit_source": cite(clause),
            "direct": True, "unit": "kg", "purpose": "measurement", "reason": "Measured at the stated unit."}
    for k, v in (claim_changes or {}).items():
        fact[k] = cite(v) if k.endswith("_source") and isinstance(v, str) else v
    return assess_text(source, packet, answer, fact, report, prior, None)


@pytest.mark.parametrize("nomination,target_status", [(False, "supported"), (True, "unknown")])
def test_occurrences_are_resolved_only_in_a_fixed_assertion(nomination, target_status):
    clause = "Batch 7 load reached 80 kg"
    source = "Batch 7 was enrolled.\r\n" + clause + "."
    row = assessment(source, clause, nomination=nomination)
    assert [row[k]["status"] for k in ("direct", "target", "fit")] == ["supported", target_status, "supported"]
    assert row["direct"]["label"] == "Batch 7"
    assert row["direct"]["evidence"][0]["start"] == source.rindex("Batch 7")
    wrong = assessment(source, clause, claim_changes={"owner_source": {"window": "b", "quote": "Batch 7", "occurrence": 0}})
    assert wrong["direct"]["status"] == "unknown"
    assert wrong["target"]["status"] == "unknown"


def test_unresolved_roster_reference_and_conditions_do_not_erase_local_mention():
    clause = "Batch 7 load reached 80 kg"
    row = assessment(clause, clause, scope="other-section", claim_changes={"conditions": "peak period"})
    assert row["direct"]["status"] == "supported"
    assert row["target"]["status"] == row["fit"]["status"] == "unknown"
    anonymous = assessment(clause, clause, claim_changes={"owner": None},
                           label="reached")
    assert anonymous["target"]["status"] == "unknown"
    assert anonymous["direct"]["status"] == "unknown"


def test_explicit_local_subject_and_value_without_any_model_owner_id_or_card():
    clause = "Batch 7 load reached 80 kg"
    r = assessment(clause, clause, nomination=True, claim_changes={"owner": None, "direct": False})
    assert r["direct"]["label"] == "Batch 7"
    assert r["direct"]["status"] == r["fit"]["status"] == "supported"
    assert r["target"]["status"] == "unknown"


@pytest.mark.parametrize("source,clause", [
    ("If Batch 7 load reached 80 kg", "Batch 7 load reached 80 kg"),
    ("Batch 9 and Batch 7 load reached 80 kg", "Batch 7 load reached 80 kg"),
    ("Batch 7 load never reached 80 kg", "Batch 7 load never reached 80 kg"),
])
def test_no_id_path_does_not_accept_cropped_conditional_or_competing_owner(source, clause):
    row = assessment(source, clause, nomination=True, claim_changes={"owner": None})
    assert row["direct"]["status"] == row["target"]["status"] == "unknown"


@pytest.mark.parametrize("declaration,target", [
    ("Furnace Alpha (identifier F17)", "supported"),
    ("Furnace Alpha and F17 were compared", "unknown"),
    ("If Furnace Alpha (identifier F17)", "unknown"),
])
def test_cross_section_alias_needs_a_positive_local_definition(declaration, target):
    clause = "F17 load reached 80 kg"
    src = declaration + ". " + clause
    r = assessment(src, clause, label="Furnace Alpha", alias=("F17", declaration), scope="other",
                   claim_changes={"owner_source": "F17"})
    assert r["direct"]["status"] == "supported"
    assert r["target"]["status"] == target
    if target == "supported":
        assert r["target"]["alias_names"] == ["F17"]
        assert span(src, declaration) in r["target"]["evidence"]


@pytest.mark.parametrize("source,clause,expected", [
    ("Batch 7 load reached 80 kg.", "Batch 7 load reached 80 kg", "supported"),
    ("If Batch 7 load reached 80 kg, stop.", "Batch 7 load reached 80 kg", "unknown"),
    ("Batch 7 predicted load reached 80 kg.", "Batch 7 predicted load reached 80 kg", "unknown"),
    ("Batch 7 load never reached 80 kg.", "Batch 7 load never reached 80 kg", "unknown"),
])
def test_old_eligible_proposals_are_rechecked_against_the_full_assertion(source, clause, expected):
    row = assessment(source, clause, prior={"eligible": True})
    assert row["fit"]["status"] == expected
    assert row["value_source"] == span(source, "80")


@pytest.mark.parametrize("clause,changes", [
    ("Batch 7 load never reached 80 kg", {}),
    ("Batch 7 hypothetical load was 80 kg", {}),
    ("Batch 7 load reached 80 kg", {"reason": "Average load, not effective load."}),
    ("Batch 7 load reached 80 kg", {"unit": "g"}),
    ("Batch 7 load reached 80 kg", {"purpose": "classification_threshold"}),
])
def test_unit_assertion_and_purpose_conflicts_are_not_admitted(clause, changes):
    row = assessment(clause, clause, claim_changes=changes)
    assert row["value_source"] == span(clause, "80")
    assert row["fit"]["status"] != "supported"


def test_competing_card_without_its_own_value_clause_is_not_an_assignment():
    source = "Batch 7 and Batch 8 combined load reached 80 kg"
    row = assessment(source, source, other_objects=({"object_id": "other", "label": "Batch 8",
                                                      "scope": "section-1", "aliases": []},))
    assert row["direct"]["status"] == "unknown"


def test_unit_attached_to_another_number_does_not_establish_field_fit():
    clause = "Batch 7 load reached 80 oz and 40 kg"
    row = assessment(clause, clause)
    assert row["fit"]["status"] == "unknown"
    assert row["fit"]["reason"] == "unit_scope_requires_review"


def test_qualitative_statement_accepts_enclosing_property_definition_without_changing_science():
    clause = "Batch 7 was matte"
    context = "Grouped by texture, " + clause
    row = assessment(context, clause, definition="Texture and appearance; preserve original wording.",
                     claim_changes={"value": "matte", "value_source": clause, "property_source": clause,
                                    "fit_source": context, "unit": None, "reason": "Specifies texture."})
    assert row["value_source"] == span(context, "matte")
    assert row["fit"]["status"] == row["direct"]["status"] == row["target"]["status"] == "supported"


@pytest.mark.parametrize("context", ["For specimen records, ", "Grouped by specimen, "])
def test_descriptor_requires_an_attested_axis_and_value_relationship(context):
    clause = "Batch 7 color red"
    source = context + clause
    row = assessment(source, clause, definition="Condition of the specimen seal. Record only intact or broken.",
                     claim_changes={"value": "red", "value_source": clause, "property_source": "color",
                                    "fit_source": source, "unit": None, "reason": "The specimen is red."})
    assert row["fit"]["status"] == "unknown"


def test_numeric_separator_repair_never_recovers_partial_digits():
    clause = r"Batch 7 load ranged 15\~75"
    row = assessment(clause, clause, claim_changes={"value": "5~7", "value_source": clause})
    assert row["value_source"] is None


@pytest.mark.parametrize("literal,rendered", [(r"33%\~47%", "33%~47%"),
                                             (r"$( 3 3 . 1 - 4 7 ) \times 1 0 ^ { - 2 } ~ { \mu \mathrm { m } } ^ { 2 }$", "(33.1-47)×10^-2 μm^2")])
def test_rendered_quantities_refer_to_exact_raw_location_for_review(literal, rendered):
    source = "Batch 7 load ranged " + literal
    row = assessment(source, source, claim_changes={"value": rendered, "value_source": source, "unit": None})
    assert row["value_source"] == span(source, literal)
    assert row["fit"]["status"] == "unknown"
    if "^-" in rendered:
        changed = assessment(source, source, claim_changes={"value": rendered.replace("^-", "^"), "value_source": source})
        assert changed["value_source"] is None


def test_unsupported_measure_is_identified_by_its_actual_property():
    source = "Batch 7 reject ratio reached 80 percent"
    row = assessment(source, source, claim_changes={"fit": "unsupported", "property_source": "reject ratio",
                                                     "fit_source": None, "unit": "percent", "purpose": "aggregate"})
    assert row["source_property"] == "reject ratio"
    assert row["fit"]["status"] == "contradicted"

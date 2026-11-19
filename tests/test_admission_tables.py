"""Independent source examples for cell roles, property scope and subject granularity."""

import pytest

from attestextract.admission_tables import assess_table
from attestextract.structure import build_structure


def _grid_case(source, definition, cells, coords, *, value="8", unit="kg", statistic="Mean", kind="instance"):
    table, = build_structure(source).tables
    value_cell, owner_cell, property_cell, role_cell, statistic_cell = coords
    name = cells[owner_cell]

    def cite(key):
        return {"window": "unit-table@" + key}

    packet = {"windows": [{"id": "unit-table", "role": "primary", "start": 0, "text": source, "cells": cells}],
              "objects": [], "task": {"fields": {"identity": "Unit identifier", "reading": definition}}}
    nomination = {"key": "proposed", "label": name, "mention": cite(owner_cell),
                  "qualification": cite(role_cell), "kind": kind}
    identity = {"field": "identity", "value": name, "owner": "proposed", "direct": True, "fit": "supported",
                "property_source": cite(role_cell), "value_source": cite(owner_cell)}
    fact = {"field": "reading", "value": value, "owner": "proposed", "direct": True,
            "fit": "supported", "unit": unit, "conditions": None, "statistic": statistic,
            "owner_source": cite(owner_cell), "link_source": cite(owner_cell),
            "property_source": cite(property_cell), "value_source": cite(value_cell),
            "fit_source": cite(statistic_cell), "qualifier_sources": [], "purpose": "measurement"}
    answer = {"facts": [identity, fact], "nominations": [nomination]}
    report = {"source_sha256": "frozen", "nominations": []}
    return source, table, packet, answer, fact, report, None, "identity"


def _case(transpose=False, *, title="Mass/kg", role="Unit", definition="Measured mass of each unit"):
    # Material specimens measured at two statistics, encoded without HTML header tags.
    if transpose:
        rows = (f'<tr><td colspan="2">{role}</td><td>B01</td></tr>'
                f'<tr><td rowspan="2">{title}</td><td>Mean</td><td>8</td></tr>'
                '<tr><td>Maximum</td><td>10</td></tr>')
        coords = ("r1c2", "r0c2", "r1c0", "r0c0", "r1c1")
        cells = {"r0c0": role, "r0c2": "B01", "r1c0": title, "r1c1": "Mean", "r1c2": "8",
                 "r2c1": "Maximum", "r2c2": "10"}
    else:
        rows = (f'<tr><td rowspan="2">{role}</td><td colspan="2">{title}</td></tr>'
                '<tr><td>Mean</td><td>Maximum</td></tr><tr><td>B01</td><td>8</td><td>10</td></tr>')
        coords = ("r2c1", "r2c0", "r0c1", "r0c0", "r1c1")
        cells = {"r0c0": role, "r0c1": title, "r1c1": "Mean", "r1c2": "Maximum", "r2c0": "B01",
                 "r2c1": "8", "r2c2": "10"}
    return _grid_case("<table>" + rows + "</table>", definition, cells, coords)


@pytest.mark.parametrize("transpose", [False, True])
def test_nested_ordinary_cells_support_scoped_subject_and_statistic(transpose):
    args = _case(transpose)
    result = assess_table(*args)
    assert [result[k]["status"] for k in ("direct", "target", "fit")] == ["supported"] * 3
    assert result["direct"]["label"] == "B01" and result["target"]["id"] == result["direct"]["id"]
    assert {s["quote"] for s in result["evidence"]} >= {"Unit", "B01", "Mass/kg", "Mean", "8"}
    assert all(args[0][s["start"]:s["end"]] == s["quote"] for s in result["evidence"])


@pytest.mark.parametrize("changes", [
    {"purpose": "classification_threshold"}, {"unit": "g"}, {"unit": None}, {"statistic": None},
    {"statistic": "Maximum", "fit_source": {"window": "unit-table@r1c2"}},
    {"qualifier_sources": [{"window": "unit-table@r2c2"}]},
])
def test_single_wrong_qualifier_blocks_field_only(changes):
    args = _case()
    args[4].update(changes)
    result = assess_table(*args)
    assert result["fit"]["status"] != "supported"
    assert result["direct"]["label"] == "B01" and result["target"]["status"] == "supported"


def test_full_property_and_role_cells_prevent_cropping_qualifiers():
    args = _case(title="Model-predicted mass/kg", role="Model Unit")
    args[4]["property_source"]["quote"] = "mass"
    args[3]["nominations"][0]["qualification"]["quote"] = "Unit"
    result = assess_table(*args)
    assert result["source_property"] == "Model-predicted mass/kg"
    assert result["fit"]["status"] == result["target"]["status"] == "unknown"


def test_excluded_property_in_task_is_not_positive_evidence():
    args = _case(definition="Record only effective mass. Exclude raw mass.")
    assert assess_table(*args)["fit"]["status"] == "unknown"


@pytest.mark.parametrize("title", ["Mass (predicted)/kg", "Mass/model setting/kg"])
def test_parenthetical_or_slash_modifiers_are_not_units_to_strip(title):
    args = _case(title=title)
    args[4]["property_source"]["quote"] = "Mass"
    assert assess_table(*args)["fit"]["status"] == "unknown"


@pytest.mark.parametrize("title,reason", [
    ("Acceptance limits by classification", "classification_scope_requires_review"),
    ("Predicted values for stations", "measurement_scope_requires_review"),
])
def test_caption_changes_result_even_with_same_model_claim(title, reason):
    args = list(_case())
    caption = "Table 2. " + title + "\n\n"
    args[0] = caption + args[0]
    args[1], = build_structure(args[0]).tables
    args[2]["windows"][0]["start"] = len(caption)
    result = assess_table(*args)
    assert result["fit"]["reason"] == reason


def test_explicit_subtotal_is_not_an_individual_roster_entry():
    args = list(_case())
    args[0] = args[0].replace("B01", "Subtotal")
    args[1], = build_structure(args[0]).tables
    args[2]["windows"][0].update(text=args[0])
    args[2]["windows"][0]["cells"]["r2c0"] = "Subtotal"
    args[3]["nominations"][0]["label"] = args[3]["facts"][0]["value"] = "Subtotal"
    result = assess_table(*args)
    assert result["direct"]["label"] == "Subtotal"
    assert result["target"]["status"] == "unknown"


def test_repeated_panel_collection_is_explorable_once():
    # Generation reports a temperature shared by a pair of production units.
    source = ("<table><tr><td>Key</td><td>Value</td><td>Key</td><td>Value</td></tr>"
              "<tr><td>Unit</td><td>A, B</td><td>Temperature/°C</td><td>36</td></tr></table>")
    cells = {"r0c0": "Key", "r0c1": "Value", "r0c2": "Key", "r0c3": "Value", "r1c0": "Unit",
             "r1c1": "A, B", "r1c2": "Temperature/°C", "r1c3": "36"}
    args = _grid_case(source, "Temperature", cells, ("r1c3", "r1c1", "r1c2", "r1c0", "r1c2"),
                      value="36", unit="°C", statistic=None, kind="group")
    args[4].update(direct=False, link_source=args[4]["property_source"], fit="unknown")
    result = assess_table(*args)
    assert result["direct"]["label"] == "A, B"
    assert result["members"] == ["A", "B"]
    assert result["target"]["status"] == result["fit"]["status"] == "unknown"
    args[4].update(fit="unsupported", reason="Temperature of the room, not of the units.")
    assert assess_table(*args) == {}


def test_compound_parameter_requires_subject_and_separate_property_cue():
    source = ("<table><tr><td>Parameter</td><td>Value</td></tr>"
              "<tr><td>Bench A: Mass/kg</td><td>8</td></tr></table>")
    cells = {"r0c0": "Parameter", "r0c1": "Value", "r1c0": "Bench A: Mass/kg", "r1c1": "8"}
    args = _grid_case(source, "Mass of unit", cells, ("r1c1", "r1c0", "r1c0", "r0c0", "r1c0"),
                      statistic=None)
    args[2]["objects"] = [{"object_id": "shown", "label": "Bench A"}]
    args[3]["nominations"] = []
    args[4].update(owner="shown", purpose="model_input", fit_source=None)
    result = assess_table(*args)
    assert result["direct"]["label"] == "Bench A" and result["source_property"] == "Mass/kg"
    assert result["target"]["status"] == "unknown"
    args[2]["objects"][0]["label"] = "Bench"
    assert assess_table(*args) == {}


def test_unshown_or_partial_value_cell_cannot_supply_an_identity():
    args = _case()
    del args[2]["windows"][0]["cells"]["r2c1"]
    assert assess_table(*args) == {}
    args = _case()
    args[4].update(value="1", value_source={"window": "unit-table@r2c2", "quote": "1"})
    assert assess_table(*args) == {}

import csv

import pytest

from niceextract.dataset_io import save_dataset
from niceextract.dataset_measurement_use import table_measurement_uses
from niceextract.dataset_statistics import group_statistic_rows, set_statistic_columns, value_statistic
from niceextract.structure import build_structure


def reading(source, value, conditions=None, unit="kg"):
    entry = {"fields": ["value"], "span": {"start": 0, "end": len(source)}, "quote": source}
    return {"value": value, "unit": unit, "conditions": conditions, "source_evidence": [entry]}


@pytest.mark.parametrize("source, value, unit, expected", [
    ("Mass 2–8 kg, mean 5 kg.", "2–8", "kg", ("range", {"min": "2", "max": "8"})),
    ("Mass 2–8 kg, mean 5 kg.", "5", "kg", ("avg", {"avg": "5"})),
    ("Duration 8 min.", "8", "min", ("value", {"value": "8"})),
    ("Speed at least 12 m/s.", "12", "m/s", ("bound", {"lower_bound": "12", "bound_operator": ">="})),
    ("Speed no more than 12 m/s.", "12", "m/s", ("bound", {"upper_bound": "12", "bound_operator": "<="})),
    ("宽度一般在10 m以上。", "10", "m", ("bound", {"lower_bound": "10", "bound_operator": ">=", "qualifier": "一般"})),
    ("Pressure was typically below 10 kPa.", "10", "kPa",
     ("bound", {"upper_bound": "10", "bound_operator": "<", "qualifier": "typically"})),
    ("Width 10 m以下。", "10 m以下", "m", ("bound", {"upper_bound": "10", "bound_operator": "<="})),
    ("Display read &gt;3", "&gt;3", None, ("bound", {"lower_bound": "3", "bound_operator": ">"})),
    ("Station S <sup>1</sup> recorded a value.", "S <sup>1</sup>", None, (None, {})),
    ("At above ambient temperature, mass was 8 kg.", "8", "kg", ("value", {"value": "8"})),
    ("Pressure was not above 12 kPa.", "12", "kPa", ("qualified", {})),
    ("Temperature was not consistently above 10 C.", "10", "C", ("qualified", {})),
    ("The 95% confidence interval was 2–8 kg.", "2–8", "kg", ("interval", {})),
    ("95% CI 2–8 kg.", "2–8", "kg", ("interval", {})),
    ("Area (2.0-9.50)×10^-3 mm^2.", "(2.0-9.50)×10^-3 mm^2", "×10^-3 mm^2",
     ("range", {"min": "2.0", "max": "9.50"})),
    ("Setpoint 0.31.", "0.31", None, ("value", {"value": "0.31"})),
    ("Signal 1e999999999999999999999999-2e999999999999999999999999.",
     "1e999999999999999999999999-2e999999999999999999999999", None, (None, {})),
    ("Maximum 8 kg and minimum 2 kg.", "2", "kg", ("min", {"min": "2"})),
    ("Mean pressure was 15 kPa. Sample B temperature was 40 C.", "40", "C", ("value", {"value": "40"})),
    ("材料平均强度为5 MPa（温度20 °C）。", "20", "°C", ("value", {"value": "20"})),
    ("材料强度为5 MPa（平均温度20 °C）。", "5", "MPa", ("value", {"value": "5"})),
    ("Mean strength (at 20 °C) was 5.75 MPa.", "5.75", "MPa", ("avg", {"avg": "5.75"})),
    ("材料强度为5 MPa（平均温度20 °C）。", "20", "°C", ("avg", {"avg": "20"})),
    ("Avg A 5.0 with B 3.2.", "3.2", None, ("value", {"value": "3.2"})),
    ("Mass 5 g with mean length 3 cm.", "5", "g", ("value", {"value": "5"})),
    ("均值为5 MPa 温度20 °C。", "20", "°C", ("value", {"value": "20"})),
    ("Mass 5 g (mean).", "5", "g", ("avg", {"avg": "5"})),
])
def test_value_specific_statistic_keeps_source_unit_and_scale(source, value, unit, expected):
    assert value_statistic(source, reading(source, value, unit=unit), None) == expected


def test_condition_citing_whole_sentence_cannot_transfer_a_statistic():
    source = "Average mass 4.8 kg. Temperature 23 °C."
    obs = reading(source, "23", conditions=source, unit="°C")
    obs["source_evidence"][0]["fields"].append("conditions")
    assert value_statistic(source, obs, None) == ("value", {"value": "23"})


def test_comparator_in_full_owner_citation_qualifies_only_the_same_reading():
    first = "Unit X was generally above 10 C."
    source = first + " Unit Y was 10 C."
    reading_x = source.index("10")
    reading_y = source.rindex("10")
    owner_ref = {"fields": ["entity"], "span": {"start": 0, "end": len(first)}, "quote": first}
    for start, expected in [(reading_x, ("bound", {"lower_bound": "10", "bound_operator": ">",
                                                   "qualifier": "generally"})),
                            (reading_y, ("value", {"value": "10"}))]:
        obs = {"value": "10", "unit": "C", "source_evidence": [
            {"fields": ["value"], "span": {"start": start, "end": start + 2}, "quote": "10"}, owner_ref]}
        assert value_statistic(source, obs, None) == expected


def series(source, values):
    rows, proofs, observations = [], [], []
    for i, (value, condition) in enumerate(values):
        oid, rid = f"obs{i}", f"row{i}"
        obs = reading(source, value, condition)
        kind, parts = value_statistic(source, obs, None)
        obs.update(observation_id=oid, field="mass", row_ids=[rid], entity="Unit A",
                   target={"id": "unit-A", "label": "Unit A"}, direct={"id": "unit-A", "label": "Unit A"},
                   statistic_kind=kind, statistic_parts=parts, measurement_use="unspecified")
        observations.append(obs)
        rows.append({"mass": value, "mass_avg": None})
        proofs.append({"row_id": rid, "delivery": "confirmed", "cells": {"mass": [oid]},
                       "context": [], "status": "已确认", "subject_label": "Unit A", "observations": [oid]})
    group_statistic_rows(rows, proofs, observations, [], source)
    data = {"schema": ["mass", "mass_avg"], "identity_fields": [], "status_column": "status",
            "lineage": {"rows": proofs}, "observations": observations}
    set_statistic_columns(data)
    return rows, proofs, observations, data["statistic_columns"]


def test_wide_main_csv_links_combined_cells_preserving_configured_field(tmp_path):
    source = "Unit A mass 2–8 kg, mean 5 kg."
    rows, proofs, obs, cols = series(source, [("2–8", "range"), ("5", "mean")])
    assert len(rows) == 1
    assert obs[0]["row_ids"] == obs[1]["row_ids"] == [proofs[0]["row_id"]]
    links = proofs[0]["statistics"]
    assert links["mass_max"] == {"value": "8", "observations": ["obs0"]}
    assert links["mass_avg（抽取）"] == {"value": "5", "observations": ["obs1"]}
    path = save_dataset({"schema": ["mass", "mass_avg"], "rows": rows, "lineage": {"rows": proofs},
                         "observations": obs, "identity_fields": [], "status_column": "status",
                         "statistic_columns": cols}, tmp_path / "output")
    with (path / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert row["mass_min"] == "2" and row["mass_avg"] == "" and row["mass_unit"] == "kg"
    assert row["mass_avg（抽取）"] == "5"


@pytest.mark.parametrize("source, values", [
    ("Batch A mass 2–8 kg, batch B mean 5 kg.", [("2–8", "range"), ("5", "mean")]),
    ("Unit A mass 2–8 kg, mean 5 kg.", [("2–8", "early; range"), ("5", "late; mean")]),
    ("Unit A mass 2–8 kg, mean 5 kg, mean 6 kg.", [("2–8", "range"), ("5", "mean"), ("6", "mean")]),
    ("Unit A mass 2–8 kg, mean 15 kg.", [("2–8", "range"), ("15", "mean")]),
])
def test_same_citation_or_unit_cannot_join_conflicting_series(source, values):
    rows, _, _, _ = series(source, values)
    assert len(rows) == len(values)


@pytest.mark.parametrize("transpose", [False, True])
def test_classification_caption_applies_only_to_the_cutoff_series(transpose):
    cells = [
        ["Grade", "I", "II", "III"],
        ["Score", "&lt;5", "5–10", "&gt;10"],
        ["Hours", "1–7", "2–9", "5–12"],
    ]
    if transpose:
        cells = list(zip(*cells))
    text = "Table 2 Classification of batches\n\n<table>" + "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in cells) + "</table>"
    structure = build_structure(text)
    uses = table_measurement_uses(text, structure)
    labeled = {text[c.span.start:c.span.end] for c in structure.tables[0].cells if c.cell_id in uses}
    assert labeled == {"&lt;5", "5–10", "&gt;10"}
    for use, proof in uses.values():
        assert use == "classification_threshold"
        assert any(text[s["start"]:s["end"]] == s["quote"] == "Table 2 Classification of batches" for s in proof)


def test_sensitivity_ranges_are_distinct_from_observed_ranges_with_identical_numbers():
    text = ("Table 1 Measured temperatures\n\n<table><tr><td>Batch A</td><td>1–3</td></tr></table>\n\n"
            "Table 2 Parameter ranges for sensitivity analysis\n\n"
            "<table><tr><td>Batch A</td><td>1–3</td></tr></table>")
    uses = table_measurement_uses(text, build_structure(text))
    assert [v[0] for v in uses.values()] == ["measured", "sensitivity_input_range"]


def test_mixed_measurement_and_prediction_columns_do_not_inherit_a_measured_use():
    source = ("Table 2 Measured properties\n\n<table><tr><td>Property</td><td>Observed</td>"
              "<td>Predicted</td></tr><tr><td>Rate</td><td>2</td><td>3</td></tr></table>")
    assert not table_measurement_uses(source, build_structure(source))


def test_equipment_specifications_do_not_describe_measurements():
    source = ("Table 1. Measurement instrument specifications\n\n"
              "<table><tr><td>Instrument</td><td>Rated load (kg)</td></tr>"
              "<tr><td>Meter A</td><td>80</td></tr></table>")
    assert not table_measurement_uses(source, build_structure(source))


def test_bound_columns_survive_csv_round_trip_without_becoming_sample_extrema(tmp_path):
    source = "Unit A mass is usually at most 18 kg."
    rows, proofs, obs, cols = series(source, [("18", None)])
    data = {"schema": ["mass", "mass_avg"], "rows": rows, "lineage": {"rows": proofs},
            "observations": obs, "identity_fields": [], "status_column": "status", "statistic_columns": cols}
    path = save_dataset(data, tmp_path / "result")
    with (path / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert row["mass_upper_bound"] == "18" and row["mass_bound_operator"] == "<="
    assert row["mass_qualifier"] == "usually"
    assert row["mass_min"] == row["mass_max"] == ""
    assert proofs[0]["statistics"]["mass_upper_bound"]["observations"] == ["obs0"]

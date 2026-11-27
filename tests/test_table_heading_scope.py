from types import SimpleNamespace

import pytest

from niceextract.dataset_resolution_table import TablePaths
from niceextract.structure import build_structure
from niceextract.table_coverage import _header_field, coverage_targets
from niceextract.table_routes import build_table_input


def test_literal_property_precedes_unit_notation_and_semantic_qualifiers_survive():
    fields = {"AB": "AB", "ratio": "AB/CD ratio", "area": "Area", "net_area": "Area (net)",
              "outcome": "Reported outcomes; yield per lot. Exclude other properties."}
    for heading, field in [("AB/CD ratio", "ratio"), ("Area (net)", "net_area"),
                           ("Area (raw)", None), ("Area/kg", "area"), ("Yield", "outcome")]:
        assert _header_field(heading, fields) == field
    assert _header_field("Other properties", fields) is None


@pytest.mark.parametrize("transposed", [True, False])
def test_local_merged_title_and_subheadings_keep_separate_value_lanes(transposed):
    if transposed:
        body = ('<tr><td rowspan="3">Monitoring</td><td>Station</td><td>N</td></tr>'
                '<tr><td>Mass/kg</td><td>2</td></tr><tr><td>Code</td><td>8</td></tr>')
    else:
        body = ('<tr><td colspan="3">Monitoring</td></tr><tr><td>Station</td><td>Mass/kg</td><td>Code</td></tr>'
                '<tr><td>N</td><td>2</td><td>8</td></tr>')
    table = build_table_input("<table>" + body + "</table>")
    cells = {e["quote"]:e["ref"] for e in table["entries"]}
    task = SimpleNamespace(input_materials=SimpleNamespace(field_list={"mass": "Mass reported for each station."}))
    assert coverage_targets(task, table, {"records": []}) == {"mass": {cells["2"]: cells["Mass/kg"]}}


@pytest.mark.parametrize("middle,expected", [("3e-2 mg", "row"), ("3 trays", None), ("East", None)])
def test_intervening_measurements_and_panel_labels_have_different_paths(middle, expected):
    source = "<table><tr><td>A17</td><td>" + middle + "</td><td>32</td></tr></table>"
    structure = build_structure(source)
    table = structure.tables[0]
    paths = TablePaths(source, structure)
    owner, _, value = table.cells
    assert paths._axis(owner, value, table) == expected

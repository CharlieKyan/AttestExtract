"""Source-role regressions with non-geological prose and parameter panels."""
import pytest

from niceextract.dataset_resolution import resolve_observations
from niceextract.dataset_resolution_table import TablePaths
from niceextract.structure import build_structure
from niceextract.table_routes import build_table_input
from test_dataset_resolution import _object, _observation, _span


def setting(text, definition="temperature", subject="named model devices"):
    item = _observation(text, "reading", text, "Unit R", "80", binding_status="as_extracted")
    evidence = {"evidence_id": "reading-source", **_span(text, text)}
    obj = _object("model-r", "model Unit R", "reading-source")
    snapshot = {"observations": [item], "task": {"fields": {"mass": definition}},
                "table_subject": subject, "objects": [obj], "evidence": [evidence], "relations": []}
    return resolve_observations(text, {}, snapshot, identity_fields=["label"], row_subject=subject)["reading"]


def test_existing_registration_grounds_model_setting_in_its_own_occurrence():
    text = "In the model Unit R temperature was set to 80."
    result = setting(text)
    assert result["purpose"] == "model_input"
    assert result["target"]["id"] == "model-r"
    assert result["direct"]["label"] == "Unit R"
    assert result["direct"]["evidence"][0]["quote"] == text


@pytest.mark.parametrize(("text", "definition"), [
    ("In the model Unit R was disabled, Unit S temperature was set to 80.", "temperature"),
    ("In the model Unit R temperature was not set to 80.", "temperature"),
    ("In the model Unit R temperature was set to 80.", "measured temperature only"),
])
def test_another_device_negation_and_task_exclusions_do_not_certify_a_setting(text, definition):
    assert setting(text, definition)["direct"]["basis"] != "cited_model_assignment"


def test_model_setting_cannot_enter_an_in_situ_measurement_field():
    result = setting("In the model Unit R temperature was set to 80.", "in situ temperature")
    assert result["fit"]["status"] == "contradicted"
    assert result["purpose"] == "model_input"


def test_location_elsewhere_cannot_block_a_different_occurrence_of_a_common_name():
    from niceextract.dataset_source_roles import _context_at_occurrence
    from types import SimpleNamespace

    source = "# East\nPlant A uses North as its location.\n# West\nNorth temperature was 15."
    location = _span(source, "Plant A uses North as its location.")
    reading = _span(source, "North temperature was 15.")
    doc = SimpleNamespace(source=source, scope=lambda p: "west" if p >= source.index("# West") else "east",
                          identities={}, labels={"North", "Plant A"})
    assert _context_at_occurrence(doc, "North", [reading], {"North": [location]}) == []
    assert _context_at_occurrence(doc, "North", [location], {"North": [location]}) == [location]


@pytest.mark.parametrize("transpose", [False, True])
def test_combined_parameter_cell_cannot_reach_into_neighboring_pair(transpose):
    matrix = [["Reference drift", "2", "Station temperature", "80"]]
    if transpose:
        matrix = list(zip(*matrix))
    source = "<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
                                 for row in matrix) + "</table>"
    cells = {c["quote"]: {**c, "selected_start": c["start"], "selected_end": c["end"],
                          "selected_quote": c["quote"]} for c in build_table_input(source)["entries"]
             if c["kind"] == "cell"}
    paths = TablePaths(source, build_structure(source))
    obs = {"entity": "Station", "record_type": "signal"}
    def pair(value):
        fields = {"entity": [cells["Station temperature"]], "value": [cells[value]]}
        return paths.path(obs, {"scope": "individual", "resolved_fields": fields}, "table-0001",
                          definition="temperature")

    assert pair("80")["combined_parameter"]["property"] == "temperature"
    assert pair("2")["gap"] == "owner_label_unconfirmed"

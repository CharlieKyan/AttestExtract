"""The same source value can be a label, an amount or a source-qualified bound."""

import json
import pytest

from niceextract import extract, save_dataset
from niceextract.backends import ReplayBackend
from niceextract.dataset_field_fit import apply_value_type
from niceextract.jev_packets import needs_review
from niceextract.jev_review import review_dataset


def deliver(definition, rule, raw, role=None, unit=None, *, statement=None, use="measured"):
    statement = statement or f"The sample Q {definition} was {raw}{' ' + unit if unit else ''}."
    record = {"record_type": "field", "statement_basis": "reported", "reported_use": use,
              "values": {"entity": "sample Q", "value": raw, "unit": unit, "conditions": None},
              "quotes": {"entity": statement, "value": statement, "unit": statement if unit else "", "conditions": ""},
              "source_property": definition, "name_handling": "as_reported"}
    if role:
        record["value_role"] = role
    task = {"prompt_description": "Collect the requested property.", "field_list": {"field": definition},
            "field_types": {"field": rule}}
    return extract(statement, task, backend=ReplayBackend([{"records": [record]}]), table_mode="text",
                   observation_protocol="source-v2", dataset_options={"row_subject": "named samples"}).dataset


@pytest.mark.parametrize("grade", ["316", "003"])
def test_text_identifiers_and_their_digits_survive_without_generating_a_measurement(grade):
    data = deliver("grade code", {"kind": "text"}, grade, "label")
    obs, = data["observations"]
    assert obs["row_ids"] and obs["value"] == grade and obs["field_type_status"] == "compatible"
    assert not obs["statistic_kind"] and obs["numeric_shape"] is None
    assert not needs_review(data, obs, set())


@pytest.mark.parametrize("value,unit", [("5 ppm", None), ("5", "ppm")])
def test_combined_and_split_concentration_produce_the_same_scalar(value, unit):
    data = deliver("concentration", {"kind": "numeric"}, value, "quantity", unit)
    obs, = data["observations"]
    assert (obs["value"], obs["unit"]) == (value, unit)
    assert obs["row_ids"] and (obs["numeric_shape"], obs["numeric_value"], obs["numeric_unit"]) == (
        "scalar", {"value": "5"}, "ppm")


def test_exponent_is_not_a_unit_and_shared_scale_reaches_both_endpoints():
    data = deliver("ratio", {"kind": "numeric"}, "1e-3", "quantity")
    ratio, = data["observations"]
    assert (ratio["numeric_value"], ratio["numeric_unit"]) == ({"value": "1e-3"}, None)
    data = deliver("conductivity", {"kind": "numeric"}, "(1.2~3.1)×10^-3", "quantity", "S")
    obs, = data["observations"]
    assert obs["numeric_shape"] == "range" and obs["numeric_value"] == {"min": "1.2", "max": "3.1", "scale": "×10^-3"}
    assert obs["statistic_parts"] == {"min": "1.2×10^-3", "max": "3.1×10^-3"}


def test_role_and_shape_restrictions_do_not_change_reported_values_or_uses():
    data = deliver("material description", {"kind": "text"}, "21.7", "quantity", "%")
    amount, = data["observations"]
    assert not amount["row_ids"] and amount["field_type_status"] == "excluded"
    assert amount["value"] == "21.7" and amount["unit"] == "%"
    data = deliver("target concentration", {"kind": "numeric", "shapes": ["scalar"]},
                   "12~30", "quantity", "ppm", use="model_input")
    interval, = data["observations"]
    assert not interval["row_ids"] and interval["measurement_use"] == "model_input"
    assert interval["numeric_shape"] == "range" and interval["numeric_value"] == {"min": "12", "max": "30"}
    data = deliver("concentration", {"kind": "numeric"}, "5", "quantity", "ppm",
                   statement="The concentration of sample Q was at least 5 ppm.")
    bound, = data["observations"]
    assert bound["numeric_shape"] == "bound" and bound["numeric_value"]["bound_operator"] == ">="


def test_ambiguous_code_stays_visible_and_an_inferred_plan_cannot_exclude_it():
    data = deliver("grade code", {"kind": "text"}, "003")
    obs, = data["observations"]
    assert obs["row_ids"] and obs["field_type_status"] == "review"
    assert "字段类型待核" in data["lineage"]["rows"][0]["status"]
    obs["value_role"] = "label"
    apply_value_type("", obs, {"kind": "numeric", "source": "model", "shapes": ["scalar"]}, None)
    assert obs["field_type_status"] == "review" and not obs.get("field_excluded")


def test_model_text_role_with_a_standalone_measurement_unit_is_a_visible_disagreement():
    data = deliver("material name", {"kind": "text"}, "21.7", "label", "%")
    obs, = data["observations"]
    assert obs["row_ids"] and obs["field_type_status"] == "review" and obs["numeric_shape"] is None
    data = deliver("material name", {"kind": "text"}, "steel with 2% dye", "description")
    obs, = data["observations"]
    assert obs["row_ids"] and obs["field_type_status"] == "compatible"


def test_optional_jev_review_asks_about_use_and_preserves_the_direct_object(tmp_path):
    statement = "The sample Q grade code was 003."
    data = deliver("grade code", {"kind": "text"}, "003", statement=statement)
    save_dataset(data, tmp_path / "baseline")
    source_file = tmp_path / "source.md"
    source_file.write_bytes(statement.encode())

    class Scorer:
        identity = "typed-field-fixture"

        def predict(self, request):
            question = request["questions"]["disposition"]
            assert set(question["criteria"]) == {"supported", "mismatch", "uncertain"}
            assert "003" in request["state"] and statement in request["state"]
            return {"model": "Intern-Decision-4B", "answers": {"disposition": {
                "type": "choice", "choice": "supported", "confidence": 1,
                "probabilities": {key: int(key == "supported") for key in question["criteria"]}}}}

    path, report = review_dataset(source_file, tmp_path / "baseline", tmp_path / "opinion", Scorer())
    assert (report["scored"], report["subject_checks"], report["field_checks"]) == (1, 0, 1)
    obs, = json.loads((path / "dataset.json").read_bytes())["observations"]
    original, = data["observations"]
    for key in ("direct", "target", "value", "row_ids", "numeric_shape", "field_type_status"):
        assert obs[key] == original[key]
    assert obs["jev_review"]["field_verdict"] == "supported" and "支持按此字段使用" in (path / "main.csv").read_text("utf-8-sig")

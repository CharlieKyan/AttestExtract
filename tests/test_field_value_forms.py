"""A label field can keep measured components in the evidence detail at the user's chosen grain."""

from hashlib import sha256

import pytest

from niceextract import extract
from niceextract.dataset import validate_dataset_options
from niceextract.dataset_field_fit import value_form
from niceextract.dataset_reading_review import apply_reading_review
from niceextract.jev_packets import needs_review
from niceextract.table_routes import build_table_input
from test_source_statement_protocol import FixtureBackend


@pytest.mark.parametrize("forms,shown", [(["description"], False), (["quantity"], True), (None, True)])
def test_measured_component_under_a_categorical_heading_retains_raw_reading(forms, shown):
    table = "| Blend | Pigment | Red (%) |\n|---|---|---|\n| P | Red No. 4 | 12.5 |"
    cells = {entry["quote"].strip(): entry["ref"] for entry in build_table_input(table)["entries"]}

    def binding(value, quoted, relation):
        return {"value": value, "sources": [{"ref": cells[quoted], "quote": quoted}], "relation": relation}

    records = []
    for value, source_property in (("12.5", "Red"), ("Red No. 4", "Pigment")):
        fields = {"entity": binding("P", "P", "row"), "value": binding(value, value, "anchor")}
        if value == "12.5":
            fields["unit"] = binding("%", "Red (%)", "column")
            fields["conditions"] = binding("Red", "Red (%)", "column")
        records.append({"record_id": value, "record_type": "material", "scope": "individual",
                        "scope_sources": [], "anchors": fields["value"]["sources"], "fields": fields,
                        "source_property": source_property, "name_handling": "as_reported"})
    options = {"row_subject": "blends"}
    if forms:
        options["field_policies"] = {"material": {"allowed_value_forms": forms}}
    data = extract(table, prompt_description="Record the material of a blend.",
                   field_list={"material": "Material description."}, backend=FixtureBackend({"records": records}),
                   dataset_options=options).dataset
    amount, label = data["observations"]
    assert (amount["entity"], amount["value"], amount["unit"], amount["source_property"]) == ("P", "12.5", "%", "Red")
    assert amount["table_context"]["value"]["ref"] == cells["12.5"]
    assert bool(amount["row_ids"]) is shown
    assert bool(label["row_ids"]) is (forms != ["quantity"])
    if not shown:
        assert "allowed_value_forms" in amount["fit_blockers"]
        assert "12.5" not in [row["material"] for row in data["rows"]]
        assert not needs_review(data, amount, set())


@pytest.mark.parametrize("value,unit,expected", [
    ("oxide with 15% dye", None, "description"), ("3D", None, "description"),
    ("oxide grade 2", None, "description"), ("2–4", None, "number"),
    ("12%", None, "quantity"), ("1e-3", "mg", "quantity"), ("≤5", "kPa", "quantity"),
    ("12 ± 2%", None, "quantity"),
    ("37 kg", None, "quantity"), ("0.18–1.1 μm", None, "quantity"),
    ("3.26 × 10⁻³", "m", "quantity"), ("(1.2-3.1) × 10⁻³ μm²", None, "quantity"),
    ("4Z", None, "description"), ("Series 3", None, "description"),
    ("3 samples", None, "description"), ("12 wt% oxide", None, "description"),
])
def test_value_form_uses_the_complete_value(value, unit, expected):
    assert value_form(value, unit) == expected


def test_rereading_cannot_publish_a_quantity_through_a_description_only_field():
    quote = "Blend P had 22% dye."
    review = {"source_sha256": sha256(quote.encode()).hexdigest(), "reviewed_by": "test",
              "readings": [{"entity": "Blend P", "field": "pigment", "value": "22", "unit": "%",
                            "target": "Blend P", "statement_basis": "reported", "reason": "Reported dye amount.",
                            "quote": quote, "start": 0, "end": len(quote)}]}
    details = []
    apply_reading_review(quote, details, review, {"pigment"}, {"pigment": {"allowed_value_forms": ["description"]}})
    assert details[0]["filing_suppressed"] and "allowed_value_forms" in details[0]["fit_blockers"]
    assert details[0]["value"] == "22"


def test_unknown_value_form_fails_before_extraction():
    with pytest.raises(ValueError, match="allowed_value_forms"):
        validate_dataset_options(["material"], field_policies={"material": {"allowed_value_forms": ["material"]}})

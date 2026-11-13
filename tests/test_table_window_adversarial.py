"""Independent adversarial checks for table observation ownership."""

from copy import deepcopy

import pytest

from attestextract.document_routing import table_input_for_chunk
from attestextract.models import DocumentChunk
from attestextract.structure import build_structure
from attestextract.table_routes import evaluate_route
from attestextract.table_windows import filter_window_records, split_table_chunk, window_owned_refs
from attestextract.task_input import compile_task


SOURCE = ("<table><tr><th>Sample</th><th>Mass/g</th></tr>"
          "<tr><td>A</td><td>11</td></tr><tr><td>B</td><td>22</td></tr>"
          "<tr><td>C</td><td>33</td></tr></table>")


def foreign_value_record():
    structure = build_structure(SOURCE)
    span = structure.tables[0].span
    root = DocumentChunk(chunk_id="table-0001", text=SOURCE, segments=[span], body_segments=[span])
    child = split_table_chunk(SOURCE, root, structure=structure)[1]
    table = table_input_for_chunk(SOURCE, child, structure)
    cells = [entry for entry in table["entries"] if entry["kind"] == "cell"]
    owned = window_owned_refs(table)
    local = next(entry for entry in cells if entry["ref"] in owned and entry["quote"].isdigit())
    foreign = next(entry for entry in cells if entry["ref"] not in owned and entry["quote"].isdigit())
    entity = next(entry for entry in cells if entry["row"] == local["row"] and entry["column"] == 0)
    unit = next(entry for entry in cells if entry["quote"] == "Mass/g")

    def selection(entry):
        return {"ref": entry["ref"], "quote": entry["quote"]}

    record = {"record_id": "foreign-value", "record_type": "mass", "scope": "individual",
              "scope_sources": [], "anchors": [selection(local)], "fields": {
                  "entity": {"value": entity["quote"], "sources": [selection(entity)], "relation": "row"},
                  "value": {"value": foreign["quote"], "sources": [selection(foreign)], "relation": "column"},
                  "unit": {"value": "g", "sources": [selection(unit)], "relation": "column"},
                  "conditions": {"value": None, "sources": [], "relation": None}}}
    assert local["quote"] == "22" and foreign["quote"] == "11"
    return table, record


@pytest.mark.parametrize("relation", ["row", "column", "context", None])
def test_simple_value_cannot_borrow_another_windows_value_by_changing_relation(relation):
    table, record = foreign_value_record()
    record["fields"]["value"]["relation"] = relation
    task = compile_task(prompt_description="Extract mass", field_list={"mass": "Reported mass"})
    before = deepcopy(record)
    # Both columns geometrically align, so the column case previously passed
    # all checks while attributing A's 11 to B. Ownership must precede geometry.
    result = evaluate_route("A", {"records": [record]}, table, task.model_dump(mode="json"))
    assert not result["candidates"] and result["status"] == "format_error"
    assert result["parse_rejections"][0]["raw"] == before
    assert result["parse_rejections"][0]["errors"][0]["type"] == "anchor_outside_window"
    assert record == before


@pytest.mark.parametrize("field_type,explicit_group", [("number", False), ("integer", False), ("string", True)])
def test_declared_measurement_or_numeric_value_is_owned_even_without_anchor_relation(field_type, explicit_group):
    table, record = foreign_value_record()
    value = record["fields"].pop("value")
    value["value"] = "11" if field_type == "string" else 11
    record["fields"]["reading"] = value
    definition = {"fields": {
        "entity": {"type": "string"}, "reading": {"type": field_type},
        "unit": {"type": "string"}, "conditions": {"type": "string"}},
        "identity_fields": ["entity"], "condition_fields": ["conditions"]}
    if explicit_group:
        definition["measurement_groups"] = {"measurement": {"value_field": "reading", "unit_field": "unit"}}
    task = {"name": "synthetic", "record_types": {"mass": definition}}
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert not result["candidates"]
    assert result["parse_rejections"][0]["raw"]["fields"]["reading"]["value"] == value["value"]


def test_numeric_identity_condition_and_unit_sources_can_remain_shared_context():
    table, record = foreign_value_record()
    record["fields"] = {
        "reading": {"value": "22", "sources": deepcopy(record["anchors"]), "relation": "anchor"},
        **{name: {"value": 11, "sources": deepcopy(record["fields"]["value"]["sources"]), "relation": "context"}
           for name in ("sample_number", "temperature", "unit_code")},
    }
    task = {"name": "synthetic", "record_types": {"mass": {
        "fields": {"reading": {"type": "string"}, "sample_number": {"type": "integer"},
                   "temperature": {"type": "number"}, "unit_code": {"type": "integer"}},
        "identity_fields": ["sample_number"],
        "measurement_groups": {"reading": {"value_field": "reading", "unit_field": "unit_code",
                                             "condition_fields": ["temperature"]}},
    }}}
    assert filter_window_records({"records": [record]}, table, task)["records"] == [record]


def test_arbitrary_string_field_meaning_is_not_inferred_by_ownership_filter():
    table, record = foreign_value_record()
    task = {"name": "legacy", "record_types": {"mass": {"fields": {
        name: {"type": "string"} for name in record["fields"]}}}}
    # Existing custom string tasks need explicit measurement roles. This does
    # not claim semantic acceptance: only declared anchors are enforceable.
    assert filter_window_records({"records": [record]}, table, task)["records"] == [record]

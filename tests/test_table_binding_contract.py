"""Conditional table fields, format examples and unmodified rejected evidence."""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from attestextract.schema_diagnostics import _check
from attestextract.table_routes import (
    Binding, TABLE_REQUEST_REVISION, build_route_request, build_route_schema,
    build_table_input, evaluate_route, parse_route_response_partial,
)
from attestextract.task_input import resolve_task


SOURCE = "| Object | Pressure/kPa |\n|---|---|\n| Actual A | 12 |\n"
SELECTION = {"ref": "table-0001-r0001-c0001", "quote": "12"}
ABSENT = "<absent>"


def task():
    return resolve_task(
        prompt_description="Extract the stated pressure.",
        field_list={"pressure": "Reported pressure", "temperature": "Reported temperature"},
        examples=[{"text": "Synthetic B pressure is 8 kPa.", "extractions": [{
            "field": "pressure", "entity": "Synthetic B", "value": "8", "unit": "kPa",
            "conditions": None, "quote": "Synthetic B pressure is 8 kPa.",
        }]}],
    )


@pytest.mark.parametrize("value,sources,relation,valid", [
    ("12", [SELECTION], ABSENT, False),
    ("12", [SELECTION], None, False),
    ("12", [SELECTION], "anchor", True),
    (None, [], ABSENT, True),
    (None, [], None, True),
    (None, [], "cell", True),
    (None, [SELECTION], ABSENT, False),
    (None, [SELECTION], None, False),
    (None, [SELECTION], "anchor", True),
    ("12", [], ABSENT, False),
    ("", [], ABSENT, False),
    (0, [], None, False),
    (False, [], None, False),
    ("12", [], "anchor", True),
    (None, [], "unknown", False),
])
def test_outgoing_schema_and_local_reader_agree_on_relation_presence(value, sources, relation, valid):
    binding = {"value": value, "sources": deepcopy(sources)}
    if relation != ABSENT:
        binding["relation"] = relation
    schema = build_route_schema()["$defs"]["Binding"]
    # The existing schema evaluator covers the presence/type/enum constraints
    # used here. All Selection instances in this test are deliberately valid;
    # resolving the Pydantic $ref is outside this presence-contract regression.
    assert (not _check(binding, schema)) is valid
    if valid:
        parsed = Binding.model_validate(binding)
        assert parsed.value == value
        assert [(item.ref, item.quote) for item in parsed.sources] == [
            (item["ref"], item["quote"]) for item in sources
        ]
    else:
        with pytest.raises(ValidationError):
            Binding.model_validate(binding)


def test_simple_task_receives_complete_format_example_separate_from_actual_source():
    index = build_table_input(SOURCE)
    request = build_route_request("A", task().model_dump(mode="json"), index)
    payload = json.loads(request.messages[1]["content"])
    assert payload["table_request_revision"] == TABLE_REQUEST_REVISION == "dev31-table-a-3"
    assert payload["schema"] == request.output_schema
    assert payload["table"] == index
    assert set(payload["task"]["record_types"]) == {"pressure", "temperature"}
    assert len(payload["task_examples"]) == 1
    example = payload["format_example"]
    record = example["response"]["records"][0]
    assert record["fields"]["entity"]["relation"] == "row"
    assert record["fields"]["value"]["relation"] == "anchor"
    assert record["fields"]["unit"]["relation"] == "column"
    assert record["fields"]["conditions"] == {"value": None, "sources": [], "relation": None}
    parsed = parse_route_response_partial("A", json.dumps(example["response"]))
    assert len(parsed["records"]) == 1 and not parsed["rejections"]
    instructions = request.messages[0]["content"]
    assert "SYNTHETIC FORMAT GUIDANCE ONLY" in instructions
    assert "Never copy those facts or references" in instructions
    assert "TASK SEMANTIC GUIDANCE ONLY" in instructions
    assert "format_example" not in request.output_schema["properties"]
    assert not {e["ref"] for e in example["source_entries"]} & {e["ref"] for e in index["entries"]}
    # A response copied from the format example cannot become valid evidence.
    evaluation = evaluate_route("A", parsed, index, task().model_dump(mode="json"))
    assert evaluation["issues"]
    assert "unknown_source_ref" in {issue["code"] for issue in evaluation["issues"]}


def test_missing_relationship_is_quarantined_without_inventing_it():
    request = build_route_request("A", task().model_dump(mode="json"), build_table_input(SOURCE))
    example = json.loads(request.messages[1]["content"])["format_example"]["response"]
    damaged = deepcopy(example)
    for field in damaged["records"][0]["fields"].values():
        field.pop("relation")
    before = deepcopy(damaged)
    parsed = parse_route_response_partial("A", json.dumps(damaged))
    assert parsed["records"] == []
    assert len(parsed["rejections"]) == 1
    assert parsed["rejections"][0]["raw"] == before["records"][0]
    assert {tuple(error["loc"]) for error in parsed["rejections"][0]["errors"]} == {
        ("fields", "entity"), ("fields", "value"), ("fields", "unit"),
    }
    assert damaged == before


def test_repeated_object_in_other_section_is_not_silently_rebound():
    source = ("| Object | Pressure/kPa |\n|---|---|\n| Actual A | 1 |\n"
              "| Object | Pressure/kPa |\n| Actual A | 12 |\n")
    index = build_table_input(source)
    record = {"record_id": "observation-1", "record_type": "pressure", "fields": {
        "entity": {"value": "Actual A", "sources": [{
            "ref": "table-0001-r0001-c0000", "quote": "Actual A", "offset": None}], "relation": "row"},
        "value": {"value": "12", "sources": [{
            "ref": "table-0001-r0003-c0001", "quote": "12", "offset": None}], "relation": "anchor"},
        "unit": {"value": None, "sources": [], "relation": None},
        "conditions": {"value": None, "sources": [], "relation": None},
    }, "anchors": [{"ref": "table-0001-r0003-c0001", "quote": "12", "offset": None}],
       "scope": "individual", "scope_sources": []}
    before = deepcopy(record)
    parsed = parse_route_response_partial("A", json.dumps({"records": [record]}))
    assert not parsed["rejections"]
    result = evaluate_route("A", parsed, index, task().model_dump(mode="json"))
    assert any(issue["code"] == "relation_geometry_mismatch" and issue["field"] == "entity"
               for issue in result["issues"])
    assert result["candidates"][0]["original_records"] == [before]
    assert record == before

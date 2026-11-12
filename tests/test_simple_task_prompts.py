"""Simple task examples guide both routes without becoming document evidence."""

from copy import deepcopy
import hashlib
import json

import pytest

from attestextract import extract
from attestextract.backends import ReplayBackend
from attestextract.models import DocumentChunk, SourceSpan, TaskSpec
from attestextract.prompting import build_messages, build_output_schema
from attestextract.table_routes import build_route_request, build_table_input, evaluate_route
from attestextract.task_input import resolve_task


EXAMPLE_SOURCE = "Sample Z at 20 C has conductivity 12 mS."
EXAMPLES = [{"text": EXAMPLE_SOURCE, "extractions": [{
    "field": "conductivity", "entity": "Sample Z", "value": "12", "unit": "mS",
    "conditions": "20 C", "quote": EXAMPLE_SOURCE,
}]}]
LEGACY_TASK = {
    "name": "legacy", "description": "Keep source",
    "record_types": {"measurement": {"fields": {
        "value": {"type": "string", "required": True}, "unit": {"type": "string"},
    }}},
}


def simple_task(examples=EXAMPLES):
    return resolve_task(prompt_description="Keep stated measurements and qualitative findings.",
                        field_list={"conductivity": "Electrical conductivity",
                                    "appearance": "Reported appearance"},
                        examples=deepcopy(examples))


def chunk(text="Actual sample A has no reported measurements."):
    return DocumentChunk(chunk_id="chunk-0001", text=text,
                         segments=[SourceSpan(start=0, end=len(text))])


def message_digest(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize("protocol,expected", [
    ("flat-v1", "129e3dd9f281bc077250349099f108f8b3aeb09c5eeadad7f47ae0369b34b4c2"),
    ("native-v1", "112cfa015b868e2f6696da0ea3479dc938e4c45df2cea9fd2be90ce21e83c89a"),
])
def test_legacy_prompt_bytes_match_dev28(protocol, expected):
    # Captured from the unchanged dev28 builder before adding this input path.
    messages = build_messages(TaskSpec.model_validate(LEGACY_TASK),
                              chunk("原文🙂  12 m\\_{a}"), output_protocol=protocol)
    assert message_digest(messages) == expected


def test_legacy_table_prompt_bytes_match_dev28():
    request = build_route_request("A", LEGACY_TASK, {"marker": "actual table"})
    assert message_digest(request.messages) == "7555f57749f8e86e37d104c150a05815cc4b7bc21dff6059c6b37ec3715a3354"


@pytest.mark.parametrize("protocol", ["flat-v1", "native-v1"])
def test_examples_use_the_selected_prose_contract_without_limiting_targets(protocol):
    task = simple_task()
    messages = build_messages(task, chunk(), output_protocol=protocol)
    payload = json.loads(messages[1]["content"])
    assert payload["source"] == chunk().text
    assert EXAMPLE_SOURCE not in messages[1]["content"]
    assert "input_materials" not in payload["task"]
    assert set(payload["task"]["record_types"]) == {"conductivity", "appearance"}
    instructions = messages[0]["content"]
    assert "USER-LABELLED TASK EXAMPLES" in instructions
    assert "SYNTHETIC FORMAT GUIDANCE ONLY" not in instructions
    assert "example101" not in instructions
    assert "never calculate a mean" in instructions
    values = {"entity": "Sample Z", "value": "12", "unit": "mS", "conditions": "20 C"}
    quotes = {name: EXAMPLE_SOURCE for name in values}
    record = {"record_type": "conductivity"}
    if protocol == "flat-v1":
        record.update(values=values, quotes=quotes)
    else:
        record["fields"] = {name: {"value": value, "quote": quotes[name]} for name, value in values.items()}
    guidance = [{"source": EXAMPLE_SOURCE, "response": {"records": [record]}}]
    assert json.dumps(guidance, ensure_ascii=False) in instructions
    schema = build_output_schema(task, output_protocol=protocol)
    branches = schema["properties"]["records"]["items"]["anyOf"]
    assert {branch["properties"]["record_type"]["enum"][0] for branch in branches} == {"conductivity", "appearance"}


def test_simple_task_without_examples_keeps_automatic_format_guidance():
    instructions = build_messages(simple_task([]), chunk())[0]["content"]
    assert "SYNTHETIC FORMAT GUIDANCE ONLY" in instructions
    assert "return no observation for it rather than an empty placeholder" in instructions


def test_separate_quotes_and_unreported_optional_fields_are_preserved():
    examples = [{"text": "Sample Z was tested. Conductivity 12 mS.", "extractions": [{
        "field": "conductivity", "entity": "Sample Z", "value": "12", "unit": "mS",
        "quote": "Conductivity 12 mS.", "quotes": {"entity": "Sample Z was tested."},
    }]}]
    text = build_messages(simple_task(examples), chunk())[0]["content"]
    expected = {"entity": "Sample Z was tested.", "value": "Conductivity 12 mS.",
                "unit": "Conductivity 12 mS.", "conditions": ""}
    assert json.dumps(expected) in text
    assert '"conditions": null' in text


def test_table_examples_are_semantic_guidance_not_flat_output_or_table_evidence():
    task = simple_task()
    table = {"marker": "actual table"}
    request = build_route_request("A", task.model_dump(mode="json"), table)
    payload = json.loads(request.messages[1]["content"])
    assert payload["table"] == table
    assert "input_materials" not in payload["task"]
    assert set(payload["task"]["record_types"]) == {"conductivity", "appearance"}
    assert len(payload["task_examples"]) == 1
    assert payload["task_examples"][0]["text"] == EXAMPLE_SOURCE
    assert "anchors" not in json.dumps(payload["task_examples"])
    assert "scope" not in json.dumps(payload["task_examples"])
    assert "task_examples" not in request.output_schema["properties"]
    assert request.output_schema["$defs"]["TableRecord"]["properties"]["anchors"]
    assert "TASK SEMANTIC GUIDANCE ONLY" in request.messages[0]["content"]


def test_copying_example_facts_does_not_supply_evidence_in_actual_document():
    values = {"entity": "Sample Z", "value": "12", "unit": "mS", "conditions": "20 C"}
    response = {"records": [{"record_type": "conductivity", "values": values,
                             "quotes": {name: EXAMPLE_SOURCE for name in values}}]}
    result = extract(chunk().text, simple_task(), backend=ReplayBackend([response]))
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_review
    assert candidate.fields["value"].evidence.status == "unmatched"


@pytest.mark.parametrize("value", [None, "", " \t\n"])
@pytest.mark.parametrize("has_sources", [False, True])
def test_required_table_values_mark_missing_without_changing_original(value, has_sources):
    task = simple_task([])
    table = build_table_input("<table><tr><td>Sample A</td><td>12</td></tr></table>")
    entry = next(entry for entry in table["entries"] if entry["quote"] == "12")
    selection = {"ref": entry["ref"], "quote": "12", "offset": None}
    fields = {name: {"value": None, "sources": [], "relation": None}
              for name in ("entity", "value", "unit", "conditions")}
    fields["value"] = {"value": value, "sources": [selection] if has_sources else [],
                       "relation": "anchor" if has_sources or value is not None else None}
    original = {"record_id": "r1", "record_type": "conductivity", "fields": fields,
                "anchors": [selection], "scope": "individual", "scope_sources": []}
    response = {"records": [original]}
    evaluation = evaluate_route("A", response, table, task.model_dump(mode="json"))
    candidate = evaluation["candidates"][0]
    assert candidate["values"]["value"] == value
    assert candidate["original_records"] == [original]
    assert response == {"records": [original]}
    assert evaluation["status"] == "issues_found"
    assert any(issue["code"] == "required_value_missing" and issue["field"] == "value"
               for issue in candidate["issues"])
    assert candidate["field_checks"]["value"]["literal_check"] != "passed"

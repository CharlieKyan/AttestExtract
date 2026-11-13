"""Synthetic table ownership, shared-context and response-boundary checks."""

from copy import deepcopy
import json

import pytest

from attestextract.document_routing import is_table_chunk, table_input_for_chunk
from attestextract.errors import ConfigurationError
from attestextract.models import ContextOptions, DocumentChunk, SourceSpan
from attestextract.structure import build_structure
from attestextract.table_routes import build_route_request, evaluate_route
from attestextract.table_windows import (
    add_table_window, filter_window_records, split_table_chunk, window_owned_refs,
)


TASK = {"name": "measurements", "description": "Extract reported measurements.",
        "record_types": {"measurement": {"fields": {"value": {"type": "string", "required": True}}}}}
SOURCE = ("# Synthetic samples\n\n"
          "<table><tr><th rowspan=2>Sample</th><th colspan=2>Measurement</th></tr>"
          "<tr><th>Before / kPa</th><th>After / kPa</th></tr>"
          "<tr><td rowspan=2>Group A</td><td>12</td><td>15</td></tr>"
          "<tr><td>22</td><td>25</td></tr>"
          "<tr><td>Group B</td><td>32</td><td>35</td></tr></table>\n")


def root_chunk(source):
    structure = build_structure(source)
    span = structure.tables[0].span
    root = DocumentChunk(chunk_id="table-0001", text=source[span.start:span.end],
                         segments=[span], body_segments=[span], context_policy=ContextOptions(mode="structure"))
    return structure, root


def value_record(ref, quote, rid="record"):
    selection = {"ref": ref, "quote": quote}
    return {"record_id": rid, "record_type": "measurement",
            "fields": {"value": {"value": quote, "sources": [selection], "relation": "anchor"}},
            "anchors": [selection], "scope": "individual", "scope_sources": []}


def test_split_keeps_original_refs_and_header_context_with_exact_disjoint_body_coverage():
    structure, root = root_chunk(SOURCE)
    parent_input = table_input_for_chunk(SOURCE, root, structure)
    children = split_table_chunk(SOURCE, root, structure=structure)
    assert [c.chunk_id for c in children] == ["table-0001.1", "table-0001.2"]
    assert children[0].body_segments[0].start == root.body_segments[0].start
    assert children[0].body_segments[-1].end == children[1].body_segments[0].start
    assert children[-1].body_segments[-1].end == root.body_segments[-1].end
    inputs = [table_input_for_chunk(SOURCE, c, structure) for c in children]
    owned = [window_owned_refs(item) for item in inputs]
    assert not owned[0] & owned[1]
    assert owned[0] | owned[1] == {e["ref"] for e in parent_input["entries"] if e["kind"] == "cell"}
    for item in inputs:
        assert item["table_source"] == parent_input["table_source"]
        assert item["entries"] == parent_input["entries"]
        merged = next(e for e in item["entries"] if e["quote"] == "Group A")
        assert merged["rowspan"] == 2
        for entry in item["entries"]:
            assert SOURCE[entry["start"]:entry["end"]] == entry["quote"]


def test_recursive_split_terminates_at_one_physical_cell_without_losing_any_refs():
    structure, root = root_chunk(SOURCE)
    queue, leaves = [root], []
    while queue:
        parent = queue.pop(0)
        children = split_table_chunk(SOURCE, parent, structure=structure)
        if children:
            queue.extend(children)
        else:
            leaves.append(parent)
    original = table_input_for_chunk(SOURCE, root, structure)
    expected = [e["ref"] for e in original["entries"] if e["kind"] == "cell"]
    actual = [ref for child in leaves for ref in window_owned_refs(table_input_for_chunk(SOURCE, child, structure))]
    assert sorted(actual) == sorted(expected)
    assert len(actual) == len(set(actual))
    assert len(leaves) == len(expected)


def test_wide_single_row_splits_cells_and_does_not_split_long_single_cell():
    source = "<table><tr><td>A</td><td>10</td><td>20</td><td>30</td></tr></table>"
    structure, root = root_chunk(source)
    children = split_table_chunk(source, root, structure=structure)
    assert len(children) == 2
    assert [len(window_owned_refs(table_input_for_chunk(source, child, structure))) for child in children] == [2, 2]
    one_cell = "<table><tr><td>" + "one observation " * 100 + "</td></tr></table>"
    structure, root = root_chunk(one_cell)
    assert split_table_chunk(one_cell, root, structure=structure) == []


def test_empty_cells_and_rowspan_cells_are_owned_once():
    source = "<table><tr><td rowspan=2>A</td><td></td></tr><tr><td>10</td></tr></table>"
    structure, root = root_chunk(source)
    inputs = [table_input_for_chunk(source, child, structure)
              for child in split_table_chunk(source, root, structure=structure)]
    owners = [ref for item in inputs for ref in window_owned_refs(item)]
    assert len(owners) == len(set(owners)) == len(structure.tables[0].cells)


def test_window_instructions_only_apply_to_children_and_keep_full_context():
    structure, root = root_chunk(SOURCE)
    original = table_input_for_chunk(SOURCE, root, structure)
    root_request = build_route_request("A", TASK, original)
    assert "window" not in original
    assert "SHARED CONTEXT" not in root_request.messages[0]["content"]
    child = split_table_chunk(SOURCE, root, structure=structure)[1]
    window = table_input_for_chunk(SOURCE, child, structure)
    request = build_route_request("A", TASK, window, chunk_id=child.chunk_id)
    assert "SHARED CONTEXT" in request.messages[0]["content"]
    payload = json.loads(request.messages[1]["content"])
    assert payload["table"]["entries"] == original["entries"]
    assert payload["table"]["window"]["chunk_id"] == child.chunk_id


def test_shared_context_anchor_is_quarantined_with_original_index_and_raw_record():
    structure, root = root_chunk(SOURCE)
    child = split_table_chunk(SOURCE, root, structure=structure)[1]
    item = table_input_for_chunk(SOURCE, child, structure)
    owned = window_owned_refs(item)
    local = next(e for e in item["entries"] if e["ref"] in owned)
    foreign = next(e for e in item["entries"] if e["kind"] == "cell" and e["ref"] not in owned)
    parsed = {"records": [value_record(local["ref"], local["quote"], "local"),
                          value_record(foreign["ref"], foreign["quote"], "foreign")],
              "record_indices": [2, 9], "rejections": []}
    before = deepcopy(parsed)
    filtered = filter_window_records(parsed, item)
    assert parsed == before
    assert filtered["record_indices"] == [2]
    assert filtered["rejections"][0]["index"] == 9
    assert filtered["rejections"][0]["raw"] == parsed["records"][1]
    assert filtered["rejections"][0]["errors"][0]["type"] == "anchor_outside_window"
    assert filter_window_records(filtered, item) == filtered
    evaluation = evaluate_route("A", parsed, item, TASK)
    assert len(evaluation["candidates"]) == 1
    assert evaluation["status"] == "partial_format_error"
    assert evaluation["parse_rejections"] == filtered["rejections"]


def test_shared_context_can_support_entity_without_becoming_an_observation_anchor():
    structure, root = root_chunk(SOURCE)
    child = split_table_chunk(SOURCE, root, structure=structure)[1]
    item = table_input_for_chunk(SOURCE, child, structure)
    local = next(e for e in item["entries"] if e["ref"] in window_owned_refs(item))
    shared = next(e for e in item["entries"] if e["quote"] == "Sample")
    record = value_record(local["ref"], local["quote"])
    record["fields"]["entity"] = {"value": "sample", "sources": [{"ref": shared["ref"], "quote": shared["quote"]}],
                                  "relation": "column"}
    assert filter_window_records({"records": [record]}, item)["records"] == [record]
    record["fields"]["value"]["sources"] = [{"ref": shared["ref"], "quote": shared["quote"]}]
    assert filter_window_records({"records": [record]}, item)["records"] == []


@pytest.mark.parametrize("change", [
    lambda w: w.update(revision="unknown"),
    lambda w: w.update(chunk_id="table-0001.3"),
    lambda w: w.update(chunk_id="table-9999.1"),
    lambda w: w.update(start=True),
    lambda w: w.update(owned_cell_refs=w["owned_cell_refs"][:-1]),
    lambda w: w.update(owned_cell_refs=w["owned_cell_refs"] + [w["owned_cell_refs"][0]]),
    lambda w: w.update(extra="unexpected"),
])
def test_tampered_window_metadata_is_rejected_before_request(change):
    structure, root = root_chunk(SOURCE)
    child = split_table_chunk(SOURCE, root, structure=structure)[1]
    item = table_input_for_chunk(SOURCE, child, structure)
    change(item["window"])
    with pytest.raises(ConfigurationError, match="window"):
        build_route_request("A", TASK, item)


def test_child_cutting_cell_is_rejected_and_unknown_suffix_is_not_a_table():
    structure, root = root_chunk(SOURCE)
    item = table_input_for_chunk(SOURCE, root, structure)
    entry = next(e for e in item["entries"] if e["quote"] == "Group A")
    span = SourceSpan(start=entry["start"] + 1, end=root.body_segments[0].end)
    corrupt = DocumentChunk(chunk_id="table-0001.1", text=SOURCE[span.start:span.end],
                            segments=[span], body_segments=[span])
    with pytest.raises(ConfigurationError, match="physical cell"):
        add_table_window(item, corrupt)
    assert not is_table_chunk(corrupt.model_copy(update={"chunk_id": "table-0001.9"}))

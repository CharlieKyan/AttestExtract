"""A completed, truncated generation has a different accounting and recovery path from an unknown dispatch."""

import json

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.entity_first import run_entity_first
from attestextract.models import BackendResponse


DOCUMENT = (
    "| Subjects | Rack A and Rack B |\n| --- | --- |\n| Shared target | amber |\n"
    "| Property two | blue |\n| Property three | pear |\n"
    "\n# Independent\n\nBench M had a red finish."
)


@pytest.mark.parametrize("limit,expected_children", [(12, 2), (3, 0), (4, 1)])
def test_http_output_limit_is_counted_and_other_blocks_go_first(tmp_path, limit, expected_children):
    trace = RunTrace(tmp_path / "trace")
    packets = []

    def serve(request):
        payload = json.loads(request.content)
        packet = json.loads(payload["messages"][1]["content"])
        packets.append(packet)
        block = packet["windows"][0]
        truncate = packet["stage"] == "attributes" and block["kind"] == "table" and ".r" not in block["id"]
        answer = {"objects": []} if packet["stage"] == "inventory" else {"nominations": [], "facts": []}
        return httpx.Response(200, json={
            "model": "fixed",
            "usage": {"prompt_tokens": 3, "completion_tokens": 97 if truncate else 2,
                      "total_tokens": 100 if truncate else 5},
            "choices": [{"finish_reason": "length" if truncate else "stop",
                         "message": {"content": '{"facts": [' if truncate else json.dumps(answer)}}],
        })

    backend = OpenAICompatibleBackend("https://model.example/v1", "fixed", max_attempts=1,
                                      transport=httpx.MockTransport(serve), trace=trace)
    report = run_entity_first(DOCUMENT, {"color": "Reported color"}, "Named benches", backend,
                              max_calls=limit, format_retries=1)
    parent, independent = report["blocks"]
    assert independent["attributes"] == "read"
    assert [p["windows"][0]["id"] for p in packets[1:3]] == [parent["block_id"], independent["block_id"]]
    note = report["calls"][1]
    assert note["state"] == "truncated_response" and note["finish_reason"] == "length"
    assert note["usage"]["total_tokens"] == 100
    assert (trace.directory / note["response_artifact"]).read_text() == '{"facts": ['
    assert report["budget"]["response_count"] == report["budget"]["calls_used"] == len(packets)
    assert report["budget"]["usage"]["total_tokens"] == 100 + 5 * (len(packets) - 1)
    assert parent["initial_attributes"] == "truncated_response"
    assert sum(c["attributes"] == "read" for c in parent["recovery"]) == expected_children
    assert report["status"] == ("complete" if expected_children == 2 else "partial")
    assert all(c["attributes"] in {"read", "budget_exhausted"} for c in parent["recovery"])
    assert not report["observations"]
    if expected_children == 2:
        first, second = (p["windows"] for p in packets[3:])
        assert first[0]["text"] + second[0]["text"] == DOCUMENT[parent["start"]:parent["end"]]
        assert first[0]["start"] + len(first[0]["text"]) == second[0]["start"]
        assert "Rack A and Rack B" in second[0]["text"] or any(
            "Rack A and Rack B" in w["text"] and w["role"] == "recovery_context" for w in second[1:])


def test_repeated_truncation_on_indivisible_passage_leaves_a_single_unresolved_region():
    class Limited:
        def generate(self, request):
            packet = json.loads(request.messages[1]["content"])
            if packet["stage"] == "inventory":
                return BackendResponse(text='{"objects":[]}')
            return BackendResponse(text="broken fixture-token", finish_reason="length", usage={"total_tokens": 42})

    report = run_entity_first("A sample", {"color": "Reported color"}, "named samples", Limited(),
                              max_calls=10, secrets=["fixture-token"])
    parent, = report["blocks"]
    child, = parent["recovery"]
    assert parent["attributes"] == child["attributes"] == "truncated_response"
    assert child["start"] == parent["start"] and child["end"] == parent["end"]
    assert report["status"] == "partial" and report["budget"]["calls_used"] == 3
    assert report["budget"]["usage"]["total_tokens"] == 84
    assert report["calls"][1]["partial_text"] == "broken [REDACTED]"


def test_truncated_inventory_batch_does_not_reset_stage_reservation():
    events = []

    class Limited:
        def generate(self, request):
            packet = json.loads(request.messages[1]["content"])
            events.append(packet["stage"])
            if len(events) == 1:
                return BackendResponse(text="cut", finish_reason="length")
            reply = {"objects": []} if packet["stage"] == "inventory" else {"nominations": [], "facts": []}
            return BackendResponse(text=json.dumps(reply))

    report = run_entity_first(DOCUMENT, {"color": "Reported color"}, "individual samples", Limited(), max_calls=6)
    assert events == ["inventory"] * 3 + ["attributes"] * 2
    assert all(b["inventory"] == "read" and b["initial_inventory"] == "truncated_response" for b in report["blocks"])
    assert report["status"] == "complete"


def test_one_wide_data_row_splits_value_responsibility_while_showing_all_labels():
    from attestextract.entity_first_blocks import recovery_blocks, source_blocks
    from attestextract.structure import build_structure

    src = "| Lot | Alpha | Beta | Gamma | Delta |\n|---|---|---|---|---|\n| trial | 81 | 62 | 53 | 34 |"
    parent, = source_blocks(src)
    table, = build_structure(src).tables
    left, right = recovery_blocks(src, parent, table)
    assert "81" in left["text"] and "34" in right["text"]
    assert left["end"] == right["start"]
    assert all(not (cell.span.start < left["end"] < cell.span.end) for cell in table.cells)
    assert any("trial" in w["text"] and "Delta" in w["text"] for w in right["context"])


def test_optional_inventory_reread_preserves_last_primary_attribute_calls():
    stages = []

    class Limited:
        def generate(self, request):
            packet = json.loads(request.messages[1]["content"])
            stages.append(packet["stage"])
            if packet["stage"] == "inventory":
                return BackendResponse(text="cut", finish_reason="length")
            return BackendResponse(text='{"nominations":[],"facts":[]}')

    source = "\n\n".join(f"# Test {i}\n\nUnit {i} was sampled." for i in range(4))
    report = run_entity_first(source, {"color": "Reported color"}, "individual units", Limited(), max_calls=5)
    assert stages == ["inventory"] + ["attributes"] * 4
    assert all(b["attributes"] == "read" and b["inventory"] == "budget_exhausted" for b in report["blocks"])

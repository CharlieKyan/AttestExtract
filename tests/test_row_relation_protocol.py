import json

import pytest

from niceextract import ExecutionOptions, extract
from niceextract.backends import ReplayBackend
from niceextract.task_input import compile_task
from niceextract.dataset_snapshot import checked_snapshot
from niceextract.errors import ConfigurationError, InvalidResponseError
from niceextract.prompting import build_output_schema
from niceextract.table_routes import build_table_input, build_route_request, evaluate_route, parse_route_response_partial


SOURCE = "Coupon R9 cut from panel B showed 12 percent strain."


def task():
    return compile_task(prompt_description="Report measurements for each panel.",
                        field_list={"strain": "Strain of the panel or its coupons."}).model_copy(
                            update={"row_subject": "Named panels"})


def record(quote, ref=None):
    return {"record_type": "strain", "name_handling": "related",
            "values": {"value": "12", "entity": "Coupon R9", "conditions": None, "unit": "percent"},
            "quotes": {"value": SOURCE, "entity": SOURCE, "conditions": "", "unit": SOURCE},
            "row_relation": {"target": "panel B", "predicate": "sample_of", "field_applicability": "included",
                             "evidence": [{"quote": quote, "ref": ref}]}}


@pytest.mark.parametrize("citation, status", [(SOURCE, "located"), ("Coupon from panel A", "unresolved")])
@pytest.mark.parametrize("protocol", ["flat-v1", "native-v1"])
def test_opt_in_prose_relation_preserves_direct_owner_and_grounded_or_unresolved_receipt(citation, status, protocol):
    item = record(citation)
    if protocol == "native-v1":
        item["fields"] = {k: {"value": v, "quote": item["quotes"][k]} for k, v in item["values"].items()}
        del item["values"], item["quotes"]
    result = extract(SOURCE, task(), row_relation_evidence=True, table_mode="text",
                     backend=ReplayBackend([{"records": [item]}], output_protocol=protocol))
    assert result.task.row_relation_protocol == "evidence-v1"
    snapshot, _ = checked_snapshot(SOURCE, result.model_dump(mode="json"), None)
    obs, = snapshot["observations"]
    assert (obs["entity"], obs["value"]) == ("Coupon R9", "12")
    receipt, = obs["row_relations"]
    assert (receipt["target"], receipt["predicate"], receipt["field_applicability"], receipt["status"]) == (
        "panel B", "sample_of", "included", status)
    start = 0 if status == "located" else None
    end = len(SOURCE) if status == "located" else None
    assert receipt["evidence"] == [{"ref": None, "quote": citation, "start": start, "end": end}]


def test_legacy_schema_and_parser_do_not_request_or_use_experimental_metadata():
    assert "row_relation" not in json.dumps(build_output_schema(task()))
    assert "row_relation_protocol" not in task().model_dump()
    with pytest.raises(InvalidResponseError, match="row_relation"):
        extract(SOURCE, task(), backend=ReplayBackend([{"records": [record(SOURCE)]}], output_protocol="flat-v1"))


def test_experiment_rejects_resuming_from_the_default_prompt_cache(tmp_path):
    item = record(SOURCE)
    del item["row_relation"]
    source_run = tmp_path / "ordinary"
    extract(SOURCE, task(), backend=ReplayBackend([{"records": [item]}], output_protocol="flat-v1"),
            backend_identity="synthetic", output_dir=source_run)
    with pytest.raises(ConfigurationError, match="fingerprint|semantic|task|plan"):
        extract(SOURCE, task(), backend=ReplayBackend([], output_protocol="flat-v1"),
                backend_identity="synthetic", output_dir=tmp_path / "experiment",
                resume_from=source_run, row_relation_evidence=True)


def test_explicit_alternatives_share_one_observation_and_remain_tentative_after_replay():
    source = "Coupon R9, cut from either panel B or panel C, showed 12 percent strain."
    item = record(source)
    item["quotes"] = {k: source if quote else "" for k, quote in item["quotes"].items()}
    item["name_handling"] = "uncertain"
    choices = [dict(item["row_relation"], target=name) for name in ("panel B", "panel C")]
    item["row_relation"] = {"alternatives": choices}
    backend = ReplayBackend([{"records": [item]}], output_protocol="flat-v1")
    result = extract(source, task(), row_relation_evidence=True, backend=backend)
    snapshot, _ = checked_snapshot(source, result.model_dump(mode="json"), None)
    obs, = snapshot["observations"]
    assert obs["entity"] == "Coupon R9" and obs["value"] == "12"
    assert [(r["target"], r["alternative"], r["status"]) for r in obs["row_relations"]] == [
        ("panel B", True, "located"), ("panel C", True, "located")]


def test_table_receipt_is_located_in_the_selected_cell_and_preserved_through_parsing(tmp_path):
    source = "| Lot | Source | Strain |\n|---|---|---|\n| R9 | panel B | 12 |"
    table = build_table_input(source)
    cells = {e["quote"].strip(): e for e in table["entries"]}
    spec = task().model_copy(update={"row_relation_protocol": "evidence-v1"})
    request = build_route_request("A", spec.model_dump(), table)
    assert "row_relation" in request.output_schema["$defs"]["TableRecord"]["required"]
    def selection(s):
        return {"ref": cells[s]["ref"], "quote": s}
    relation = record("panel B", cells["panel B"]["ref"])["row_relation"]
    item = {"record_id": "1", "record_type": "strain", "scope": "individual", "scope_sources": [],
            "anchors": [selection("12")], "name_handling": "related", "row_relation": relation,
            "fields": {"entity": {"value": "R9", "relation": "row", "sources": [selection("R9")]},
                       "value": {"value": "12", "relation": "anchor", "sources": [selection("12")]}}}
    for ref, expected in [(cells["panel B"]["ref"], "located"), (cells["R9"]["ref"], "unresolved")]:
        relation["evidence"][0]["ref"] = ref
        payload = json.dumps({"records": [item]})
        assert len(parse_route_response_partial("A", payload)["rejections"]) == 1
        parsed = parse_route_response_partial("A", payload, row_relations=True)
        candidate, = evaluate_route("A", parsed, table, spec.model_dump())["candidates"]
        receipt, = candidate["row_relations"]
        assert receipt["status"] == expected
        assert receipt["evidence"][0]["ref"] == ref
    relation["evidence"][0]["ref"] = cells["panel B"]["ref"]
    backend = ReplayBackend([{"records": [item]}], output_protocol="flat-v1")
    result = extract(source, task(), row_relation_evidence=True, backend=backend).model_dump(mode="json")
    snapshot, _ = checked_snapshot(source, result, None)
    observation, = snapshot["observations"]
    assert observation["row_relations"][0]["status"] == "located"
    result["table_results"][0]["candidates"][0]["row_relations"][0]["evidence"][0]["ref"] = cells["R9"]["ref"]
    with pytest.raises(ConfigurationError, match="cited table cells"):
        checked_snapshot(source, result, None)

    # Simulate a missing numeric cell. Reread and cache replay use the same
    # record-level protocol even when just one value cell is requested.
    reader = ReplayBackend([{"records": []}, {"records": [item]}], output_protocol="flat-v1")
    settings = dict(task=task(), row_relation_evidence=True, execution=ExecutionOptions(max_model_calls=2),
                    backend_identity="table-relation-replay", progress="quiet")
    repaired = extract(source, backend=reader, output_dir=tmp_path / "first", **settings)
    first, second = repaired.table_results
    assert first.evidence["coverage_reread"]["remaining"] == {}
    assert second.chunk_id.endswith(".coverage")
    assert second.candidates[0]["row_relations"][0]["status"] == "located"
    resumed = extract(source, backend=ReplayBackend([], output_protocol="flat-v1"),
                      output_dir=tmp_path / "resumed", resume_from=tmp_path / "first", **settings)
    assert resumed.table_results == repaired.table_results

"""Reader CSV and lookup retain direct owners, filing scope and original evidence."""

import csv
import json

import pytest

from niceextract import assemble_dataset, extract, query_dataset, save_dataset
from niceextract.models import BackendResponse
from test_row_relation_delivery import link
from test_source_statement_protocol import FixtureBackend


def reading(entity, target, quote, value_quote, applicability, predicate):
    return {"record_type": "amplitude", "values": {"value": "22", "unit": "mV",
                "entity": entity, "conditions": None},
            "quotes": {"entity": quote, "value": value_quote, "unit": value_quote, "conditions": ""},
            "statement_basis": "reported", "reported_use": "measured", "source_property": "amplitude",
            "name_handling": "related", "row_relation": link(target, quote, applicability, predicate)}


def export(tmp_path, source, records, subjects):
    result = extract(source, prompt_description="Keep each measurement and its direct owner's identity.",
        field_list={"name": subjects, "amplitude": "Amplitude of this object or its associated sensing element."},
        backend=FixtureBackend({"records": records}), dataset_options={"row_subject": subjects},
        table_mode="text", observation_protocol="source-v1")
    assert result.task.statement_protocol == "source-v1"
    dataset = assemble_dataset(source, result.model_dump(mode="json"), subject_label_field="name")
    directory = save_dataset(dataset, tmp_path / "reader")
    saved = json.loads((directory / "dataset.json").read_bytes())
    with (directory / "main.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return saved, rows


@pytest.mark.parametrize("use,marker", [("included", None), ("excluded", "字段不适用"),
                                         ("uncertain", "适用范围待定")])
def test_equipment_component_value_and_scope_remain_visible_in_csv_and_both_queries(tmp_path, use, marker):
    relation = "Pickup P4 belongs to recorder T9."
    value = "Pickup P4 recorded amplitude 22 mV."
    record = reading("Pickup P4", "recorder T9", relation, value, use, "part_of")
    data, rows = export(tmp_path, relation + " " + value, [record], "Named recorders")
    obs, = query_dataset(data, "recorder T9", association_only=True, citation_status="located")
    assert query_dataset(data, "Pickup P4") == [obs]
    assert (obs["entity"], obs["value"], obs["measurement_use"], obs["statement_basis"]) == (
        "Pickup P4", "22", "measured", "reported")
    row, = rows
    assert (row["name"], row["直接对象"], row["amplitude"], row["amplitude_引文状态"]) == (
        "recorder T9", "Pickup P4", "22", "已定位")
    assert obs["citation_evidence"][0]["quote"] == value
    if marker:
        assert marker in row["记录状态"] and obs["delivery"] == "provisional"
        assert not query_dataset(data, include_pending=False)


@pytest.mark.parametrize("same_local_name", [False, True])
def test_station_sample_corrects_only_a_distinguishable_source_with_same_digits(tmp_path, same_local_name):
    first, second = "Vial J", "Vial J" if same_local_name else "Vial K"
    relation = f"{first} was taken at station Moor."
    other = f"{second} was taken at station Pine."
    measurement = f"{first} at station Moor recorded amplitude 22 mV."
    rival = f"{second} at station Pine recorded amplitude 22 mV."
    wrong = "The front sheet contains an index."
    source = " ".join((relation, measurement, other, rival, wrong))
    records = [reading(first, "station Moor", relation, wrong, "included", "sample_of"),
               reading(second, "station Pine", other, rival, "included", "sample_of")]
    data, rows = export(tmp_path, source, records, "Named stations")
    obs, = query_dataset(data, "station Moor", association_only=True)
    by_station = {row["name"]: row for row in rows}
    assert by_station["station Moor"]["直接对象"] == first
    assert by_station["station Pine"]["直接对象"] == second
    assert any(s["quote"] == wrong for s in obs["source_evidence"])
    if same_local_name:
        assert obs["citation_status"] == "unverified"
        assert by_station["station Moor"]["amplitude_引文状态"] == "引文未核实"
        assert not query_dataset(data, "station Moor", citation_status="located")
    else:
        assert obs["citation_reason"] == "引文已找回"
        assert by_station["station Moor"]["amplitude_引文状态"] == "已定位"
        proof, = obs["citation_evidence"]
        assert source[proof["start"]:proof["end"]].strip() == measurement
    other_obs, = query_dataset(data, "station Pine", citation_status="located")
    assert other_obs["observation_id"] != obs["observation_id"]


def test_a_fresh_verifier_can_read_both_ambiguous_citations_in_its_existing_single_call():
    bad = "Pulse Q was recorded at opening."
    opening = "Pulse Q at opening had amplitude 22 mV."
    closing = "Pulse Q at closing had amplitude 22 mV."
    source = "\n\n".join((bad, "Additional setup records.", opening, closing))
    record = {"record_type": "amplitude", "values": {"entity": "Pulse Q", "value": "22",
                  "unit": "mV", "conditions": "at opening"},
              "quotes": dict.fromkeys(("entity", "value", "unit", "conditions"), bad),
              "statement_basis": "reported", "reported_use": "measured", "source_property": "amplitude",
              "name_handling": "as_reported", "row_relation": None}

    class Verifier(FixtureBackend):
        def generate(self, request):
            if not request.chunk_id.startswith("verify-"):
                return super().generate(request)
            self.requests.append(request)
            payload = json.loads(request.messages[-1]["content"])
            obs, = payload["observations"]
            windows = payload["citation_lookup"]["windows"]
            assert payload["citation_lookup"]["candidate_counts"][obs["observation_id"]] == 2
            assert all(source[w["start"]:w["end"]] == w["text"] for w in windows)
            correct, = [w for w in windows if opening in w["text"]]
            assert any(closing in w["text"] for w in windows)
            cite = {"passage_id": correct["passage_id"], "quote": opening}
            result = {"action": "decide", "question": "Which phase is reported?",
                      "objects": [{"object_id": "q", "name": "Pulse Q", "kind": "instance", "scope": "at opening",
                                   "is_table_subject": True, "mentions": [cite]}],
                      "bindings": [{"observation_id": obs["observation_id"], "subject_id": "q",
                                    "status": "supported", "target_match": "supported", "evidence": [cite],
                                    "property_name": "amplitude", "reason": "The opening phase is explicitly identified."}]}
            return BackendResponse(text=json.dumps(result))

    backend = Verifier({"records": [record]})
    result = extract(source, prompt_description="Keep pulse readings with their reported phases.",
        field_list={"amplitude": "Pulse amplitude"}, backend=backend, backend_identity="window-judge-v1",
        table_subject="Named pulses",
        dataset_options={"row_subject": "Named pulses"}, max_analysis_calls=1, table_mode="text")
    assert len(backend.requests) == 2 and result.analysis["budget"]["reserved"] == 1
    observation, = result.analysis["observations"]
    quotes = {e["evidence_id"]: e["quote"] for e in result.analysis["evidence"]}
    assert observation["binding_status"] == "supported"
    assert [quotes[key] for key in observation["binding_evidence_ids"]] == [opening]
    assert any(q["quote"] == bad for q in observation["source_evidence"])

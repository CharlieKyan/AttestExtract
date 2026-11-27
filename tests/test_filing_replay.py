"""The reader can reuse a checked assignment or recover an archived optional proposal."""

import json
from hashlib import sha256

import pytest

from niceextract.dataset_filing import FilingContext
from niceextract.filing_replay import replay_filing_issues
from test_explanation_filing import MEASUREMENT, filing, reading, view


@pytest.mark.parametrize("is_row, field_fit, expected", [
    (True, "supported", True), (False, "supported", False), (True, "unknown", False),
])
def test_reviewed_direct_row_requires_both_object_and_field_decision(is_row, field_fit, expected):
    obs, = view([reading()])["observations"]
    span = {"start": 0, "end": len(MEASUREMENT), "quote": MEASUREMENT, "evidence_id": "quote"}
    obj = {"object_id": "registered", "name": "Bearing B7", "is_table_subject": is_row,
           "mention_evidence_ids": ["quote"]}
    obs.update(binding_status="supported", subject_id="registered", binding_evidence_ids=["quote"],
               target_match=field_fit, name_handling="uncertain", filing_review_reason="Check the requested row.")
    snapshot = {"table_subject": "Components", "objects": [obj], "relations": [], "observations": [obs],
                "evidence": [span]}
    FilingContext(MEASUREMENT, snapshot, [len(MEASUREMENT)], statement_protocol="source-v2").apply(obs, True, {})
    assert bool(obs["filing_subjects"] and obs["filing_subjects"][0]["status"] == "supported") == expected
    if not is_row:
        assert obs["filing_pending_reason"] and not obs["associated_row"]


@pytest.mark.parametrize("scenario", ["accepted", "different_owner", "different_value", "different_condition",
                                     "different_quote", "indistinguishable", "format_retry", "recovered_both",
                                     "recovered_one_missing", "recovered_not_retained", "recovered_ambiguous"])
def test_frozen_string_citation_must_come_from_the_actual_response_record(tmp_path, scenario):
    from copy import deepcopy
    from niceextract.execution import _Node, _State, digest
    from niceextract.models import BackendResponse

    visible = "Bearing B7 belongs to motor M4."
    hidden = "A different motor M5 was inspected."
    source = visible + "\n\n" + hidden
    proposal = {**filing("motor M4", visible), "evidence": [visible, hidden]}
    answer = reading(quote=visible, relation=proposal)
    row = {k: answer[k] for k in ("record_type", "statement_basis", "reported_use", "source_property")}
    row.update({"fields": {k: {"value": v, "evidence": {"quote": answer["quotes"][k]}}
                           for k,v in answer["values"].items()},
           "row_relation_issues": [{"raw": proposal, "status": "malformed"}],
           "chunk_ids": ["prose-0001"]})
    result = {"task": {"statement_protocol": "source-v2"}, "source": {"sha256": sha256(source.encode()).hexdigest()},
              "candidates": [row]}
    for sub in ("chunks", "requests", "responses", "execution/cache"):
        (tmp_path / sub).mkdir(parents=True)
    span = {"start": 0, "end": len(visible)}
    def write(path, data):
        (tmp_path / path).write_text(json.dumps(data), encoding="utf-8")
    chunk = {"chunk_id": "prose-0001", "text": visible, "body_segments": [span], "segments": [span]}
    write("chunks/prose-0001.json", chunk)
    recovered_run = scenario.startswith("recovered_")
    retry = scenario == "format_retry" or recovered_run
    attempt = "prose-0001.format-2" if retry else "prose-0001"
    request = {"messages": [{"role": "user", "content": json.dumps({"source": [{"role": "body", "text": visible}]})}]}
    wrong = deepcopy(answer)
    if scenario == "different_owner":
        wrong["values"]["entity"] = "Bearing B8"
    elif scenario == "different_value":
        wrong["values"]["value"] = "61"
    elif scenario == "different_condition":
        wrong["values"]["conditions"] = "wet"
    elif scenario == "different_quote":
        wrong["quotes"]["value"] = "under a different test"
    if retry:
        write("requests/prose-0001.request.json", request)
        write("responses/prose-0001.txt", {"records": [answer]})
    accepted = {"records": [wrong, answer] if scenario == "indistinguishable" else [wrong]}
    write("requests/" + attempt + ".request.json", request)
    write("responses/" + attempt + ".txt", accepted)
    cached = BackendResponse(text=json.dumps(accepted)).model_dump(mode="json")
    migrations = []
    if recovered_run:
        from niceextract.format_recovery import KIND
        first = deepcopy(answer)
        if scenario != "recovered_ambiguous":
            first["values"]["conditions"] = "frozen"
        write("responses/prose-0001.txt", {"records": [first]})
        items = []
        for stem in ("prose-0001", "prose-0001.format-2"):
            response_bytes = (tmp_path/"responses"/(stem+".txt")).read_bytes()
            request_bytes = (tmp_path/"requests"/(stem+".request.json")).read_bytes()
            items.append({"response": BackendResponse(text=response_bytes.decode()).model_dump(mode="json"),
                          "request_sha256": sha256(request_bytes).hexdigest(), "response_sha256": sha256(response_bytes).hexdigest()})
        origins = [[{"attempt": 1, "record_index": 0}]] if scenario == "recovered_not_retained" else [
            [{"attempt": i, "record_index": 0}] for i in (1, 2)]
        bundle = {"source_diagnostics": str(tmp_path.resolve()), "source_chunk": chunk, "chunk_id": "prose-0001",
                  "attempts": items, "record_origins": origins}
        (tmp_path/"execution"/"format-recovery").mkdir()
        write("execution/format-recovery/prose-0001.json", bundle)
        migrations = [{"chunk_id": "prose-0001", "kind": KIND, "bundle_sha256": digest(bundle)}]
        cached = items[0]["response"]
        if scenario == "recovered_one_missing":
            (tmp_path/"responses"/"prose-0001.txt").unlink()
    write("execution/cache/prose-0001.json", cached)
    node = _Node(segments=[span], body_segments=[span], depth=0, status="completed", calls=1+retry,
                 format_retries_reserved=int(retry), cache_hash=digest(cached))
    state = _State(fingerprint="test", roots=["prose-0001"], nodes={"prose-0001": node}, migrations=migrations,
                   max_model_calls=4, calls_used=1+retry).model_dump(mode="json")
    write("execution/checkpoint.json", {"state": state, "sha256": digest(state)})
    if scenario not in {"accepted", "format_retry", "recovered_both"}:
        assert replay_filing_issues(source, result, tmp_path) == {}
        return
    recovered = replay_filing_issues(source, result, tmp_path)["/candidates/0"]
    receipt, = recovered["receipts"]
    assert recovered["attempt"] == attempt and recovered["record_index"] == 0
    assert receipt["evidence"][0]["start"] == 0
    assert receipt["status"] == "unresolved" and receipt["evidence"][1]["start"] is None
    write("responses/" + attempt + ".txt", {"records": []})
    assert replay_filing_issues(source, result, tmp_path) == {}

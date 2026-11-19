from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
import threading

import pytest

from attestextract.dataset_supplement import discover_gaps, run_supplement
from attestextract.dataset_supplement_packs import review_packs
from attestextract.models import BackendResponse


SOURCE = "Sample Alpha from Batch Red measured 7 mg.\r\n\r\nStation γ measured 12 mg."


def quote(text, *, source=SOURCE):
    start = source.index(text)
    return {"start": start, "end": start + len(text), "quote": text}


def fixture():
    first = quote("Sample Alpha from Batch Red measured 7 mg.")
    obs = {"observation_id": "obs", "value": "7", "unit": "mg", "field": "mass", "entity": "Sample Alpha",
           "conditions": None, "purpose": "measured", "blockers": [],
           "source_evidence": [{"fields": ["value"], "span": {"start": 0, "end": first["end"]},
                                "quote": first["quote"]}],
           "direct": {"status": "unknown"}, "target": {"status": "unknown"}, "fit": {"status": "supported"},
           "candidates": [{"id": "a", "label": "Sample Alpha", "evidence": [quote("Sample Alpha")]},
                          {"id": "b", "label": "Batch Red", "evidence": [quote("Batch Red")]}]}
    data = {"document_id": "doc", "source_sha256": sha256(SOURCE.encode()).hexdigest(), "schema": ["mass"],
            "row_subject": "batch", "field_definitions": {"mass": "Measured mass"}, "observations": [obs]}
    data["coverage_gaps"] = discover_gaps(SOURCE, {}, data)
    return data


def test_contradicted_field_is_archived_without_an_ownership_reread():
    data = fixture()
    data["observations"][0]["fit"]["status"] = "contradicted"
    assert all(p["kind"] != "existing_observation" for p in review_packs(data, SOURCE))


def decisions(pack):
    if pack["kind"] == "missing_candidate":
        return [{"item_id": pack["item_id"], "axis": "missing", "status": "supported", "reason": "reported value",
                 "value": "12", "entity": "Station γ", "field": "mass", "unit": "mg",
                 "evidence": [quote("Station γ measured 12 mg.")]}]
    common = {"item_id": "obs", "status": "supported", "reason": "explicit measurement and source",
              "evidence": [quote("Sample Alpha from Batch Red measured 7 mg.")]}
    return [{**common, "axis": "target", "endpoint_id": "b", "predicate": "sample_of"},
            {**common, "axis": "direct", "endpoint_id": "a", "predicate": "direct"}]


class Backend:
    name = "synthetic"
    model = "test"

    def __init__(self, decide=decisions):
        self.calls = 0
        self.decide = decide

    def generate(self, request):
        self.calls += 1
        pack = json.loads(request.messages[-1]["content"])
        return BackendResponse(text=json.dumps({"decisions": self.decide(pack)}), usage={"output_tokens": 9})


def test_gaps_independent_of_existing_candidates_and_table_layout():
    data = fixture()
    assert [q["quote"] for gap in data["coverage_gaps"] for q in gap["uncovered"]] == ["12"]
    source = "<table><tr><td>text only</table>"
    gaps = discover_gaps(source, {"table_results": [{"table_id": "table-0001", "chunk_id": "segment",
                         "status": "format_error", "candidates": []}]}, {"observations": []})
    assert len(gaps) == 1
    assert gaps[0]["kind"] == "table_coverage"
    assert "unusable_cell_grid" in gaps[0]["diagnostics"]
    assert "segment:format_error:0_candidates" in gaps[0]["diagnostics"]


def test_restart_uses_cumulative_budget_and_cache(tmp_path):
    data = fixture()
    original = deepcopy(data)
    ledger = tmp_path / "budget.sqlite"
    adapter = Backend()
    zero = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=0)
    assert zero["attempts_used"] == 0 and len(zero["pending"]) == 2
    first = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1, backend=adapter)
    assert [d["endpoint_label"] for d in first["decisions"]] == ["Sample Alpha", "Batch Red"]
    again = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1, backend=adapter)
    assert first == again and adapter.calls == 1
    final = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=2, backend=adapter)
    assert final["attempts_used"] == 2 and final["usage"] == {"output_tokens": 18}
    gap = next(d for d in final["decisions"] if d["kind"] == "missing_candidate")
    assert (gap["field"], gap["value"], gap["entity"], gap["basis"]) == ("mass", "12", "Station γ", "semantic_model")
    expected = quote("Station γ measured 12 mg.")
    assert gap["scope"] == [{"start": expected["start"], "end": expected["end"]}]
    assert gap["scope_basis"] == "single_cited_statement"
    assert data == original and not final["pending"]
    data["field_definitions"]["mass"] = "Maximum mass only"
    edited = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=2, backend=adapter)
    assert edited["attempts_used"] == 2 and not edited["decisions"] and len(edited["pending"]) == 2
    with pytest.raises(ValueError, match="checksum"):
        run_supplement(data, SOURCE + "x", ledger_path=ledger, max_calls=0)
    data["document_id"] = "other"
    with pytest.raises(ValueError, match="different document"):
        run_supplement(data, SOURCE, ledger_path=ledger, max_calls=0)


@pytest.mark.parametrize("change, reason", [
    ("foreign_id", "endpoint_not_grounded_in_shown_mention"),
    ("unseen_quote", "citation_not_in_shown_original_source"),
    ("conflict", "conflicting_identity_evidence"),
    ("unresolved_direct", "direct_object_requires_resolution_first"),
    ("wrong_value", "value_not_grounded_in_this_observation"),
])
def test_evidence_failure_charges_attempt_and_retries_only_with_budget(tmp_path, change, reason):
    data = fixture()
    data["coverage_gaps"] = []
    if change == "conflict":
        data["lineage"] = {"relations": [{"source_id": "a", "target_id": "b", "predicate": "different_from"}]}

    def invalid(pack):
        target, direct = decisions(pack)
        if change == "foreign_id":
            direct["endpoint_id"] = "foreign"
        if change == "unseen_quote":
            direct["evidence"][0]["quote"] = "not in source"
        if change == "conflict":
            target["predicate"] = "same_as"
        if change == "unresolved_direct":
            direct = {"axis": "direct", "item_id": "obs", "status": "unknown", "reason": "ambiguous", "evidence": []}
        if change == "wrong_value":
            direct["evidence"] = [quote("Sample Alpha")]
        return [target, direct]

    ledger, backend = tmp_path / "audit.sqlite", Backend(invalid)
    report = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1, backend=backend)
    assert not report["decisions"] and report["failures"][0]["reason"] == reason
    backend.decide = decisions
    assert run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1, backend=backend)["pending"]
    fixed = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=2, backend=backend)
    assert len(fixed["decisions"]) == 2 and fixed["attempts_used"] == 2


def test_concurrent_reservations_and_unknown_transport_outcome(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def delay(pack):
        entered.set()
        release.wait(5)
        raise RuntimeError("private endpoint detail")

    data, ledger, backend = fixture(), tmp_path / "credits.sqlite", Backend(delay)
    opts = {"backend": backend, "ledger_path": ledger, "max_calls": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(run_supplement, data, SOURCE, **opts)
        assert entered.wait(5)
        during = run_supplement(data, SOURCE, **opts)
        assert during["attempts_used"] == backend.calls == 1
        release.set()
        report = future.result()
    assert report["failures"][0]["state"] == "uncertain"
    assert report["failures"][0]["reason"] == "RuntimeError"
    data["coverage_gaps"] = []
    assert run_supplement(data, SOURCE, **{**opts, "max_calls": 2})["attempts_used"] == 1


def test_explicit_backend_and_single_attempt_required(tmp_path):
    data, ledger = fixture(), tmp_path / "credits.sqlite"
    with pytest.raises(ValueError, match="positive supplement budget"):
        run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1)
    adapter = Backend()
    adapter.max_attempts = 2
    with pytest.raises(ValueError, match="internal retries"):
        run_supplement(data, SOURCE, ledger_path=ledger, max_calls=1, backend=adapter)
    assert adapter.calls == 0 and not ledger.exists()


def test_foreign_database_rejected_without_modifying_original(tmp_path):
    archive = tmp_path / "archive.sqlite"
    with sqlite3.connect(archive) as connection:
        connection.execute("CREATE TABLE original_records (item TEXT)")
    connection.close()
    original = archive.read_bytes()
    with pytest.raises(ValueError, match="not a supported"):
        run_supplement(fixture(), SOURCE, ledger_path=archive, max_calls=0)
    assert archive.read_bytes() == original


def test_credential_in_model_reason_is_never_written_to_ledger(tmp_path):
    marker = "synthetic-private-value"

    def with_secret(pack):
        answers = decisions(pack)
        answers[0]["reason"] = "provider diagnostic " + marker
        return answers

    ledger = tmp_path / "review.sqlite"
    result = run_supplement(fixture(), SOURCE, ledger_path=ledger, max_calls=1,
                            backend=Backend(with_secret), secrets=[marker])
    assert result["decisions"] == [] and result["failures"][0]["reason"] == "credential_in_review"
    assert marker not in json.dumps(result) and marker.encode() not in ledger.read_bytes()
    adapter = Backend()
    adapter._api_key = "Station γ"
    with pytest.raises(ValueError, match="configured credential"):
        run_supplement(fixture(), SOURCE, ledger_path=ledger, max_calls=2, backend=adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize("policy", [{"allowed_units": ["kg"]}, {"required_conditions": ["steady"]},
                                    {"allowed_statistics": ["mean"]}, {"allowed_purposes": ["classification"]}])
def test_missing_fact_requires_all_configured_field_policies(tmp_path, policy):
    data = fixture()
    data["observations"] = []
    data["field_policies"] = {"mass": policy}
    review = run_supplement(data, SOURCE, backend=Backend(), ledger_path=tmp_path / "review.sqlite", max_calls=1)
    assert not review["decisions"]
    assert review["failures"][0]["reason"] == "missing_fact_violates_field_policy"


@pytest.mark.parametrize("mode,err", [("explicit", None), ("omitted", "explicit_applicability_required"),
                                     ("disconnected", "scope_outside_observation")])
def test_distant_evidence_needs_source_backed_local_applicability(tmp_path, mode, err):
    data = fixture()
    data["coverage_gaps"] = []
    sentence = quote("Sample Alpha from Batch Red measured 7 mg.")
    other = quote("Station γ measured 12 mg.")

    def review(pack):
        answers = decisions(pack)
        for answer in answers:
            answer["evidence"] = [sentence, other]
            if mode != "omitted":
                answer["applicability"] = {"description": "Sample Alpha measurement",
                    "source": other if mode == "disconnected" else sentence}
        return answers

    result = run_supplement(data, SOURCE, backend=Backend(review), ledger_path=tmp_path / "review.sqlite", max_calls=1)
    if err:
        assert not result["decisions"] and result["failures"][0]["reason"] == err
    else:
        assert all(d["scope_basis"] == "model_scoped" for d in result["decisions"])
        assert result["decisions"][0]["applicability"]["source"] == sentence

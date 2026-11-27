"""Partial decisions preserve independent bindings and explicit rejected proposals."""

from copy import deepcopy
from io import StringIO
import json
import sqlite3

import pytest

from niceextract.analysis import is_review_only_partial
from niceextract.analysis_export import save_analysis
from niceextract.progress import ConsoleProgress
from test_analysis_export import review_sample, read_csv


def partial_sample():
    data = review_sample()
    data.update(analysis_version="0.4", source_format_status="complete")
    group = data["review_groups"][0]
    group.update(target_observation_ids=["o1", "o2", "o3", "o4"], applied_observation_ids=["o2", "o3", "o4"])
    review = {"proposal_type": "binding", "path": "bindings[0]", "proposal_id": "o1",
              "observation_ids": ["o1"], "dependencies": [], "code": "invalid_binding_status",
              "issues": [{"path": "bindings[0]", "code": "invalid_binding_status", "phase": "binding",
                          "message": "Supported ownership needs a subject."}],
              "raw_proposal": {"observation_id": "o1", "status": "supported", "subject_id": None}}
    group["validation_summary"] = {"category": "model_response", "phase": "proposal_validation", "code": "proposal_review",
                                  "proposal_count": 1, "observation_count": 1, "applied_count": 3}
    group["diagnostic"] = {**group["validation_summary"], "reviews": [review]}
    group["response_text"] = json.dumps({"action": "decide", "bindings": [review["raw_proposal"]]})
    data["jobs"] = [{"job_id": "verify-0001", "status": "review_required", "error_code": "proposal_review",
                     "observation_ids": group["target_observation_ids"], "review_observation_ids": ["o1"],
                     "attempts": 1, "steps": [group]}]
    return data


def mixed_partial_sample():
    data = partial_sample()
    direct = deepcopy(data["observations"][1])
    direct.update(observation_id="direct", binding_status="as_extracted", name_handling="as_reported",
                  subject_id=None, binding_evidence_ids=[], target_match="unknown", blockers=[])
    data["observations"].append(direct)
    return data


def test_review_only_counts_first_pass_owners_outside_verification_jobs(tmp_path):
    data = mixed_partial_sample()
    data["review_only"] = False  # Historical dev55 snapshot recorded this value.
    assert is_review_only_partial(data)
    save_analysis(data, tmp_path)
    report = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "观测共5条" in report and "沿用首轮归属1条，归属隔离待核1条" in report


@pytest.mark.parametrize("damage", [
    {"binding_status": "pending"}, {"name_handling": "uncertain"}, {"entity": " "},
    {"evidence_ids": []}, {"evidence_ids": ["missing"]}, {"subject_id": "L1"},
    {"binding_evidence_ids": ["e1"]}, {"blockers": [{"code": "ambiguous"}]},
    {"name_review": {"issue": "competing_owner"}},
])
def test_skipped_observation_must_qualify_for_direct_delivery(damage, tmp_path):
    data = mixed_partial_sample()
    data["observations"][-1].update(damage)
    data["review_only"] = True
    assert not is_review_only_partial(data)
    with pytest.raises(ValueError):
        save_analysis(data, tmp_path / "invalid")


@pytest.mark.parametrize(("raw", "origin"), [
    ({"source_kind": "mention", "source_id": "o2", "target_id": "unseen"}, ["o2"]),
    ({"source_kind": "mention", "source_id": "o1"}, ["o1"]),
    ({"source_kind": "mention", "source_id": "foreign"}, []),
    (None, []),
])
def test_single_entry_relation_review_keeps_original_and_independent_bindings(tmp_path, raw, origin):
    data = partial_sample()
    data["relation_entry_protocol"] = "single-entry-v1"
    group = data["review_groups"][0]
    review = {"proposal_type": "relation", "path": "relations[0]", "proposal_id": "relations[0]",
              "observation_ids": origin, "dependencies": [], "code": "invalid_relation",
              "issues": [{"path": "relations[0]", "phase": "relation", "code": "invalid_relation",
                          "message": "Relation cannot be applied."}], "raw_proposal": raw}
    group["diagnostic"]["reviews"].append(review)
    response = json.loads(group["response_text"])
    response["relations"] = [raw]
    group["response_text"] = json.dumps(response)
    for summary in (group["diagnostic"], group["validation_summary"]):
        summary["proposal_count"] = 2
    save_analysis(data, tmp_path / "valid")
    stored, = json.loads((tmp_path / "valid" / "review_groups.json").read_bytes())
    assert stored["diagnostic"]["reviews"][-1] == review
    assert stored["applied_observation_ids"] == ["o2", "o3", "o4"]
    review["observation_ids"] = ["o3"]
    with pytest.raises(ValueError, match="original slot and mention reference"):
        save_analysis(data, tmp_path / "damaged")


@pytest.mark.parametrize("fault", [None, "duplicate", "binding_collision", "wrong_target", "invalid_path", "out_of_range"])
def test_nested_name_rejections_can_coexist_with_other_held_bindings(tmp_path, fault):
    data = partial_sample()
    group = data["review_groups"][0]
    reviews = group["diagnostic"]["reviews"]
    for index, oid in [(1, "o2"), (2, "o3")]:
        path = f"bindings[{index}].name_resolution"
        name = {"relation": "local_identity", "target_id": "unshown", "reason": "Compared a local name."}
        reviews.append({"proposal_type": "name_resolution", "path": path, "proposal_id": oid,
                        "observation_ids": [oid], "dependencies": [], "code": "invalid_decision",
                        "issues": [{"path": path, "phase": "name_resolution", "code": "invalid_decision",
                                    "message": "The proposed endpoint was not shown."}], "raw_proposal": name})
    for d in (group["diagnostic"], group["validation_summary"]):
        d["proposal_count"] = len(reviews)
    if fault == "duplicate":
        reviews[-1]["path"] = reviews[1]["path"]
    elif fault == "binding_collision":
        reviews[-1]["path"] = "bindings[0].name_resolution"
    elif fault == "wrong_target":
        reviews[-1].update(proposal_id="o1", observation_ids=["o1"])
    elif fault == "invalid_path":
        reviews[-1]["path"] = "bindings[2].anything"
    elif fault == "out_of_range":
        reviews[-1]["path"] = "bindings[4].name_resolution"
    if fault:
        with pytest.raises(ValueError):
            save_analysis(data, tmp_path)
        return
    save_analysis(data, tmp_path)
    stored = json.loads((tmp_path / "review_groups.json").read_bytes())[0]
    assert stored["diagnostic"]["reviews"] == reviews
    assert stored["applied_observation_ids"] == ["o2", "o3", "o4"]


def test_single_entry_endpoint_markers_survive_json_csv_and_sqlite(tmp_path):
    data = partial_sample()
    data["relation_entry_protocol"] = "single-entry-v1"
    proof = data["evidence"][0]
    for key, quote in (("left", "Core S1"), ("right", "second layer")):
        start = proof["start"] + proof["quote"].index(quote)
        data["evidence"].append({"evidence_id": key, "quote": quote, "start": start, "end": start + len(quote)})
    data["relations"][0].update(source_mention_id="left", target_mention_id="right")
    save_analysis(data, tmp_path / "ok")
    csv_relation, = read_csv(tmp_path / "ok" / "relations.csv")
    json_relation, = json.loads((tmp_path / "ok" / "analysis.json").read_bytes())["relations"]
    for row in (csv_relation, json_relation):
        assert (row["source_mention_id"], row["target_mention_id"]) == ("left", "right")
    with sqlite3.connect(tmp_path / "ok" / "analysis.sqlite") as db:
        assert db.execute("SELECT source_mention_id, target_mention_id FROM relations").fetchall() == [("left", "right")]
    data["evidence"][-1].update(start=100, end=112)
    with pytest.raises(ValueError, match="endpoint evidence"):
        save_analysis(data, tmp_path / "damaged")


def test_partial_exports_retain_siblings_unknown_and_raw_rejection(tmp_path):
    data = partial_sample()
    original = deepcopy(data)
    assert is_review_only_partial(data)
    save_analysis(data, tmp_path)
    view = json.loads((tmp_path / "objects.json").read_text(encoding="utf-8"))
    assert [item["observation_id"] for item in view["objects"][0]["fields"]["porosity"]] == ["o2"]
    assert [item["observation_id"] for item in view["related_observations"]] == ["o3"]
    assert {item["observation_id"]: item["binding_status"] for item in view["unbound_observations"]} == {
        "o1": "review_required", "o4": "unknown"}
    assert len(read_csv(tmp_path / "observations.csv")) == 4
    row = read_csv(tmp_path / "review_groups.csv")[0]
    assert json.loads(row["target_observation_ids"]) == ["o1", "o2", "o3", "o4"]
    assert json.loads(row["applied_observation_ids"]) == ["o2", "o3", "o4"]
    held = json.loads((tmp_path / "review_groups.json").read_text(encoding="utf-8"))[0]
    assert held["diagnostic"]["reviews"][0]["raw_proposal"]["status"] == "supported"
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert db.execute("SELECT subject_id,binding_status FROM observations ORDER BY observation_id").fetchall() == [
            (None, "review_required"), ("L1", "supported"), ("S1", "supported"), (None, "unknown")]
        assert db.execute("SELECT target_observation_ids_json,applied_observation_ids_json FROM review_groups").fetchone() == (
            '["o1","o2","o3","o4"]', '["o2","o3","o4"]')
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    report = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "逐提议隔离" in report and "保留有归属" in report and "保留未知" in report
    assert "| 4 | 2 | 1 | 1 | 1 |" in report
    assert "归属提议不合格、未应用" in report and "显式对象引用依赖" in report
    assert "整组隔离待复核" not in report
    assert data == original


def relation_only_sample():
    data = partial_sample()
    item = data["observations"][0]
    item.update(subject_id="L1", binding_status="supported", target_match="supported", binding_evidence_ids=["e1"])
    group = data["review_groups"][0]
    group.update(observation_ids=[], record_types=[], applied_observation_ids=group["target_observation_ids"][:])
    group["validation_summary"].update(observation_count=0, applied_count=4)
    review = group["diagnostic"]["reviews"][0]
    review.update(proposal_type="relation", path="relations[0]", proposal_id="relations[0]", observation_ids=[],
                  code="quote_not_found", raw_proposal={"source_id": "L1", "target_id": "L2", "evidence": []})
    review["issues"] = [{"path": "relations[0].evidence[0]", "code": "quote_not_found", "phase": "evidence",
                        "message": "Quote absent."}]
    group["diagnostic"].update(group["validation_summary"])
    data["jobs"][0]["review_observation_ids"] = []
    return data


def test_relation_only_rejection_still_requires_review_without_hiding_observations(tmp_path):
    data = relation_only_sample()
    assert is_review_only_partial(data)
    save_analysis(data, tmp_path)
    report = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "0条观测的归属暂未应用" in report and "仅对象/关系提议" in report
    assert len(read_csv(tmp_path / "observations.csv")) == 4


@pytest.mark.parametrize("damage", ["partition", "duplicate", "job", "affected", "raw_id", "kind", "path", "counts",
                                    "issues", "dependencies", "applied_pending", "missing_review", "complete",
                                    "retained_no_evidence", "unknown_subject", "job_overlap", "job_gap"])
def test_damaged_partial_review_is_rejected_before_writing(tmp_path, damage):
    data = partial_sample()
    group = data["review_groups"][0]
    review = group["diagnostic"]["reviews"][0]
    if damage == "partition":
        group["applied_observation_ids"].remove("o2")
    elif damage == "duplicate":
        group["applied_observation_ids"].append("o1")
    elif damage == "job":
        data["jobs"][0]["review_observation_ids"] = []
    elif damage == "affected":
        review["observation_ids"] = ["o2"]
    elif damage == "raw_id":
        review["raw_proposal"]["observation_id"] = "o2"
    elif damage == "kind":
        review["proposal_type"] = "unknown"
    elif damage == "path":
        review["path"] = "../untrusted"
    elif damage == "counts":
        group["validation_summary"]["observation_count"] = True
    elif damage == "issues":
        review["issues"] = []
    elif damage == "dependencies":
        review["dependencies"] = ["bad", "bad"]
    elif damage == "applied_pending":
        data["observations"][1]["binding_status"] = "pending"
    elif damage == "missing_review":
        group["diagnostic"]["reviews"] = []
    elif damage == "retained_no_evidence":
        data["observations"][1]["binding_evidence_ids"] = []
    elif damage == "unknown_subject":
        data["observations"][3]["subject_id"] = "L1"
    elif damage == "job_overlap":
        data["jobs"].append({"job_id": "verify-0002", "status": "completed", "observation_ids": ["o2"]})
    elif damage == "job_gap":
        data["jobs"][0]["observation_ids"] = ["o1", "o2", "o3"]
        group["target_observation_ids"] = ["o1", "o2", "o3"]
        group["applied_observation_ids"] = ["o2", "o3"]
        group["validation_summary"]["applied_count"] = 2
        group["diagnostic"]["applied_count"] = 2
    else:
        data["status"] = "complete"
    with pytest.raises(ValueError):
        save_analysis(data, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("damage", ["pending", "limited", "failed", "format", "partition", "job", "missing_review", "applied_pending"])
def test_partial_continuation_does_not_cover_unfinished_work(damage):
    data = partial_sample()
    if damage in {"pending", "limited", "failed"}:
        data["jobs"][0]["status"] = damage
    elif damage == "format":
        data["source_format_status"] = "partial_format_error"
    elif damage == "partition":
        data["review_groups"][0]["applied_observation_ids"] = []
    elif damage == "job":
        data["jobs"][0]["review_observation_ids"] = []
    elif damage == "missing_review":
        data["review_groups"][0]["diagnostic"]["reviews"] = []
    else:
        data["observations"][1]["binding_status"] = "pending"
    data["review_only"] = True
    assert not is_review_only_partial(data)


def test_partial_progress_distinguishes_retained_and_held_without_raw_text():
    stream = StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "review_required", "job_id": "verify-0001", "used": 4, "limit": 48,
             "observation_count": 2, "applied_count": 4, "proposal_count": 2,
             "validation_summary": {"code": "proposal_review"}, "response_text": "PRIVATE"})
    console({"stage": "analysis", "event": "reused", "job_id": "verify-0001", "status": "review_required",
             "error_code": "proposal_review"})
    text = stream.getvalue()
    assert "retained 4 observation decisions" in text and "2 observations held" in text
    assert "Retained proposals remain applied" in text and "no request resent" in text
    assert "No group proposals" not in text and "PRIVATE" not in text


def test_partial_progress_does_not_echo_untrusted_count_values():
    stream = StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "review_required", "observation_count": True,
             "applied_count": "PRIVATE", "proposal_count": -1, "validation_summary": {"code": "proposal_review"}})
    assert "retained ? observation decisions; ? observations held" in stream.getvalue()
    assert "? rejected proposals" in stream.getvalue() and "PRIVATE" not in stream.getvalue()

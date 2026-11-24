"""Exports are proposals with lossless observations, not inferred database facts."""

from copy import deepcopy
import csv
import json
import sqlite3

import pytest

from niceextract.analysis_export import save_analysis


def sample():
    def obj(key, name, kind="layer", target=True):
        return {"object_id": key, "name": name, "kind": kind, "scope": key,
                "is_table_subject": target, "mention_evidence_ids": ["e1"], "semantic_status": "model_proposed"}

    def obs(key, subject, value, conditions=None, status="supported"):
        return {"observation_id": key, "candidate_id": "candidate-" + key,
                "source_record": "/candidates/" + key[1:], "record_type": "porosity", "source_kind": "prose",
                "entity": "sample label", "value": value, "unit": "%", "conditions": conditions,
                "subject_id": subject, "binding_status": status, "reason": "source relation",
                "blockers": [], "evidence_ids": ["e1"], "binding_evidence_ids": ["e1"],
                "semantic_status": "model_proposed"}

    return {
        "analysis_version": "0.1", "status": "complete", "semantic_status": "model_proposed",
        "source": {"name": "synthetic.md", "sha256": "0" * 64, "document_id": "doc1"},
        "task": {"name": "synthetic", "description": "Keep raw observations", "fields": {
            "porosity": "Reported porosity", "permeability": "Reported permeability"}},
        "table_subject": "layer", "objects": [obj("L1", "Same layer"), obj("L2", "Same layer"),
                                                    obj("S1", "Core S1", "sample", False)],
        "observations": [obs("o1", "L1", "10–20", "range"), obs("o2", "L1", "15", "mean"),
                         obs("o3", "S1", "12", "sample measurement"), obs("o4", None, "9", status="unknown")],
        "relations": [{"relation_id": "r1", "source_id": "S1", "target_id": "L2",
                       "predicate": "sampled_from", "evidence_ids": ["e1"], "semantic_status": "model_proposed"}],
        "evidence": [{"evidence_id": "e1", "quote": "Core S1 from the second layer has porosity 12%.",
                      "start": 0, "end": 52}], "jobs": [], "review": [], "quarantined": [],
        "budget": {"limit": 2, "reserved": 1, "extraction_reserved": 3, "total_reserved": 4},
    }


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_same_names_stay_distinct_multiple_conditions_remain_and_samples_never_inherit(tmp_path):
    analysis = sample()
    before = deepcopy(analysis)
    save_analysis(analysis, tmp_path)
    view = json.loads((tmp_path / "objects.json").read_text(encoding="utf-8"))
    assert [obj["object_id"] for obj in view["objects"]] == ["L1", "L2"]
    assert [obj["name"] for obj in view["objects"]] == ["Same layer", "Same layer"]
    assert [item["value"] for item in view["objects"][0]["fields"]["porosity"]] == ["10–20", "15"]
    assert view["objects"][1]["fields"]["porosity"] == []
    assert [item["observation_id"] for item in view["related_observations"]] == ["o3"]
    assert [item["observation_id"] for item in view["unbound_observations"]] == ["o4"]
    assert len(read_csv(tmp_path / "observations.csv")) == 4
    assert len(read_csv(tmp_path / "objects.csv")) == 3
    wide = read_csv(tmp_path / "object_table.csv")
    assert len(wide) == 2 and json.loads(wide[1]["porosity"]) == []
    assert [item["conditions"] for item in json.loads(wide[0]["porosity"])] == ["range", "mean"]
    assert json.loads(wide[0]["permeability"]) == []
    assert analysis == before
    assert json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8")) == analysis


def test_sqlite_joins_retain_original_pointer_raw_values_and_foreign_keys(tmp_path):
    save_analysis(sample(), tmp_path)
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        row = db.execute("""SELECT o.name, v.value_json, v.source_record, e.quote
            FROM observations v JOIN objects o ON o.object_id=v.subject_id
            JOIN observation_evidence p ON p.observation_id=v.observation_id AND p.role='observation'
            JOIN evidence e ON e.evidence_id=p.evidence_id WHERE v.observation_id='o3'""").fetchone()
        assert row[:3] == ("Core S1", '"12"', "/candidates/3")
        assert "12%" in row[3]
        assert db.execute("SELECT count(*) FROM observations WHERE subject_id IS NULL").fetchone() == (1,)
        assert db.execute("SELECT source_id,target_id FROM relations").fetchall() == [("S1", "L2")]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE observations SET subject_id='missing' WHERE observation_id='o1'")


@pytest.mark.parametrize("dangerous", ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(1)", " \t=HYPERLINK(1)"])
def test_csv_formula_safety_does_not_modify_json_or_sqlite(tmp_path, dangerous):
    analysis = sample()
    analysis["objects"][0]["name"] = dangerous
    analysis["observations"][0]["value"] = dangerous
    save_analysis(analysis, tmp_path)
    assert read_csv(tmp_path / "objects.csv")[0]["name"] == "'" + dangerous
    assert read_csv(tmp_path / "observations.csv")[0]["value"] == "'" + dangerous
    assert json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))["objects"][0]["name"] == dangerous
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert json.loads(db.execute("SELECT value_json FROM observations WHERE observation_id='o1'").fetchone()[0]) == dangerous


def test_blockers_unknowns_and_failed_state_are_visible_without_claiming_acceptance(tmp_path):
    analysis = sample()
    analysis["status"] = "failed"
    analysis["observations"][0]["blockers"] = [{"code": "required_missing", "message": "unit not verified"}]
    analysis["observations"][3]["reason"] = "ambiguous ownership"
    analysis["quarantined"] = [{"raw": "invalid record"}]
    save_analysis(analysis, tmp_path)
    markdown = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "失败 / failed" in markdown
    assert "待人工复核" in markdown and "不是专家确认" in markdown
    assert "unit not verified" in markdown and "ambiguous ownership" in markdown
    assert "原格式隔离记录：1" in markdown
    assert json.loads(read_csv(tmp_path / "observations.csv")[0]["blockers"])[0]["code"] == "required_missing"


def test_redaction_in_every_artifact_and_no_caller_mutation(tmp_path):
    analysis = sample()
    secret = "private-token-for-synthetic-test"
    analysis["source"]["name"] = secret
    analysis["evidence"][0]["quote"] += secret
    analysis["observations"][0]["reason"] = secret
    save_analysis(analysis, tmp_path, secrets=[secret])
    for path in tmp_path.iterdir():
        assert secret.encode() not in path.read_bytes(), path.name
    assert analysis["source"]["name"] == secret


@pytest.mark.parametrize("damage", ["subject", "relation", "evidence", "duplicate", "accepted", "position", "target"])
def test_invalid_snapshot_rejected_before_any_output(tmp_path, damage):
    analysis = sample()
    if damage == "subject":
        analysis["observations"][0]["subject_id"] = "missing"
    elif damage == "relation":
        analysis["relations"][0]["target_id"] = "missing"
    elif damage == "evidence":
        analysis["observations"][0]["evidence_ids"] = ["missing"]
    elif damage == "duplicate":
        analysis["objects"].append(deepcopy(analysis["objects"][0]))
    elif damage == "accepted":
        analysis["observations"][0]["semantic_status"] = "accepted"
    elif damage == "position":
        analysis["evidence"][0]["start"] = -1
    else:
        analysis["observations"][0]["record_type"] = "undeclared"
    target = tmp_path / "new-output"
    with pytest.raises(ValueError):
        save_analysis(analysis, target)
    assert not target.exists()


def test_existing_output_is_never_overwritten_or_partly_reexported(tmp_path):
    (tmp_path / "observations.csv").write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_analysis(sample(), tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / "observations.csv"]
    assert (tmp_path / "observations.csv").read_text() == "original"


def test_generic_target_name_cannot_overwrite_object_metadata(tmp_path):
    analysis = sample()
    analysis["task"]["fields"] = {"object_id": "Reported identifier", "name": "Reported name"}
    for item in analysis["observations"]:
        item["record_type"] = "name"
    save_analysis(analysis, tmp_path)
    rows = read_csv(tmp_path / "object_table.csv")
    assert rows[0]["metadata.object_id"] == "L1" and rows[0]["metadata.name"] == "Same layer"
    assert json.loads(rows[0]["object_id"]) == []
    assert len(json.loads(rows[0]["name"])) == 2


def test_unlocated_evidence_and_zero_false_values_are_retained(tmp_path):
    analysis = sample()
    analysis["evidence"][0].update(start=None, end=None)
    analysis["observations"][0]["value"] = 0
    analysis["observations"][1]["value"] = False
    save_analysis(analysis, tmp_path)
    row = read_csv(tmp_path / "object_table.csv")[0]
    assert [item["value"] for item in json.loads(row["porosity"])] == [0, False]
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert db.execute("SELECT start,end FROM evidence").fetchone() == (None, None)


def test_verification_target_mismatch_is_retained_but_not_projected(tmp_path):
    analysis = sample()
    analysis["analysis_version"] = "0.2"
    for item in analysis["observations"]:
        item.update(target_match="supported", property_name="Reported porosity", statistic=None)
    analysis["observations"][0].update(target_match="unsupported", property_name="Pore volume fraction",
                                      statistic="range", reason="The task excludes this measurement kind.")
    analysis["observations"][1]["target_match"] = "unknown"
    before = deepcopy(analysis)
    save_analysis(analysis, tmp_path)
    view = json.loads((tmp_path / "objects.json").read_text(encoding="utf-8"))
    assert view["objects"][0]["fields"]["porosity"] == []
    assert [row["observation_id"] for row in view["unmatched_observations"]] == ["o1", "o2"]
    assert [row["observation_id"] for row in view["related_observations"]] == ["o3"]
    assert [row["observation_id"] for row in view["unbound_observations"]] == ["o4"]
    assert len(read_csv(tmp_path / "observations.csv")) == 4
    assert len(read_csv(tmp_path / "unmatched_observations.csv")) == 2
    assert json.loads(read_csv(tmp_path / "object_table.csv")[0]["porosity"]) == []
    assert "尚不能填入目标字段" in (tmp_path / "analysis.md").read_text(encoding="utf-8")
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert db.execute("SELECT target_match,property_name,statistic FROM observations WHERE observation_id='o1'").fetchone() == (
            "unsupported", "Pore volume fraction", "range")
        assert db.execute("SELECT count(*) FROM observations").fetchone() == (4,)
    assert analysis == before


def test_verification_missing_target_assessment_cannot_enter_wide_table(tmp_path):
    analysis = sample()
    analysis["analysis_version"] = "0.2"
    save_analysis(analysis, tmp_path)
    assert all(json.loads(row["porosity"]) == [] for row in read_csv(tmp_path / "object_table.csv"))
    assert len(read_csv(tmp_path / "unmatched_observations.csv")) == 3


def test_verification_job_stop_reason_is_visible(tmp_path):
    analysis = sample()
    analysis.update(analysis_version="0.2", status="partial", jobs=[{
        "job_id": "verify-0001", "status": "partial", "attempts": 4,
        "error_code": "step_limit", "reason": "Step limit reached; ownership remains unknown."}, {
        "job_id": "verify-0002", "status": "failed", "attempts": 1,
        "error_code": "invalid_analysis_response", "steps": [{"error": "Citation outside viewed passages."}]}])
    save_analysis(analysis, tmp_path)
    markdown = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "Step limit reached" in markdown and "verify\\-0001" in markdown
    assert "Citation outside viewed passages" in markdown


def test_verification_scope_diagnostic_is_readable_and_does_not_echo_raw_error(tmp_path):
    analysis = sample()
    analysis.update(analysis_version="0.2", status="failed", jobs=[{
        "job_id": "verify-0001", "status": "failed", "attempts": 2,
        "error": "PRIVATE JOB ERROR", "steps": [{
            "error": "PRIVATE RAW ANSWER", "validation_summary": {
                "category": "model_response", "phase": "binding_scope", "code": "binding_scope_mismatch",
                "expected_count": 6, "returned_count": 18, "missing_count": 0,
                "duplicate_count": 0, "unexpected_count": 12, "reference_count": 12,
                "unexpected_ids": ["PRIVATE OBSERVATION"]},
            "validation_details": "analysis/validation/verify-0001.step-02.errors.json"}]}])
    before = deepcopy(analysis)
    save_analysis(analysis, tmp_path)
    markdown = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "期望6条，返回18条" in markdown and "越界12条（其中12条来自只读参考）" in markdown
    assert "详情：diagnostics/analysis/validation/" in markdown
    assert "普通恢复会保留失败任务" in markdown and "不会修复已失败任务" in markdown
    assert "PRIVATE" not in markdown
    assert json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8")) == before
    assert analysis == before


@pytest.mark.parametrize("category,expected", [
    ("internal_state", "原文或内部状态校验失败"),
    ("model_response", "模型回答未通过查证协议检查"),
])
def test_verification_error_category_uses_safe_report_text(tmp_path, category, expected):
    analysis = sample()
    analysis.update(analysis_version="0.2", status="failed", jobs=[{
        "job_id": "verify-0001", "status": "failed", "attempts": 1,
        "steps": [{"error": "PRIVATE EXCEPTION", "validation_summary": {
            "category": category, "code": "PRIVATE CODE", "phase": "PRIVATE PHASE"},
            "validation_details": "../PRIVATE/path.json"}]}])
    save_analysis(analysis, tmp_path)
    markdown = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert expected in markdown and "PRIVATE" not in markdown


def test_verification_audit_counts_preserve_unknowns_unmatched_and_unbound_objects(tmp_path):
    analysis = sample()
    analysis["analysis_version"] = "0.2"
    for item, match in zip(analysis["observations"], ("unsupported", "unknown", "supported", "unknown")):
        item["target_match"] = match
    # L1 has direct observations despite target mismatch; L2 only participates
    # in a source relation and is retained, not treated as invalid or deleted.
    pending = deepcopy(analysis["observations"][3])
    pending.update(observation_id="o5", candidate_id="candidate-o5", source_record="/candidates/5",
                   binding_status="pending")
    failed = deepcopy(pending)
    failed.update(observation_id="o6", candidate_id="candidate-o6", source_record="/candidates/6",
                  binding_status="failed")
    analysis["observations"].extend([pending, failed])
    before = deepcopy(analysis)
    save_analysis(analysis, tmp_path)
    markdown = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "观测共6条；模型认为归属有支持3条，归属未知1条，待处理1条，查证失败1条" in markdown
    assert "模型认为有支持1条，不支持1条，未确定4条" in markdown
    assert "未被归属明确的观测直接引用的对象1个" in markdown
    assert "不据此删除或判定错误" in markdown and "不是准确率" in markdown
    assert len(read_csv(tmp_path / "objects.csv")) == 3
    assert len(read_csv(tmp_path / "object_table.csv")) == 2
    assert analysis == before


def review_sample():
    data = sample()
    data.update(analysis_version="0.3", status="partial")
    for item in data["observations"]:
        item.update(target_match="supported", property_name=None, statistic=None)
    data["observations"][0].update(binding_status="review_required", subject_id=None,
        binding_evidence_ids=[], target_match="unknown", reason="Whole group retained for review.")
    group = {"job_id": "verify-0001", "observation_ids": ["o1"], "record_types": ["porosity"],
        "response_details": "analysis/responses/verify-0001.step-01.txt",
        "validation_details": "analysis/validation/verify-0001.step-01.errors.json",
        "validation_summary": {"code": "quote_ambiguous", "category": "model_response", "ambiguity_count": 1},
        "response_text": '{"action":"decide","bindings":[]}',
        "diagnostic": {"code": "quote_ambiguous", "ambiguities": []}}
    data["review_groups"] = [group]
    data["jobs"] = [{"job_id": "verify-0001", "status": "review_required", "observation_ids": ["o1"],
        "attempts": 1, "steps": [group]}, {"job_id": "verify-0002", "status": "completed", "attempts": 1,
        "observation_ids": ["o2", "o3", "o4"]}]
    quote = data["evidence"][0]["quote"]
    prefix = "Original passage. "
    data["evidence"][0].update(start=len(prefix), end=len(prefix) + len(quote))
    text = prefix + quote + " Additional context."
    data["evidence_contexts"] = [{"evidence_id": "e1", "artifact_id": "verify-0002.step-01",
        "passage_id": "passage-000001", "start": 0, "end": len(text), "text": text, "model_visible": False}]
    return data


def test_isolated_group_and_display_context_stay_separate_in_every_export(tmp_path):
    data = review_sample()
    original = deepcopy(data)
    save_analysis(data, tmp_path)
    assert data == original
    assert json.loads((tmp_path / "review_groups.json").read_text(encoding="utf-8")) == data["review_groups"]
    assert json.loads((tmp_path / "evidence_contexts.json").read_text(encoding="utf-8")) == data["evidence_contexts"]
    wide = read_csv(tmp_path / "object_table.csv")
    assert [item["value"] for item in json.loads(wide[0]["porosity"])] == ["15"]
    held = read_csv(tmp_path / "unbound_observations.csv")
    assert next(item for item in held if item["observation_id"] == "o1")["binding_status"] == "review_required"
    assert read_csv(tmp_path / "review_groups.csv")[0]["record_types"] == '["porosity"]'
    assert read_csv(tmp_path / "evidence.csv")[0]["quote"] == data["evidence"][0]["quote"]
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert db.execute("SELECT subject_id,binding_status FROM observations WHERE observation_id='o1'").fetchone() == (None, "review_required")
        assert db.execute("SELECT observation_ids_json FROM review_groups").fetchone() == ('["o1"]',)
        assert db.execute("SELECT response_text FROM review_groups").fetchone()[0] == data["review_groups"][0]["response_text"]
        assert db.execute("SELECT evidence_id,artifact_id,model_visible FROM evidence_contexts").fetchone() == ("e1", "verify-0002.step-01", 0)
        assert db.execute("SELECT quote FROM evidence").fetchone()[0] == data["evidence"][0]["quote"]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    report = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "部分完成 / partial" in report and "1组、1条观测" in report
    assert "原始引文" in report and "展示上下文" in report and "含程序补充上下文" in report
    assert "不代表原文没有报告" in report and "不会自动重答" in report


@pytest.mark.parametrize("damage", ["applied", "missing_group", "wrong_field", "complete", "context_text", "context_duplicate", "context_foreign", "unsafe_path"])
def test_invalid_isolation_or_display_context_rejected_before_export(tmp_path, damage):
    data = review_sample()
    if damage == "applied":
        data["observations"][0]["subject_id"] = "L1"
    elif damage == "missing_group":
        data["review_groups"] = []
    elif damage == "wrong_field":
        data["review_groups"][0]["record_types"] = ["permeability"]
    elif damage == "complete":
        data["status"] = "complete"
    elif damage == "context_text":
        data["evidence_contexts"][0]["text"] = "fabricated"
    elif damage == "context_duplicate":
        data["evidence_contexts"].append(deepcopy(data["evidence_contexts"][0]))
    elif damage == "context_foreign":
        data["evidence_contexts"][0]["evidence_id"] = "missing"
    else:
        data["review_groups"][0]["response_details"] = "../private-file.txt"
    target = tmp_path / "output"
    with pytest.raises(ValueError):
        save_analysis(data, target)
    assert not target.exists()


def test_context_redaction_preserves_original_offsets_and_provenance(tmp_path):
    data = review_sample()
    original = deepcopy(data)
    secret = "Additional context"
    save_analysis(data, tmp_path, secrets=[secret])
    contexts = json.loads((tmp_path / "evidence_contexts.json").read_text(encoding="utf-8"))
    assert secret not in contexts[0]["text"]
    assert contexts[0]["end"] == original["evidence_contexts"][0]["end"]
    assert contexts[0]["model_visible"] is False and data == original
    for path in tmp_path.iterdir():
        assert secret.encode() not in path.read_bytes()


def test_legacy_export_does_not_gain_review_files_or_context_tables(tmp_path):
    save_analysis(sample(), tmp_path)
    assert not (tmp_path / "review_groups.json").exists()
    assert not (tmp_path / "evidence_contexts.csv").exists()
    with sqlite3.connect(tmp_path / "analysis.sqlite") as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('evidence_contexts','review_groups')").fetchall()


def test_review_without_trace_retains_answer_and_candidate_positions(tmp_path):
    data = review_sample()
    group = data["review_groups"][0]
    group.update(response_details=None, validation_details=None)
    group["diagnostic"]["ambiguities"] = [{"quote": "repeated phrase", "matches": [{"start": 10}, {"start": 30}]}]
    save_analysis(data, tmp_path)
    assert json.loads(read_csv(tmp_path / "review_groups.csv")[0]["diagnostic"]) == group["diagnostic"]
    report = (tmp_path / "analysis.md").read_text(encoding="utf-8")
    assert "review\\_groups\\.json 的 response\\_text" in report and group["response_text"] not in report

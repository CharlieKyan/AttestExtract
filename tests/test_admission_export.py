import csv
import json
from copy import deepcopy

from attestextract import assemble_from_files, cli, extract
from attestextract.annotation_view import build_annotation
from attestextract.execution import digest
from attestextract.models import BackendResponse


SOURCE = "Bench A produced 80 boxes. Both benches together produced 140 boxes."
TASK = {"prompt_description": "Extract counts for individual benches.",
        "field_list": {"bench": "Bench name", "count": "Count produced by this individual bench"}}


class Answers:
    name = "synthetic"

    def generate(self, request):
        packet = json.loads(request.messages[-1]["content"])
        window = packet["windows"][0]["id"]

        def cite(quote):
            return {"window": window, "quote": quote}

        sentence = SOURCE.split(".")[0]
        if packet["stage"] == "inventory":
            answer = {"objects": [{"key": "a", "label": "Bench A", "mention": cite("Bench A"),
                                   "qualification": cite(sentence)}]}
        else:
            owner, = packet["objects"]
            positive = {"owner": owner["object_id"], "field": "count", "value": "80", "unit": "boxes",
                        "value_source": cite("80"), "owner_source": cite("Bench A"), "property_source": cite("produced"),
                        "link_source": cite(sentence), "fit_source": cite(sentence), "fit": "supported",
                        "purpose": "measurement", "direct": True, "reason": "Count in the first sentence."}
            positive["property_source"].update(occurrence=0)
            total = dict(positive, owner=None, value="140", value_source=cite("140"), fit="unsupported",
                         direct=False, purpose="aggregate", reason="Both benches combined.")
            rejected = dict(positive, value="17", value_source=cite("17"))
            answer = {"facts": [positive, positive, total, rejected], "nominations": []}
        return BackendResponse(text=json.dumps(answer), usage={"total_tokens": 10})


def run(tmp_path):
    path = tmp_path / "source.md"
    path.write_bytes(SOURCE.encode())
    run_dir = tmp_path / "run"
    result = extract(SOURCE, **TASK, backend=Answers(), subject_strategy="entity-first",
                     table_subject="individual benches", dataset_options={"subject_label_field": "bench"},
                     output_dir=run_dir)
    return result, path, run_dir


def test_audit_reproduces_live_projection_and_preserves_every_receipt(tmp_path):
    result, source, run_dir = run(tmp_path)
    revised = tmp_path / "revised"
    assert cli.main(["assemble-dataset", "--audit-admission", "--run-dir", str(run_dir),
                     "--input", str(source), "--output-dir", str(revised)]) == 0
    archive = json.loads((revised / "dataset.json").read_bytes())
    assert result.dataset == archive == assemble_from_files(source, run_dir)
    with (revised / "proposals.csv").open(encoding="utf-8-sig", newline="") as stream:
        proposals = list(csv.DictReader(stream))
    assert [p["value"] for p in proposals] == ["80", "80", "140", "17"]
    assert len({p["proposal_id"] for p in proposals}) == 4
    assert proposals[0]["row_ids"] == proposals[1]["row_ids"] != "[]"
    assert proposals[1]["prior_disposition"] == "coalesced"
    assert proposals[2]["row_ids"] == proposals[3]["row_ids"] == "[]"
    assert proposals[3]["disposition"] == "proposal_only"
    assert archive["rows"] == [{"bench": "Bench A", "count": "80"}]
    annotation = build_annotation(result)
    assert annotation == json.loads((revised / "annotation.json").read_bytes())
    assert len(annotation["records"][0]["fields"]["count"]) == 1
    assert len(annotation["review_proposals"]) == 2
    assert annotation["review_proposals"][0]["fit"] == "contradicted"
    assert annotation["missing_fields"] == []
    markdown = (revised / "annotation.md").read_text(encoding="utf-8")
    assert "身份待核" in markdown and "原文依据" in markdown


def test_review_of_standalone_new_result_reports_missing_projection(tmp_path):
    _, _, run_dir = run(tmp_path)
    target = tmp_path / "review"
    (run_dir / "dataset" / "dataset.json").unlink()
    assert cli.main(["export-review", "--input", str(run_dir), "--output-dir", str(target)]) == 1
    assert not target.exists()


def test_overrides_only_change_audited_rows_not_the_frozen_comparison(tmp_path):
    _, source, run_dir = run(tmp_path)
    data = assemble_from_files(source, run_dir, audit_admission=True,
                               field_policies={"count": {"allowed_units": ["pallets"]}})
    assert data["rows"] == []
    assert data["admission"]["strict_rows"] == [{"bench": "Bench A", "count": "80"}]
    assert data["proposals"][0]["prior_row_ids"] and not data["proposals"][0]["row_ids"]


def test_legacy_without_diagnostics_reports_gap_and_retains_strict_snapshot(tmp_path):
    result, source, run_dir = run(tmp_path)
    legacy = deepcopy(result.model_dump(mode="json"))
    report = legacy["entity_first"]
    for key in list(report):
        if key.startswith("admission_") or key == "snapshot_id":
            del report[key]
    for c in report["calls"]:
        c.pop("assignment_receipt", None)
    report["snapshot_id"] = digest([legacy["task"], report])
    old = tmp_path / "legacy"
    old.mkdir()
    (old / "result.json").write_text(json.dumps(legacy))
    strict = assemble_from_files(source, old)
    assert "proposals" not in strict
    revised = tmp_path / "audit"
    assert cli.main(["assemble-dataset", "--audit-admission", "--input", str(source),
                     "--run-dir", str(old), "--output-dir", str(revised)]) == 2
    data = json.loads((revised / "dataset.json").read_bytes())
    assert data["admission"]["coverage"][0]["reason"] == "missing_call_artifact"
    assert data["proposals"] == [] and data["rows"] == []
    assert data["admission"]["strict_rows"] == strict["rows"] != []
    assert all(o["reason"] == "missing_proposal_receipt" for o in data["observations"])

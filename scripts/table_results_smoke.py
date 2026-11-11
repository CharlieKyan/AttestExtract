"""Installed-package acceptance of complete A-only table extraction results.

Copy this script and table_route_experiment.py/table_route_runner.py from the
sdist into one directory outside the checkout. All observations are synthetic;
outbound socket operations are blocked throughout preparation and replay.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import attestextract
from table_route_runner import prepare_bundle, read_json, run_bundle, write_report


def _hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _run(root: Path, cases: list[dict], *, partial: bool = False) -> dict:
    manifest = prepare_bundle(root, cases, replay_only=True)
    assert manifest["schema_version"] == "table-a-0.2"
    assert manifest["max_calls"] == len(cases)
    first = run_bundle(root)
    assert first["live_backend_invocations_this_run"] == 0
    assert first["remaining_slots"] == 0
    expected_status = "partial_format_error" if partial else "success"
    assert first["statuses"] == {expected_status: len(cases)}
    before = _hashes(root)
    resumed = run_bundle(root)
    assert resumed["live_backend_invocations_this_run"] == 0
    assert resumed["statuses"] == first["statuses"]
    assert _hashes(root) == before, "Resume must not rewrite or resend a completed slot"
    inputs = read_json(root / "table-inputs.json")
    rows = []
    for case in cases:
        slot = root / "trials" / f'r1-{case["id"]}-A'
        outcome = read_json(slot / "outcome.json")
        parsed = read_json(slot / "parsed.json")
        result = read_json(slot / "results.json")
        evaluation = read_json(slot / "evaluation.json")
        assert outcome["stage"] == "A" and outcome["mode"] == "replay"
        assert outcome["status"] == expected_status
        assert result["schema_version"] == "table-route-v2"
        assert result["semantic_verification"] == "not_run"
        assert result["evidence"]["table_input"] == inputs[case["id"]]
        assert sum(len(r["record_ids"]) for r in result["candidates"]) == len(parsed["records"])
        assert result["rejections"] == parsed["rejections"]
        for record in result["candidates"]:
            assert record["semantic_verification"] == "not_run"
            assert record["evidence_ref"] == result["evidence"]["evidence_id"]
            assert record["field_checks"]
            assert {"mechanical_errors", "relationship_warnings", "risk_diagnostics"} <= record.keys()
        expected = case.get("expected_diagnostics", {}).get("A", [])
        assert set(expected) <= {issue["code"] for issue in evaluation["issues"]}
        if partial:
            assert len(parsed["records"]) == len(result["candidates"]) == 2
            assert parsed["record_indices"] == [0, 1]
            assert len(parsed["rejections"]) == 1
            assert parsed["rejections"][0]["index"] == 2
            assert parsed["rejections"][0]["errors"][0]["loc"] == ["fields", "value"]
            assert len(evaluation["current_results"]) == 2
        rows.append({"case_id": case["id"], "status": outcome["status"],
                     "result_count": len(result["candidates"]), "rejected_count": len(parsed["rejections"])})
    report = write_report(root)
    assert report.is_file() and (root / "SUMMARY.json").is_file()
    return {"slots": len(cases), "resume_new_calls": 0, "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    examples = Path(attestextract.__file__).resolve().parent / "examples" / "table_routes"
    groups = {name: read_json(examples / f"{name}.json")
              for name in ("cases", "edge_cases", "failure_cases")}
    assert sum(len(cases) for cases in groups.values()) == 11
    partial = deepcopy(next(case for case in groups["cases"] if case["id"] == "paired_parameters"))
    partial["id"] = "optional-null-with-invalid-record"
    records = partial["replays"]["A"]["records"]
    empty = next(record["fields"]["unit"] for record in records if record["fields"]["unit"]["value"] is None)
    empty.pop("relation")
    invalid = deepcopy(records[0])
    invalid["record_id"] = "invalid-protocol"
    invalid["fields"]["value"].pop("relation")
    records.append(invalid)
    network_attempts = []

    def blocked(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Network access is forbidden in table result acceptance")

    with patch("socket.socket.connect", blocked), patch("socket.socket.connect_ex", blocked), \
            patch("socket.create_connection", blocked), patch("socket.getaddrinfo", blocked):
        runs = [_run(root / name, cases) for name, cases in groups.items()]
        runs.append(_run(root / "partial-format", [partial], partial=True))
    assert not network_attempts
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": str(Path(attestextract.__file__).resolve()),
               "real_model_calls": 0, "network_attempts": 0,
               "planned_slots": sum(run["slots"] for run in runs),
               "completed_resume_new_calls": 0, "runs": runs,
               "notice": "Synthetic offline package acceptance; no real endpoint or expert verification."}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

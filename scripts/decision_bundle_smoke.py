"""Installed-package acceptance with synthetic source and fixed offline replies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import attestextract
from attestextract import ExecutionOptions, RunTrace, cli, extract
from attestextract.backends import ReplayBackend
from attestextract.decision_bundle import load_decision_bundle


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _hashes(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def run_acceptance(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    source = "Item A reads 7. Item B reads 9.\n\nNo observations returned here."
    task = {"name": "Synthetic decision coverage", "record_types": {"reading": {
        "fields": {"subject": {"type": "string", "required": True},
                   "value": {"type": "integer", "required": True}}, "identity_fields": ["subject"]}}}
    records = [{"record_type": "reading", "values": {"subject": f"Item {letter}", "value": value},
                "quotes": {"subject": f"Item {letter}", "value": str(value) if letter == "A" else ""}}
               for letter, value in (("A", 7), ("B", 9))]
    run = directory / "run"
    trace = RunTrace(run / "diagnostics")
    result = extract(source, task, backend=ReplayBackend([{"records": records}, {"records": []}]),
                     trace=trace, max_chunk_chars=40, backend_identity="synthetic-decision-materials-replay-v1",
                     execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=2))
    assert result.run.chunk_count == 2 and len(result.candidates) == 2
    _write(run / "result.json", result.model_dump(mode="json"))
    trace.finish(result.model_dump(mode="json"))
    before = _hashes(run)
    materials = directory / "materials"
    assert cli.main(["prepare-decisions", "--run", str(run), "--output", str(materials)]) == 0
    bundle_path = materials / "decision-input.json"
    bundle = load_decision_bundle(bundle_path)
    assert len(bundle.batches) == 2 and len(bundle.tasks) == 3
    assert bundle.document.text == source
    assert bundle.extraction == result
    responses = {"bundle_id": bundle.bundle_id, "results": [
        {"task_id": task.task_id, "action": "select", "candidate_id": task.candidate_ids[0]}
        if task.candidate_ids else {"task_id": task.task_id, "action": "review"}
        for task in bundle.tasks]}
    response_path = directory / "responses.json"
    _write(response_path, responses)
    checked = directory / "checked"
    assert cli.main(["check-decisions", "--bundle", str(bundle_path), "--responses", str(response_path),
                     "--output", str(checked)]) == 0
    ledger = json.loads((checked / "decision-ledger.json").read_text(encoding="utf-8"))
    assert ledger["status"] == "valid" and ledger["applied"] is False
    assert ledger["created_final_records"] == 0
    selections = [entry for entry in ledger["entries"] if entry["recommendation"] == "suggest_keep"]
    assert len(selections) == 2 and any(entry["blocking_reasons"] for entry in selections)
    assert len({task.batch_id for task in bundle.tasks if task.candidate_ids}) == 1
    # Duplicate returns are rejected; no "last answer wins" behavior.
    _write(response_path, {"bundle_id": bundle.bundle_id, "results": responses["results"] * 2})
    invalid = directory / "invalid"
    assert cli.main(["check-decisions", "--bundle", str(bundle_path), "--responses", str(response_path),
                     "--output", str(invalid)]) == 2
    assert json.loads((invalid / "decision-ledger.json").read_text(encoding="utf-8"))["status"] == "invalid"
    _write(response_path, {"bundle_id": bundle.bundle_id, "results": []})
    incomplete = directory / "incomplete"
    assert cli.main(["check-decisions", "--bundle", str(bundle_path), "--responses", str(response_path),
                     "--output", str(incomplete)]) == 2
    assert json.loads((incomplete / "decision-ledger.json").read_text(encoding="utf-8"))["status"] == "incomplete"
    assert _hashes(run) == before
    assert load_decision_bundle(bundle_path).extraction == result
    summary = {"package_version": attestextract.__version__, "module_path": str(Path(attestextract.__file__).resolve()),
               "passed": True, "real_endpoint_calls": 0, "decision_model_calls": 0,
               "batch_count": 2, "candidate_count": 2, "empty_chunk_count": 1,
               "same_batch_selects": 2, "created_final_records": 0, "source_run_unchanged": True}
    _write(directory / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(run_acceptance(options.output_dir.resolve()), indent=2))

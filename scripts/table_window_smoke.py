"""Offline clean-install acceptance for bounded table truncation recovery.

Run outside the source checkout with an installed wheel. Only synthetic data
and a normalized injected backend are used; all socket and DNS access is blocked.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import attestextract
from attestextract import ExecutionOptions, cli, extract
from attestextract.errors import BackendError
from attestextract.models import BackendResponse


SOURCE = ("<table><tr><th>Sample</th><th>Mass (g)</th></tr>"
          "<tr><td>A</td><td>11</td></tr><tr><td>B</td><td>22</td></tr>"
          "<tr><td>C</td><td>33</td></tr><tr><td>D</td><td>44</td></tr></table>")
TASK = {"name": "Synthetic table mass observations", "record_types": {"mass": {
    "fields": {name: {"type": "string"} for name in ("object", "value", "unit")},
    "identity_fields": ["object"],
    "measurement_groups": {"mass": {"value_field": "value", "unit_field": "unit"}},
}}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hashes(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


class SyntheticBackend:
    name, mode, output_protocol = "synthetic-table-windows", "replay", "flat-v1"

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request.chunk_id)
        table = json.loads(request.messages[-1]["content"])["table"]
        assert table["table_source"] == SOURCE
        if "window" not in table:
            return BackendResponse(text='{"records":[', model="synthetic-fixed", finish_reason="length")
        window = table["window"]
        assert window["chunk_id"] == request.chunk_id
        owned = set(window["owned_cell_refs"])
        entries = [e for e in table["entries"] if e["kind"] == "cell"]
        header = next(e for e in entries if e["quote"] == "Mass (g)")

        def selection(entry):
            return {"ref": entry["ref"], "quote": entry["quote"], "offset": 0}

        records = []
        for anchor in entries:
            if anchor["ref"] not in owned or anchor["quote"] not in {"11", "22", "33", "44"}:
                continue
            subject = next(e for e in entries if e["row"] == anchor["row"] and e["column"] == 0)
            records.append({"record_id": f"reading-{len(records) + 1}", "record_type": "mass",
                "fields": {
                    "object": {"value": subject["quote"], "sources": [selection(subject)], "relation": "row"},
                    "value": {"value": anchor["quote"], "sources": [selection(anchor)], "relation": "anchor"},
                    "unit": {"value": "g", "sources": [selection(header)], "relation": "column"},
                }, "anchors": [selection(anchor)], "scope": "individual", "scope_sources": []})
        return BackendResponse(text=json.dumps({"records": records}), model="synthetic-fixed")


def run(directory, backend, *, budget, resume=None):
    return extract(SOURCE, TASK, backend=backend, backend_identity="synthetic-table-window-v1",
                   output_dir=directory, resume_from=resume,
                   execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=budget))


def accept(root):
    source, task = root / "source.md", root / "task.json"
    source.write_bytes(SOURCE.encode())
    write(task, TASK)
    backend = SyntheticBackend()
    first = root / "budget-stop"
    try:
        run(first, backend, budget=2)
    except BackendError as exc:
        assert exc.code == "call_budget_exhausted"
    else:
        raise AssertionError("The budget must stop before the second window")
    assert backend.calls == ["table-0001", "table-0001.1"]
    saved = read(first / "diagnostics/execution/checkpoint.json")["state"]
    assert saved["calls_used"] == 2
    assert saved["nodes"]["table-0001"]["status"] == "split"
    assert saved["nodes"]["table-0001.1"]["status"] == "completed"
    assert (first / "diagnostics/responses/table-0001.txt").read_text() == '{"records":['
    assert not (first / "diagnostics/execution/cache/table-0001.json").exists()
    first_hashes = hashes(first)

    resumed = root / "resumed"
    result = run(resumed, backend, budget=3, resume=first)
    assert backend.calls == ["table-0001", "table-0001.1", "table-0001.2"]
    assert result.schema_version == "0.6" and result.format_status == "complete"
    assert not result.candidates
    assert [table.chunk_id for table in result.table_results] == ["table-0001.1", "table-0001.2"]
    assert {table.table_id for table in result.table_results} == {"table-0001"}
    assert sorted(c["values"]["value"] for t in result.table_results for c in t.candidates) == ["11", "22", "33", "44"]
    for table in result.table_results:
        indexed = table.evidence["table_input"]
        owned = set(indexed["window"]["owned_cell_refs"])
        for entry in indexed["entries"]:
            assert SOURCE[entry["start"]:entry["end"]] == entry["quote"]
        assert all(a["ref"] in owned for c in table.candidates for a in c["anchors"])
        assert all(c["field_checks"]["value"]["literal_check"] == "passed" for c in table.candidates)
    assert read(resumed / "result.json")["schema_version"] == "0.6"
    assert read(resumed / "diagnostics/execution/checkpoint.json")["state"]["calls_used"] == 3
    report = (resumed / "report.md").read_text(encoding="utf-8")
    # Existing report literals escape punctuation; the rendered identifiers must
    # still distinguish both windows without changing that escaping contract.
    displayed = report.replace("\\-", "-").replace("\\.", ".")
    assert "table-0001.1" in displayed and "table-0001.2" in displayed
    assert (resumed / "annotation.md").is_file()
    mappings = read(resumed / "annotation.sources.json")["entries"]
    ids = [item["candidate_id"] for item in mappings]
    assert len(ids) == len(set(ids)) == 4
    assert {item.split(":")[0] for item in ids} == {"table-0001.1", "table-0001.2"}
    resumed_hashes = hashes(resumed)

    replay = run(root / "replay", backend, budget=3, resume=resumed)
    assert len(backend.calls) == 3 and replay.table_results == result.table_results
    assert hashes(first) == first_hashes and hashes(resumed) == resumed_hashes
    capture = io.StringIO()
    with redirect_stdout(capture):
        status = cli.main(["extract", "--input", str(source), "--task", str(task),
                           "--base-url", "https://synthetic.invalid/v1",
                           "--model", "synthetic-window", "--check"])
    assert status == 0
    (root / "cli-check.txt").write_text(capture.getvalue(), encoding="utf-8")
    return {"initial_calls": 2, "resume_new_calls": 1, "completed_replay_new_calls": 0,
            "model_call_ledger": 3, "table_windows": 2, "retained_observations": 4,
            "original_parent_response_retained": True, "historical_artifacts_unchanged": True,
            "child_scoped_annotation_ids_unique": True, "cli_offline_check_exit_status": status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Networking is forbidden in table window acceptance")

    environment = {k: v for k, v in os.environ.items() if not k.startswith("ATTESTEXTRACT_")
                   and k not in {"DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "VLM_API_KEY", "OPENAI_API_KEY"}}
    with patch.dict(os.environ, environment, clear=True), patch("socket.socket.connect", blocked), \
            patch("socket.socket.connect_ex", blocked), patch("socket.create_connection", blocked), \
            patch("socket.getaddrinfo", blocked):
        details = accept(root)
    assert not attempts
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": str(Path(attestextract.__file__).resolve()),
               "network_attempts": 0, "real_model_calls": 0, **details,
               "notice": "Synthetic offline acceptance, not real endpoint or scientific quality validation."}
    write(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

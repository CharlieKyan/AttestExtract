"""Context roles survive execution, decision export and local CLI preflight."""

import json

import pytest

from attestextract import ContextOptions, ExecutionOptions, cli, extract
from attestextract.decision_bundle import prepare_decision_bundle
from attestextract.diagnostics import RunTrace
from attestextract.models import BackendResponse


TASK = {"name": "Synthetic observation", "record_types": {"reading": {"fields": {"value": {"type": "string"}}}}}


def test_context_truncation_resume_and_decision_export(tmp_path):
    source = "# Sample A\n\n" + "".join(f"Observation {index} has a value.\n\n" for index in range(6))
    class Backend:
        name = "synthetic-context"
        calls = 0
        def generate(self, request):
            self.calls += 1
            return BackendResponse(text='{"records":[]}', finish_reason="length" if self.calls == 1 else "stop")
    backend = Backend()
    trace = RunTrace(tmp_path / "first" / "diagnostics")
    options = ExecutionOptions(adaptive_chunking=False, max_model_calls=30, min_chunk_chars=10)
    context = ContextOptions(mode="neighbors", neighbor_chars=30)
    result = extract(source, TASK, backend=backend, context=context, execution=options,
                     trace=trace, backend_identity="synthetic", max_chunk_chars=1000)
    trace.finish(result.model_dump(mode="json"))
    assert result.run.chunk_count > 1
    bundle = prepare_decision_bundle(tmp_path / "first")
    assert len(bundle.batches) == result.run.chunk_count
    calls = backend.calls
    resumed_trace = RunTrace(tmp_path / "second" / "diagnostics")
    resumed = extract(source, TASK, backend=backend, context=context, execution=options,
                      trace=resumed_trace, backend_identity="synthetic", max_chunk_chars=1000,
                      resume_from=tmp_path / "first")
    assert backend.calls == calls
    assert resumed.run.chunk_count == result.run.chunk_count
    resumed_trace.finish(resumed.model_dump(mode="json"))
    assert len(prepare_decision_bundle(tmp_path / "second").batches) == len(bundle.batches)


def test_cli_context_mineru_preflight_and_protected_sidecar(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    table = "<table><tr><td>Sample</td><td>MPa</td></tr><tr><td>A</td><td>12</td></tr></table>"
    source.write_text("# Test\n\n" + table, encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(json.dumps(TASK), encoding="utf-8")
    mineru = tmp_path / "content_list.json"
    mineru.write_text(json.dumps([{"type": "table", "table_body": table, "page_idx": 0}]), encoding="utf-8")
    hints = tmp_path / "hints.json"
    hints.write_text(json.dumps({"tables": {"table-0001": {"header_rows": [0], "row_header_columns": [0]}}}), encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "synthetic")
    args = ["extract", "--input", str(source), "--task", str(task), "--context", "neighbors", "--check",
            "--mineru-content-list", str(mineru), "--structure-hints", str(hints)]
    assert cli.main([*args, "--output", str(tmp_path / "result.json")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_calls"] == 0
    assert payload["coverage"]["uncovered"] == []
    assert payload["context"]["mode"] == "neighbors"
    before = hints.read_bytes()
    assert cli.main([*args, "--output", str(hints)]) == 1
    assert hints.read_bytes() == before

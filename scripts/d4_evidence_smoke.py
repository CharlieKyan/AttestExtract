"""Installed-package D4 acceptance using only synthetic source and responses.

No credentials, HTTP mocks, or real endpoints are used. The recovery case
injects BackendResponse objects through an in-memory synthetic backend.
"""

import argparse
import hashlib
import json
from pathlib import Path

import attestextract
from attestextract import (
    ContextOptions, Document, ExecutionOptions, create_archive, extract,
    load_archive, save_archive,
)
from attestextract.archive import CandidateReview
from attestextract.backends import ReplayBackend
from attestextract.chunking import chunk_document
from attestextract.contextual_chunking import coverage_report
from attestextract.diagnostics import RunTrace
from attestextract.models import BackendResponse
from attestextract.structure import build_structure


SIMPLE_TASK = {"name": "Synthetic D4 reading", "record_types": {"reading": {
    "fields": {"value": {"type": "string", "required": True}},
}}}
TABLE_TASK = {"name": "Synthetic D4 cell binding", "record_types": {"reading": {
    "fields": {"value": {"type": "string"}, "unit": {"type": "string"}},
    "measurement_groups": {"distance": {"value_field": "value", "unit_field": "unit"}},
}}}


def record(value, quote):
    return {"record_type": "reading", "values": {"value": value}, "quotes": {"value": quote}}


def roundtrip(document, result, path):
    archive = create_archive(document, result)
    assert archive.records == [], "Location alone must not accept a scientific fact"
    save_archive(archive, path)
    assert load_archive(path) == archive


def normalized_case(root):
    source = "🙂 Preface\r\n距离：7\t  cm。"
    document = Document.from_text(source)
    response = {"records": [record("7", "距离:7 cm.")]}
    result = extract(document, SIMPLE_TASK, backend=ReplayBackend([response]))
    candidate = result.candidates[0]
    evidence = candidate.fields["value"].evidence
    assert evidence.status == "normalized" and evidence.quote == "距离:7 cm."
    match = evidence.source_matches[0]
    assert match.source_quote == "距离：7\t  cm。"
    assert source[match.span.start:match.span.end] == match.source_quote
    assert evidence.matches == [] and candidate.needs_review
    assert any(item.code == "evidence_normalized" for item in result.review)
    roundtrip(document, result, root / "normalized.archive.json")
    review = CandidateReview(candidate_id=candidate.candidate_id, status="accepted",
                             origin="synthetic_fixture", reviewer="smoke", rationale="Synthetic check")
    try:
        create_archive(document, result, [review])
    except ValueError:
        pass
    else:
        raise AssertionError("Normalized evidence must not become accepted exact evidence")


def table_case(root, name, source, unit_quote):
    document = Document.from_text(source, structure=build_structure(source))
    raw = {"records": [{"record_type": "reading", "values": {"value": "7", "unit": "cm"},
                        "quotes": {"value": "7", "unit": unit_quote},
                        "measurements": {"distance": {"context_quote": "7", "value_text": "7",
                                                          "unit_text": "cm", "unit_quote": unit_quote}}}]}
    result = extract(document, TABLE_TASK, backend=ReplayBackend([raw]))
    measurement = result.candidates[0].measurements["distance"]
    assert measurement.issues == []
    assert measurement.value.table_refs[0].column == 1
    header = measurement.unit.table_refs[0]
    assert header.column == 1 and header.role == "header"
    assert measurement.value.status == measurement.unit.status == "matched"
    for evidence in (measurement.value, measurement.unit):
        assert source[evidence.start:evidence.end] == evidence.quote
    roundtrip(document, result, root / f"{name}.archive.json")


class RecoveryBackend:
    name = "synthetic-d4-recovery"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            return BackendResponse(text='{"records":[]}', finish_reason="length")
        payload = json.loads(request.messages[-1]["content"])
        source = payload["source"]
        assert isinstance(source, list), "Enabled context must retain source roles"
        owned = "".join(part["text"] for part in source if part["role"] == "body")
        # Two hand-specified source facts; auxiliary passages never create records.
        expected = [("7", "Length 7 cm."), ("9", "Length 9 cm.")]
        records = [record(value, quote) for value, quote in expected if quote in owned]
        return BackendResponse(text=json.dumps({"records": records}), finish_reason="stop")


def file_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def recovery_case(root):
    source = "# Sample Q\n\nAt 18 C.\n\nLength 7 cm.\n\nLength 9 cm.\n"
    structure = build_structure(source)
    document = Document.from_text(source, structure=structure)
    for mode in ("structure", "neighbors"):
        chunks = chunk_document(source, structure=structure, max_chars=20,
                                context=ContextOptions(mode=mode, neighbor_chars=40))
        coverage = coverage_report(source, chunks)
        assert coverage["uncovered"] == [] and coverage["repeated_body_characters"] == 0
        assert any(part.role == "heading" for chunk in chunks for part in chunk.context_segments)
        for chunk in chunks:
            assert chunk.text == "".join(source[span.start:span.end] for span in chunk.segments)
    options = ExecutionOptions(adaptive_chunking=False, max_model_calls=16, min_chunk_chars=8)
    context = ContextOptions(mode="neighbors", neighbor_chars=40)
    backend = RecoveryBackend()
    first = root / "first" / "diagnostics"
    trace = RunTrace(first)
    result = extract(document, SIMPLE_TASK, backend=backend, context=context, execution=options,
                     trace=trace, backend_identity="synthetic-d4", max_chunk_chars=1000)
    trace.finish(result.model_dump(mode="json"))
    assert sorted(candidate.fields["value"].value for candidate in result.candidates) == ["7", "9"]
    assert result.run.chunk_count > 1 and backend.calls > 1
    assert any(candidate.source_context for candidate in result.candidates)
    for candidate in result.candidates:
        for passage in candidate.source_context:
            assert source[passage.span.start:passage.span.end] == passage.quote
    roundtrip(document, result, root / "context.archive.json")
    before, calls = file_hashes(first), backend.calls
    second = RunTrace(root / "second" / "diagnostics")
    resumed = extract(document, SIMPLE_TASK, backend=backend, context=context, execution=options,
                      trace=second, backend_identity="synthetic-d4", max_chunk_chars=1000,
                      resume_from=first)
    second.finish(resumed.model_dump(mode="json"))
    assert backend.calls == calls and resumed.candidates == result.candidates
    assert file_hashes(first) == before
    return calls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    normalized_case(root)
    table_case(root, "html", '<table><tr><th>Sample</th><th colspan="2">Distance (cm)</th></tr>'
               '<tr><td>Q</td><td>7</td><td>9</td></tr></table>', "Distance (cm)")
    table_case(root, "escaped-pipe", r"| Sample \| ID | Distance (cm) | Temperature (K) |" + "\n"
               "| --- | --- | --- |\n" + r"| Q \| R | 7 | 290 |", "Distance (cm)")
    calls = recovery_case(root)
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": str(Path(attestextract.__file__).resolve()), "real_endpoint_calls": 0,
               "mock_http_calls": 0, "offline_replay_calls": 3, "synthetic_backend_generate_calls": calls,
               "resume_additional_calls": 0, "cases": ["normalized-review", "html-binding",
                                                          "escaped-pipe-binding", "context-truncation-resume"]}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

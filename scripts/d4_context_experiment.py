"""Four-arm D4 context experiment; offline is the default and makes no calls.

The live command executes only a previously frozen request set, once. The
synthetic probes measure source access and ambiguity, never model accuracy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ARMS = ("dev18", "none", "structure", "neighbors")
BASELINE = "a95bf81"
TASK = {"name": "D4 context ownership experiment", "description": (
    "Extract measurements whose value occurs in the target body. Preserve the "
    "reported object, parameter, value, unit and condition. Context may explain "
    "body measurements, but do not extract a separate context-only measurement. "
    "Keep distinct conditions separate; use null for unreported information. "
    "Preserve numbers as source strings without unit conversion."), "record_types": {
    "measurement": {"fields": {name: {"type": "string", "required": name != "condition"}
                                  for name in ("object", "parameter", "value", "unit", "condition")},
                    "identity_fields": ["object", "parameter"],
                    "condition_fields": ["condition"]}}}
BACKEND = {"adapter": "deepseek-responses", "base_url": "https://api.deepseek.com",
           "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY", "timeout": 60.0,
           "max_attempts": 1, "structured_output": "json_schema", "format_retries": 0,
           "tls_mode": "1.2", "output_token_parameter": "max_output_tokens",
           "generation": {"temperature": 0.0, "max_output_tokens": 4096},
           "provider_options": {"reasoning": {"effort": "none"}}}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read_json(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def synthetic_cases() -> list[dict]:
    """Independent small source examples; probes are specified before execution."""
    definitions = [
        ("heading_object", "# Sample A\n\nDescription of the sample.\n\nDensity was 12 kg.\n",
         "Density was 12 kg.\n", [("object", "Sample A", "useful")]),
        ("neighbor_condition", "# Measurements\n\nSample A was tested at 80 C.\n\nDensity was 12 kg.\n",
         "Density was 12 kg.\n", [("condition", "80 C", "useful")]),
        ("table_unit", "# Table\n\n| Sample | Mass (kg) |\n| --- | --- |\n| A | 12 |\n| B | 14 |\n",
         "| B | 14 |\n", [("unit", "kg", "useful")]),
        ("other_object", "# Measurements\n\nSample B was tested at 90 C.\n\nSample A density was 12 kg.\n",
         "Sample A density was 12 kg.\n", [("other_object", "90 C", "risk")]),
        ("repeated_value", "# Measurements\n\nSample B mass was 12 kg.\n\nSample A mass was 12 kg.\n",
         "Sample A mass was 12 kg.\n", [("repeated_value", "12", "risk")]),
        ("section_boundary", "# Sample B\n\nAt 90 C, mass was 77 kg.\n\n# Sample A\n\nMass was 12 kg.\n",
         "Mass was 12 kg.\n", [("other_section", "77", "forbidden")]),
        ("context_only", "# Measurements\n\nSample B mass was 77 kg.\n\nSample A mass was 12 kg.\n",
         "Sample A mass was 12 kg.\n", [("context_only_value", "77", "context_only")]),
    ]
    cases = []
    for name, source, body, probes in definitions:
        start = source.index(body)
        cases.append({"id": name, "source": source, "target": {"start": start, "end": start + len(body)},
                      "max_chars": 45, "probes": [{"name": n, "quote": q, "kind": k} for n, q, k in probes],
                      "task": TASK, "gold": []})
    return cases


def freeze_baseline(repo: Path, output: Path, commit: str = BASELINE) -> None:
    """Copy tracked Python source through read-only Git operations, never a worktree."""
    resolved = subprocess.check_output(["git", "-C", str(repo), "rev-parse", commit], text=True).strip()
    names = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", resolved,
                                     "src/attestextract"], text=True).splitlines()
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in names:
        if not name.endswith(".py"):
            continue
        data = subprocess.check_output(["git", "-C", str(repo), "show", f"{resolved}:{name}"])
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = digest(data)
    write_json(output / "baseline-manifest.json", {"commit": resolved, "files": hashes})


def worker(case: dict, arm: str) -> dict:
    import attestextract
    from attestextract.chunking import chunk_document
    from attestextract.evidence import ground_quote
    from attestextract.models import DocumentChunk, SourceSpan, TaskSpec, GenerationOptions
    from attestextract.pipeline import _request

    source = case["source"]
    body = SourceSpan(**case["target"])
    chunk = DocumentChunk(chunk_id=case["id"], text=source[body.start:body.end], segments=[body])
    kwargs = {}
    if arm in {"structure", "neighbors"}:
        from attestextract.models import ContextOptions
        from attestextract.structure import build_structure
        from attestextract.contextual_chunking import enrich_chunk
        structure = build_structure(source, mineru_content=case.get("mineru_content"), hints=case.get("hints"))
        context = ContextOptions(mode=arm, neighbor_chars=500)
        chunk = enrich_chunk(source, chunk, structure, context)
        kwargs = {"structure": structure, "context": context}
    natural = chunk_document(source, max_chars=case.get("max_chars", 500), **kwargs)
    coverage = bytearray(len(source))
    for piece in natural:
        for span in getattr(piece, "body_segments", None) or piece.segments:
            coverage[span.start:span.end] = b"\x01" * (span.end - span.start)
    probes = []
    for probe in case.get("probes", []):
        located = ground_quote(source, chunk, probe["quote"])
        body_hits = [match for match in located.matches if body.start <= match.start and match.end <= body.end]
        probes.append({**probe, "status": located.status, "matches": len(located.matches),
                       "body_matches": len(body_hits), "context_only": bool(located.matches) and not body_hits})
    request = _request(TaskSpec.model_validate(case["task"]), chunk, GenerationOptions(**BACKEND["generation"]))
    return {"arm": arm, "case": case["id"], "version": attestextract.__version__,
            "body": case["target"], "chunk": chunk.model_dump(mode="json"),
            "request": request.model_dump(mode="json"), "probes": probes,
            "natural_chunk_count": len(natural), "uncovered_chars": coverage.count(0),
            "natural_input_chars": sum(len(piece.text) for piece in natural),
            "target_input_chars": len(chunk.text), "source_chars": len(source)}


def isolated_worker(case: dict, arm: str, package_source: Path) -> dict:
    environment = dict(os.environ, PYTHONPATH=str(package_source.resolve()), PYTHONIOENCODING="utf-8")
    completed = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "worker", "--arm", arm],
                               input=json.dumps(case, ensure_ascii=False), capture_output=True,
                               text=True, encoding="utf-8", env=environment)
    if completed.returncode:
        raise RuntimeError(f"{case['id']}/{arm} preparation failed: {completed.stderr}")
    return json.loads(completed.stdout)


def prepare(cases: list[dict], output: Path, baseline_source: Path, current_source: Path,
            *, env_file: str | None = None, private: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for case in cases:
        start, end = case["target"]["start"], case["target"]["end"]
        if not 0 <= start < end <= len(case["source"]):
            raise ValueError("Target must be a nonempty span in the frozen source")
        for arm in ARMS:
            rows.append(isolated_worker(case, arm, baseline_source if arm == "dev18" else current_source))
    write_json(output / "cases.json", cases)
    write_json(output / "backend.json", BACKEND)
    with (output / "planned-requests.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    comparisons = []
    for case in cases:
        selected = [row for row in rows if row["case"] == case["id"]]
        base = selected[0]
        comparisons.append({"case": case["id"], "same_body_all_arms": all(r["body"] == base["body"] for r in selected),
                            "dev18_none_request_identical": base["request"] == selected[1]["request"],
                            "source_coverage_all_arms": all(r["uncovered_chars"] == 0 for r in selected)})
    manifest = {"format_version": 1, "private": private, "real_endpoint_calls": 0,
                "scope": "Fixed body source-access experiment; no model accuracy inferred from offline probes.",
                "gold_origin": "Agent manual source check, not expert-reviewed gold",
                "arms": list(ARMS), "cases": len(cases), "planned_calls": len(rows),
                "max_output_tokens_total": len(rows) * 4096, "env_file": env_file,
                "comparisons": comparisons,
                "files": {name: digest((output / name).read_bytes()) for name in
                          ("cases.json", "backend.json", "planned-requests.jsonl")}}
    write_json(output / "manifest.json", manifest)
    return manifest


def check_frozen(root: Path) -> dict:
    manifest = read_json(root / "manifest.json")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or digest((root / name).read_bytes()) != expected:
            raise ValueError("Frozen experiment material changed; create a new experiment")
    return manifest


def score_records(records: list[dict], gold: list[dict]) -> dict:
    """Exact tuple scoring; alternative spellings require separate manual review."""
    names = ("object", "parameter", "value", "unit", "condition")
    def key(value):
        return tuple(value.get(name) for name in names)
    expected = Counter(key(row) for row in gold)
    actual = Counter(key(row["values"]) for row in records)
    correct = sum((expected & actual).values())
    extras = list((actual - expected).elements())
    ownership = sum(any(x[1:4] == g[1:4] and (x[0], x[4]) != (g[0], g[4]) for g in expected) for x in extras)
    wrong_value = sum(any((x[0], x[1], x[4]) == (g[0], g[1], g[4]) and x[2:4] != g[2:4]
                          for g in expected) for x in extras)
    return {"complete_correct_tuples": correct, "expected_tuples": sum(expected.values()),
            "object_or_condition_errors": ownership, "wrong_value_or_unit": wrong_value,
            "missing_tuples": sum((expected - actual).values()),
            "duplicates": sum(max(0, count - 1) for count in actual.values()),
            "unexpected_tuples": len(extras), "manual_semantic_review": "pending"}


def review_source(raw, row: dict, source: str) -> dict:
    from attestextract.evidence import ground_quote
    from attestextract.models import DocumentChunk
    # Old chunks are supported by the current contract; source checks below
    # are common post-hoc checks and are not attributed to the old engine.
    chunk = DocumentChunk.model_validate(row["chunk"])
    body = row["body"]
    unresolved = 0
    context_only = 0
    for record in raw.records:
        for field in record.fields.values():
            if field.value is not None:
                located = ground_quote(source, chunk, field.quote)
                unresolved += located.status != "matched"
        value = record.fields.get("value")
        if value is not None:
            anchor = ground_quote(source, chunk, value.quote)
            if anchor.matches and not any(body["start"] <= span.start and span.end <= body["end"]
                                          for span in anchor.matches):
                context_only += 1
    return {"unresolved_field_quotes": unresolved, "context_only_value_quotes": context_only,
            "scope": "Common post-hoc locator; not a semantic ownership verifier"}


def run_live(root: Path, output: Path) -> dict:
    import attestextract
    from attestextract.config import BackendConfig
    from attestextract.diagnostics import RunTrace
    from attestextract.envconfig import load_env_file
    from attestextract.errors import BackendError
    from attestextract.models import ExtractionRequest, TaskSpec
    from attestextract.pipeline import _parse_response, _check_task_records
    from attestextract.registry import default_registry

    manifest = check_frozen(root)
    if not manifest["private"] or manifest["planned_calls"] != 12:
        raise ValueError("Live pilot requires a frozen private 3-case/12-request bundle")
    config = BackendConfig.model_validate(read_json(root / "backend.json"))
    if config.model_dump(mode="json") != BackendConfig.model_validate(BACKEND).model_dump(mode="json"):
        raise ValueError("Pilot backend budget differs from the reviewed configuration")
    environment = load_env_file(manifest["env_file"])
    credential = environment.get(config.api_key_env)
    if not credential:
        raise ValueError("Configured API credential is missing")
    rows = [json.loads(line) for line in (root / "planned-requests.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(rows) != 12 or any(row["request"]["generation"] != BACKEND["generation"] for row in rows):
        raise ValueError("Pilot requests do not match the fixed budget")
    # One fixed result directory is a call ledger. A second invocation cannot
    # repeat requests, including after interruption; no automatic resume.
    output.mkdir(parents=True, exist_ok=False)
    write_json(root / "live-started.json", {"result_directory": str(output.resolve()), "max_calls": 12})
    cases = {case["id"]: case for case in read_json(root / "cases.json")}
    results = []
    for index, row in enumerate(rows):
        stem = f"{index + 1:02d}-{row['case']}-{row['arm']}"
        write_json(output / f"{stem}.reserved.json", {"case": row["case"], "arm": row["arm"], "calls": 1})
        result = {"case": row["case"], "arm": row["arm"], "calls": 1}
        trace = RunTrace(output / f"{stem}.diagnostics", secrets=[credential])
        stop_after_request = False
        try:
            backend = default_registry().create(config.adapter, config=config, api_key=credential, trace=trace)
            response = backend.generate(ExtractionRequest.model_validate(row["request"]))
            write_json(output / f"{stem}.response.json", response.model_dump(mode="json"))
            result.update(usage=response.usage, elapsed_seconds=response.elapsed_seconds)
            raw = _parse_response(response.text)
            _check_task_records(raw, TaskSpec.model_validate(cases[row["case"]]["task"]),
                                chunk_id=row["case"], trace=None)
            flat = [{"values": {name: field.value for name, field in record.fields.items()}} for record in raw.records]
            result.update(status="completed", scores=score_records(flat, cases[row["case"]]["gold"]),
                          review=review_source(raw, row, cases[row["case"]]["source"]))
            trace.finish({"package_version": attestextract.__version__, "run": result})
        except BackendError as error:
            result.update(status="failed", error_code=error.code, attempts=error.attempts,
                          http_diagnostics=error.http_diagnostics)
            metadata = getattr(error, "response_metadata", None)
            if metadata is not None:
                result["response_metadata"] = metadata
                for key in ("usage", "elapsed_seconds", "finish_reason"):
                    if key in metadata:
                        result[key] = metadata[key]
            trace.fail(error, stage="experiment_response")
            # Configuration/account errors end the pilot, without fallback.
            if error.code in {"authentication_error", "billing_error", "permission_denied", "not_found", "invalid_request"}:
                stop_after_request = True
        results.append(result)
        write_json(output / f"{stem}.score.json", result)
        if stop_after_request:
            break
    summary = {"results": results, "reserved_calls": len(results),
               "claim": "Small descriptive pilot; no population-level accuracy conclusion.",
               "currency_cost": "not_estimated; use provider billing", "manual_review": "pending"}
    write_json(output / "summary.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("offline", "prepare", "freeze-baseline", "worker", "live"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--current-source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--arm", choices=ARMS)
    args = parser.parse_args(argv)
    if args.command == "worker":
        print(json.dumps(worker(json.load(sys.stdin), args.arm), ensure_ascii=False))
        return 0
    if args.output is None:
        parser.error("--output is required")
    if args.command == "freeze-baseline":
        freeze_baseline(args.repo, args.output)
    elif args.command == "live":
        if args.bundle is None:
            parser.error("live requires --bundle")
        run_live(args.bundle, args.output)
    else:
        if args.baseline_source is None:
            parser.error("preparation requires --baseline-source")
        if args.command == "prepare" and (args.bundle is None or args.env_file is None):
            parser.error("private preparation requires --bundle (case list) and --env-file")
        cases = synthetic_cases() if args.command == "offline" else read_json(args.bundle)
        prepare(cases, args.output, args.baseline_source, args.current_source,
                env_file=str(args.env_file.resolve()) if args.env_file else None, private=args.command == "prepare")
    print(json.dumps({"output": str(args.output.resolve()), "executed_live": args.command == "live"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

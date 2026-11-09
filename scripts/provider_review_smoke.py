"""Installed, synthetic acceptance of review-driven experiment selection.

Copy this script and provider_experiment*.py from the source distribution next
to each other outside the checkout. No credentials, papers or real APIs are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import httpx

import attestextract
from attestextract.errors import ConfigurationError
from provider_experiment_bundle import load_bundle, prepare_bundle
from provider_experiment_runner import (attach_review, inspect_experiment,
                                        load_results, read_record, run_stage)
from provider_experiment_smoke import Clock, _factory


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_factory(data, observed, clock):
    """Explicit synthetic model output: same sample, category prefix omitted."""
    original_factory = _factory(data, observed, clock)

    def factory(adapter, *, config, api_key, trace):
        backend = original_factory(adapter, config=config, api_key=api_key, trace=trace)
        transport = backend._transport

        def endpoint(request):
            response = transport.handle_request(request)
            payload = response.json()
            responses = request.url.path.endswith("/responses")
            text = (payload["output"][0]["content"][0]["text"] if responses
                    else payload["choices"][0]["message"]["content"])
            records = json.loads(text)
            for record in records["records"]:
                if record["record_type"] == "flow_observation":
                    record["values"]["sample"] = "青禾"
            output = json.dumps(records, ensure_ascii=False)
            if responses:
                payload["output"][0]["content"][0]["text"] = output
            else:
                payload["choices"][0]["message"]["content"] = output
            return httpx.Response(200, json=payload)

        backend._transport = httpx.MockTransport(endpoint)
        return backend

    return factory


def prepare_synthetic(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    source, task = root / "paper.md", root / "task.json"
    text = "\n\n".join(f"Item {i:02} reads {101 + i}. " + "Synthetic background. " * 170
                         for i in range(16))
    source.write_bytes(text.encode("utf-8"))
    task.write_text(json.dumps({"name": "Synthetic review acceptance", "record_types": {
        "reading": {"fields": {"value": {"type": "integer", "required": True}}}}}), encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "synthetic-deepseek", "VLM_API_KEY": "synthetic-qwen",
                   "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    bundle = root / "bundle"
    prepare_bundle(bundle, source, task, environment)
    return bundle, environment, load_bundle(bundle), Clock()


def execute(root, environment, stage, clock, factory, round_number=None):
    return run_stage(root, environment, stage, round_number, backend_factory=factory,
                     clock=clock, sleep=clock.sleep, progress=lambda _: None)


def complete_abc(root, environment, data, clock):
    observed = []
    factory = review_factory(data, observed, clock)
    execute(root, environment, "A", clock, factory)
    execute(root, environment, "B", clock, factory, 1)
    execute(root, environment, "B", clock, factory, 2)
    clock.sleep(1800)
    execute(root, environment, "B", clock, factory, 3)
    summary = execute(root, environment, "C", clock, factory)
    assert len(observed) == 76
    assert all(value["selected"] is None for value in summary["protocol_selection"].values())
    return observed, factory


def make_review(root, data):
    """Hand-specified fixture verdicts, with independent raw-result binding."""
    findings = []
    for row in data["rows"]:
        if row["stage"] != "C":
            continue
        directory = Path(root) / "results/trials" / row["id"]
        result_path = directory / "result.json"
        response_path = directory / "diagnostics/unified-response.json"
        result = read_record(result_path)
        actual = json.loads(json.loads(response_path.read_text(encoding="utf-8"))["text"])["records"]
        expected = data["cases"][row["case_id"]]["gold"]
        assert len(actual) == len(expected)
        differences = []
        fields = 0
        for index, (record, gold) in enumerate(zip(actual, expected)):
            assert record["record_type"] == gold["record_type"]
            assert set(record["values"]) == set(record["quotes"]) == set(gold["fields"])
            for name, field in gold["fields"].items():
                fields += 1
                assert record["quotes"][name] == field["quote"]
                value = record["values"][name]
                if value != field["value"]:
                    assert name == "sample" and value == "青禾" and field["value"] == "试样青禾"
                    differences.append({"record_index": index, "field": name,
                        "model_value": value, "gold_value": field["value"],
                        "judgment": "equivalent_in_this_source",
                        "reason": "Synthetic reviewer: the same named sample, with its category prefix omitted."})
        findings.append({"trial_id": row["id"], "path_id": row["path_id"],
            "case_id": row["case_id"], "round": row["round"],
            "original_result_file": result_path.relative_to(root).as_posix(),
            "original_result_sha256": sha256(result_path),
            "response_file": response_path.relative_to(root).as_posix(),
            "response_sha256": sha256(response_path),
            "original_strict_content_passed": result["scores"]["content_passed"],
            "original_strict_evidence_passed": result["scores"]["evidence_passed"],
            "assistant_semantic_review": "pass_for_this_synthetic_case",
            "candidate_count": len(actual), "quote_fields_exact_and_condition_aligned": fields,
            "name_boundary_findings": differences,
            "reason": "Synthetic review only: stated values, separate conditions and exact source quotations are retained."})
    return {"review_version": "0.1", "origin": "assistant_semantic_review",
            "bundle_id": data["manifest"]["bundle_id"], "expert_gold": False,
            "reference_checksums": {name: sha256(Path(root) / name) for name in
                ("sources.json", "tasks.json", "gold.json", "rows.json", "requests.json")},
            "rubric_basis": "Explicit synthetic acceptance verdicts, not model or expert accuracy evidence.",
            "findings": findings}


def historical_snapshot(root):
    """Freeze complete raw trial and A-paper artifacts, excluding derived reports."""
    root = Path(root)
    paths = list((root / "results/trials").rglob("*"))
    paths += list((root / "results/papers/A-qwen").rglob("*"))
    paths += [root / "manifest.json"]
    return {p.relative_to(root).as_posix(): sha256(p) for p in paths if p.is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ConfigurationError("Choose a new acceptance directory outside the script checkout.")
    bundle, environment, data, clock = prepare_synthetic(root)
    observed, factory = complete_abc(bundle, environment, data, clock)
    before = historical_snapshot(bundle)
    review_path = root / "synthetic-review.json"
    review_path.write_text(json.dumps(make_review(bundle, data), ensure_ascii=False, indent=2), encoding="utf-8")

    attached = attach_review(bundle, review_path)
    assert attached["reserved_calls"] == 76 and attached["remaining_call_budget"] == 32
    assert attached["protocol_selection"]["qwen"]["selected"] == "qwen-chat-object"
    assert attached["protocol_selection"]["deepseek"]["selected"] == "deepseek-chat-object"
    assert all(v["selected"] is None for v in attached["strict_protocol_selection"].values())
    assert len(observed) == 76
    assert inspect_experiment(bundle)["reserved_calls"] == 76
    assert len(observed) == 76
    final = execute(bundle, environment, "D", clock, factory)
    assert len(observed) == 108 and final["counts"]["reserved"] == 108
    assert final["counts"]["completed"] == 108
    assert final["protocol_selection_basis"] == "semantic_review"
    assert all(item["passed"] for item in final["full_runs"])
    for name, digest in before.items():
        assert sha256(bundle / name) == digest, name
    results = load_results(bundle / "results", data["rows"])
    assert all(results[row["id"]]["scores"]["content_passed"] is False
               for row in data["rows"] if row["stage"] == "C" and row["case_id"] == "d1-observation")
    for provider in ("qwen", "deepseek"):
        paper = json.loads((bundle / "results/papers" / f"D-{provider}" / "result.json").read_text(encoding="utf-8"))
        assert [c["fields"]["value"]["value"] for c in paper["candidates"]] == list(range(101, 117))
        outcome = read_record(bundle / "results/papers" / f"D-{provider}" / "outcome.json")
        assert outcome["fresh"] and outcome["reused_chunks"] == 0
    execute(bundle, environment, "D", clock, factory)
    assert len(observed) == 108
    assert not any(name == "langextract" or name.startswith("langextract.") for name in sys.modules)
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "real_endpoint_calls": 0,
               "mock_network_calls": 108, "attach_review_new_calls": 0,
               "D_new_calls": 32, "repeat_D_new_calls": 0,
               "historical_artifacts_unchanged": True, "strict_scores_preserved": True,
               "fresh_D_papers_completed": 2, "langextract_imported": False,
               "scope": "Synthetic review selection and installed execution only; no semantic-model quality claim."}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

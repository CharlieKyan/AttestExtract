"""Installed-package acceptance for the 108-trial provider experiment.

Copy provider_experiment*.py together outside the checkout and run this script
using an independently installed wheel. Only synthetic source, credentials and
MockTransport responses are used. No connection or model quality is claimed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import httpx

import attestextract
from attestextract import Document, create_archive
from attestextract.errors import ConfigurationError
from attestextract.models import ExtractionResult
from attestextract.registry import default_registry

from provider_experiment_bundle import load_bundle, prepare_bundle
from provider_experiment_runner import load_results, read_record, run_stage


class Clock:
    """Advance experiment windows without a thirty-minute installation wait."""

    def __init__(self):
        self.now = 1800000000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _factory(data, observed, clock):
    def endpoint(request):
        assert request.method == "POST", "Custom transport must not claim TLS probes."
        body = json.loads(request.content)
        observed.append({"path": request.url.path, "body": body})
        clock.sleep(0.5)
        responses = request.url.path.endswith("/responses")
        messages = body["input"] if responses else body["messages"]
        content = messages[-1]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        source = json.loads(content)["source"]
        fixture = next((case for key, case in data["cases"].items()
                        if key.startswith("d1-") and case["source"] == source), None)
        if fixture is not None:
            records = [{"record_type": record["record_type"],
                        "values": {name: field["value"] for name, field in record["fields"].items()},
                        "quotes": {name: field["quote"] for name, field in record["fields"].items()}}
                       for record in fixture["gold"]]
        else:
            match = re.match(r"Item ([0-9]{2}) reads ([0-9]+)\.", source)
            assert match is not None, "One intact synthetic paragraph is expected per paper chunk."
            records = [{"record_type": "reading", "values": {"value": int(match[2])},
                        "quotes": {"value": match[0]}}]
        output = json.dumps({"records": records}, ensure_ascii=False)
        if responses:
            payload = {"object": "response", "status": "completed", "model": body["model"],
                       "output": [{"type": "message", "role": "assistant", "status": "completed",
                                   "content": [{"type": "output_text", "text": output}]}],
                       "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}
        else:
            payload = {"model": body["model"],
                       "choices": [{"finish_reason": "stop", "message": {"content": output}}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
        return httpx.Response(200, json=payload)

    def factory(adapter, *, config, api_key, trace):
        backend = default_registry().create(adapter, config=config, api_key=api_key, trace=trace)
        backend._transport = httpx.MockTransport(endpoint)
        return backend

    return factory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    root = parser.parse_args(argv).output_dir.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ConfigurationError("Choose a new acceptance output directory outside the script checkout.")
    root.mkdir(parents=True, exist_ok=False)
    source, task = root / "paper.md", root / "task.json"
    # Each paragraph fits the normal planner; two do not fit. Distinct values
    # make the expected order independent of the implementation's chunk IDs.
    text = "\n\n".join(f"Item {i:02} reads {101 + i}. " + "Synthetic background. " * 170
                        for i in range(16))
    source.write_bytes(text.encode("utf-8"))
    task.write_text(json.dumps({"name": "Synthetic installed experiment", "record_types": {
        "reading": {"fields": {"value": {"type": "integer", "required": True}}}}}), encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "synthetic-deepseek", "VLM_API_KEY": "synthetic-qwen",
                   "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    bundle_path = root / "bundle"
    prepare_bundle(bundle_path, source, task, environment)
    data = load_bundle(bundle_path, environment)
    assert len(data["rows"]) == 108
    assert data["manifest"]["expected_paper_chunks"] == 16
    observed, clock = [], Clock()
    factory = _factory(data, observed, clock)

    def run(stage, round_number=None):
        return run_stage(bundle_path, environment, stage, round_number,
                         backend_factory=factory, clock=clock, sleep=clock.sleep,
                         progress=lambda _: None)

    run("A")
    assert len(observed) == 16
    run("B", 1)
    run("B", 2)
    before = len(observed)
    assert before == 40
    try:
        run("B", 3)
    except ConfigurationError as error:
        assert "not due" in str(error)
    else:
        raise AssertionError("The third window must not execute early.")
    assert len(observed) == before
    clock.sleep(1800)
    run("B", 3)
    assert len(observed) == 52
    run("C")
    assert len(observed) == 76
    final = run("D")
    assert len(observed) == 108
    results = load_results(bundle_path / "results", data["rows"])
    assert len(results) == 108
    assert final["counts"]["reserved"] == final["counts"]["model_post_attempts"] == 108
    assert final["counts"]["completed"] == final["counts"]["http_success"] == 108
    assert final["counts"]["actual_tls_versions"] == {"unknown": 108}
    assert final["counts"]["probe_requests"] == 0
    assert all(item["passed"] for item in final["full_runs"])

    d1_count = 0
    for row in data["rows"]:
        result = results[row["id"]]
        scores = result["scores"]
        assert scores["format_passed"] is True and scores["evidence_passed"] is True
        if row["case_id"].startswith("d1-"):
            d1_count += 1
            assert scores["content_passed"] is True
            assert scores["matching_records"] == len(data["cases"][row["case_id"]]["gold"])
            assert scores["missing_records"] == scores["unexpected_records"] == 0
            assert scores["fixture_supported_fields"] == scores["evidence_fields"]
        else:
            assert scores["content_passed"] is None
        observation = result["connection_observation"]
        assert observation["actual_tls_version"] == "unknown"
        assert observation["custom_transport"] is True

    assert d1_count == 42  # Eighteen B short trials plus twenty-four C trials.
    fresh_results = []
    for group in ("A-qwen", "D-qwen", "D-deepseek"):
        directory = bundle_path / "results/papers" / group
        result = ExtractionResult.model_validate(_read(directory / "result.json"))
        outcome = read_record(directory / "outcome.json")
        assert outcome["status"] == "completed" and outcome["fresh"] is True
        assert outcome["completed_chunks"] == 16 and outcome["reused_chunks"] == 0
        assert result.schema_version == "0.4"
        assert [candidate.fields["value"].value for candidate in result.candidates] == list(range(101, 117))
        document = Document.from_text(text, source_name=source.name, representation="text/markdown")
        assert create_archive(document, result).records == []
        for candidate in result.candidates:
            evidence = candidate.fields["value"].evidence
            assert evidence.status == "matched" and text[evidence.start:evidence.end] == evidence.quote
        fresh_results.append({"group": group, "completed_chunks": 16, "candidates": 16,
                              "reused_chunks": 0, "accepted_records": 0})

    # Repeating any finished stage only reads the ledger, never resends trials.
    for stage, round_number in (("A", None), ("B", 1), ("B", 2), ("B", 3), ("C", None), ("D", None)):
        run(stage, round_number)
    assert len(observed) == 108
    assert not any(name == "langextract" or name.startswith("langextract.") for name in sys.modules)
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "real_endpoint_calls": 0,
               "mock_network_calls": len(observed), "probe_requests": 0,
               "actual_tls_versions": {"unknown": 108}, "d1_scored_trials": d1_count,
               "fresh_runs": fresh_results, "repeat_stage_new_calls": 0,
               "langextract_imported": False,
               "scope": "Installed pipeline and experiment harness only; synthetic gold-backed responses, no network/TLS or model-quality claim"}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

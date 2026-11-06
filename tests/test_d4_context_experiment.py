"""Experimental metrics and freeze boundaries, independent of model calls."""

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d4_context_experiment.py"
SPEC = importlib.util.spec_from_file_location("d4_context_experiment", SCRIPT)
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


def test_tuple_score_prioritizes_wrong_owner_even_with_correct_value():
    gold = [{"object": "A", "parameter": "mass", "value": "12", "unit": "kg", "condition": "warm"}]
    wrong_owner = dict(gold[0], object="B")
    wrong_condition = dict(gold[0], condition="cold")
    scores = experiment.score_records([{"values": wrong_owner}, {"values": wrong_condition}], gold)
    assert scores["complete_correct_tuples"] == 0
    assert scores["object_or_condition_errors"] == 2
    assert scores["missing_tuples"] == 1
    assert scores["manual_semantic_review"] == "pending"


def test_tuple_score_separates_duplicate_and_wrong_value():
    gold = [{"object": "A", "parameter": "mass", "value": "12", "unit": "kg", "condition": None}]
    records = [{"values": gold[0]}, {"values": gold[0]}, {"values": dict(gold[0], value="15")}]
    scores = experiment.score_records(records, gold)
    assert scores["complete_correct_tuples"] == 1
    assert scores["duplicates"] == 1
    assert scores["wrong_value_or_unit"] == 1
    assert scores["object_or_condition_errors"] == 0


def test_frozen_material_change_is_rejected(tmp_path):
    material = tmp_path / "cases.json"
    material.write_bytes(b"[]")
    experiment.write_json(tmp_path / "manifest.json", {"files": {"cases.json": experiment.digest(b"[]")}})
    experiment.check_frozen(tmp_path)
    material.write_bytes(b"[1]")
    with pytest.raises(ValueError, match="changed"):
        experiment.check_frozen(tmp_path)


def test_synthetic_bodies_and_risk_expectations_are_explicit():
    cases = {case["id"]: case for case in experiment.synthetic_cases()}
    assert len(cases) == 7
    for case in cases.values():
        span = case["target"]
        assert case["source"][span["start"]:span["end"]].strip()
    assert cases["section_boundary"]["probes"] == [{"name": "other_section", "quote": "77", "kind": "forbidden"}]
    assert cases["context_only"]["probes"][0]["kind"] == "context_only"


def test_preparation_compares_identical_target_bodies_without_calls(tmp_path, monkeypatch):
    def fake_worker(case, arm, package_source):
        return {"arm": arm, "case": case["id"], "body": case["target"],
                "request": {"body": case["source"][case["target"]["start"]:case["target"]["end"]]},
                "uncovered_chars": 0}

    monkeypatch.setattr(experiment, "isolated_worker", fake_worker)
    root = tmp_path / "prepared"
    manifest = experiment.prepare(experiment.synthetic_cases()[:1], root, tmp_path, tmp_path)
    assert manifest["real_endpoint_calls"] == 0
    assert manifest["planned_calls"] == 4
    assert manifest["comparisons"] == [{"case": "heading_object", "same_body_all_arms": True,
                                        "dev18_none_request_identical": True, "source_coverage_all_arms": True}]
    assert len((root / "planned-requests.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    with pytest.raises(FileExistsError):
        experiment.prepare(experiment.synthetic_cases()[:1], root, tmp_path, tmp_path)


def test_live_refuses_a_public_probe_bundle_before_loading_credentials(tmp_path):
    experiment.write_json(tmp_path / "manifest.json", {"private": False, "planned_calls": 12, "files": {}})
    with pytest.raises(ValueError, match="private"):
        experiment.run_live(tmp_path, tmp_path / "results")


def test_real_context_probes_measure_both_benefit_and_new_ambiguity():
    cases = {case["id"]: case for case in experiment.synthetic_cases()}
    heading = experiment.worker(cases["heading_object"], "structure")
    condition = experiment.worker(cases["neighbor_condition"], "neighbors")
    repeated = experiment.worker(cases["repeated_value"], "neighbors")
    forbidden = experiment.worker(cases["section_boundary"], "neighbors")
    context_only = experiment.worker(cases["context_only"], "neighbors")
    assert heading["probes"][0]["status"] == "matched"
    assert condition["probes"][0]["status"] == "matched"
    assert repeated["probes"][0]["status"] == "ambiguous"
    assert repeated["probes"][0]["body_matches"] == 1
    assert forbidden["probes"][0]["status"] == "unmatched"
    assert context_only["probes"][0]["context_only"] is True
    assert all(row["uncovered_chars"] == 0 for row in (heading, condition, repeated, forbidden, context_only))


def test_live_executes_frozen_requests_once_and_retains_parse_failure_usage(tmp_path, monkeypatch):
    from attestextract import envconfig, registry
    from attestextract.errors import BackendError
    from attestextract.models import BackendResponse

    cases = experiment.synthetic_cases()[:3]
    rows = [experiment.worker(case, arm) for case in cases for arm in experiment.ARMS]
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    experiment.write_json(bundle / "cases.json", cases)
    experiment.write_json(bundle / "backend.json", experiment.BACKEND)
    (bundle / "planned-requests.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    files = {name: experiment.digest((bundle / name).read_bytes())
             for name in ("cases.json", "backend.json", "planned-requests.jsonl")}
    experiment.write_json(bundle / "manifest.json", {"private": True, "planned_calls": 12,
                                                     "files": files, "env_file": "unused-test-env"})
    calls = []

    class Backend:
        def generate(self, request):
            calls.append(request.model_dump(mode="json"))
            if len(calls) == 3:
                raise BackendError("Synthetic transport failure", code="transport_error",
                                   http_diagnostics={"causes": [{"type": "ConnectError"}]})
            text = '{"records": []}' if len(calls) != 2 else '{"records":'
            return BackendResponse(text=text, usage={"output_tokens": 10}, elapsed_seconds=0.1)

    class Registry:
        def create(self, *args, **kwargs):
            return Backend()

    monkeypatch.setattr(registry, "default_registry", Registry)
    monkeypatch.setattr(envconfig, "load_env_file", lambda path: {"DEEPSEEK_API_KEY": "synthetic-unused"})
    summary = experiment.run_live(bundle, tmp_path / "results")
    assert calls == [row["request"] for row in rows]
    assert summary["reserved_calls"] == 12
    assert summary["results"][1]["status"] == "failed"
    assert summary["results"][1]["usage"] == {"output_tokens": 10}
    assert summary["results"][2]["http_diagnostics"] == {"causes": [{"type": "ConnectError"}]}
    with pytest.raises(FileExistsError):
        experiment.run_live(bundle, tmp_path / "other-results")
    assert len(calls) == 12


def test_adapter_truncation_keeps_usage_and_separate_partial_response_traces(tmp_path, monkeypatch):
    import httpx

    from attestextract import envconfig, registry
    from attestextract.responses_backend import DeepSeekResponsesBackend

    cases = experiment.synthetic_cases()[:3]
    rows = [experiment.worker(case, arm) for case in cases for arm in experiment.ARMS]
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    experiment.write_json(bundle / "cases.json", cases)
    experiment.write_json(bundle / "backend.json", experiment.BACKEND)
    (bundle / "planned-requests.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    files = {name: experiment.digest((bundle / name).read_bytes())
             for name in ("cases.json", "backend.json", "planned-requests.jsonl")}
    experiment.write_json(bundle / "manifest.json", {"private": True, "planned_calls": 12,
                                                     "files": files, "env_file": "unused-test-env"})
    requests = []

    def endpoint(request):
        requests.append(request)
        truncated = len(requests) <= 2
        content = '{"records":[' + str(len(requests)) if truncated else '{"records":[]}'
        return httpx.Response(200, json={
            "id": "synthetic-response", "object": "response",
            "status": "incomplete" if truncated else "completed", "model": "deepseek-flash",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": content}]}],
            "error": None, "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
            "usage": {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20},
        })

    class Registry:
        def create(self, name, *, config, api_key, trace):
            return DeepSeekResponsesBackend(config.base_url, config.model, api_key=api_key,
                                           trace=trace, max_attempts=1, transport=httpx.MockTransport(endpoint))

    monkeypatch.setattr(registry, "default_registry", Registry)
    monkeypatch.setattr(envconfig, "load_env_file", lambda path: {"DEEPSEEK_API_KEY": "synthetic-unused"})
    output = tmp_path / "results"
    summary = experiment.run_live(bundle, output)
    assert len(requests) == summary["reserved_calls"] == 12
    for index, arm in enumerate(("dev18", "none"), 1):
        result = summary["results"][index - 1]
        assert result["status"] == "failed" and result["error_code"] == "truncated_response"
        assert result["finish_reason"] == "length"
        assert result["usage"] == {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
        assert result["response_metadata"]["elapsed_seconds"] == result["elapsed_seconds"]
        trace = output / f"{index:02d}-heading_object-{arm}.diagnostics"
        assert (trace / "responses" / "heading_object.txt").read_text(encoding="utf-8") == '{"records":[' + str(index)
        manifest = json.loads((trace / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
    assert len(list(output.glob("*.diagnostics"))) == 12

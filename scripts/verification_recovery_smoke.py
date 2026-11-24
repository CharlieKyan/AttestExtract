"""Installed Python/CLI acceptance for bounded verification recovery.

Uses synthetic observations, mock HTTP and explicitly simulated 0.4 checkpoint
fields. No real model, credential file or repository import path is used.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

import niceextract
from niceextract import ExecutionOptions, GenerationOptions, cli, extract
from niceextract.backends import OpenAICompatibleBackend
from niceextract.errors import ConfigurationError
from niceextract.registry import BackendRegistry


CLAIMS = tuple((f"Reactor {'A' if n <= 6 else 'B' if n <= 12 else 'C'} has mass {n} g under condition {n}.",
                f"Reactor {'A' if n <= 6 else 'B' if n <= 12 else 'C'}", str(n)) for n in range(1, 14))
SOURCE = "\n\n".join(item[0] for item in CLAIMS)
TASK = {"prompt_description": "Extract the mass reported for each reactor and experimental condition.",
        "field_list": {"mass": "Directly reported reactor mass under its stated experimental condition"}}
SUBJECT = "one individual reactor; experimental conditions remain separate observations"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def checkpoint(run):
    return read(run / "diagnostics/analysis/checkpoint.json")["state"]


def legacy_fixture(run):
    """Simulate the old storage shape, not a historical model run or accepted answer."""
    path = run / "diagnostics/analysis/checkpoint.json"
    state = checkpoint(run)
    state = {key: state[key] for key in ("revision", "fingerprint", "max_calls", "calls_used", "jobs")}
    state["version"] = "0.4"
    job_keys = {"job_id", "kind", "status", "attempts", "steps", "observation_ids",
                "error_code", "review_observation_ids", "reason"}
    step_keys = {"number", "status", "input_bytes", "request_hash", "route_id", "model",
                 "reported_model", "request_id", "usage", "http_attempts", "elapsed_seconds",
                 "outcome_unknown", "request_outcome", "action", "tool_hash", "response_hash",
                 "error_code", "error", "validation_summary", "validation_details", "validation_hash",
                 "http_status", "http_details"}
    state["jobs"] = [{key: value for key, value in job.items() if key in job_keys} for job in state["jobs"]]
    for job in state["jobs"]:
        job["steps"] = [{key: value for key, value in step.items() if key in step_keys} for step in job["steps"]]
    checksum = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    path.write_text(json.dumps({"state": state, "sha256": checksum}, indent=2), encoding="utf-8")


class Service:
    def __init__(self):
        self.mode = "duplicate"
        self.calls = []
        self.responses = []
        self.bad_sent = False

    def handle(self, request):
        assert request.url.host == "synthetic.invalid" and request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-install-secret"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        payload = json.loads(body["messages"][-1]["content"])
        kind = "extract" if "source" in payload else "verify"
        self.calls.append({"kind": kind, "body": body})
        if kind == "extract":
            records = []
            for quote, entity, value in CLAIMS:
                if quote in payload["source"]:
                    fields = {"entity": entity, "value": value, "unit": "g", "conditions": "condition " + value}
                    records.append({"record_type": "mass", "values": fields,
                                    "quotes": {name: quote for name in fields}})
            text = json.dumps({"records": records})
        else:
            values = [item["value"] for item in payload["observations"]]
            decision = self.decision(payload)
            is_second = values == [str(n) for n in range(7, 13)]
            if is_second and self.mode == "timeout":
                raise httpx.ReadTimeout("Synthetic unknown outcome", request=request)
            bad = is_second and (self.mode == "duplicate" or self.mode == "once" and not self.bad_sent)
            if bad:
                # The first declaration is empty; the later one is a new owner.
                # Never convert this ambiguous response with ordinary json.loads.
                objects = decision["objects"]
                assert objects
                decision["objects"] = []
                text = json.dumps(decision)[:-1] + ', "objects": ' + json.dumps(objects) + '}'
                self.bad_sent = True
            else:
                text = json.dumps(decision)
        self.responses.append(text)
        return httpx.Response(200, json={"model": "synthetic-verification",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    @staticmethod
    def decision(payload):
        observations = payload["observations"]
        entity = observations[0]["entity"]
        assert all(item["entity"] == entity for item in observations)
        passages = payload["initial_context"]["passages"] + payload["known_object_evidence"]

        def evidence(quote):
            passage = next(part for part in passages if quote in part["text"])
            return {"passage_id": passage["passage_id"], "quote": quote, "occurrence": None}

        known = next((item for item in payload["known_objects"] if item["name"] == entity), None)
        object_id = known["object_id"] if known else "owner"
        first_quote = next(quote for quote, _, value in CLAIMS if value == observations[0]["value"])
        objects = [] if known else [{"object_id": object_id, "name": entity, "kind": "instance",
            "scope": "synthetic experimental reactor", "is_table_subject": True, "mentions": [evidence(first_quote)]}]
        bindings = []
        for item in observations:
            quote = next(quote for quote, _, value in CLAIMS if value == item["value"])
            bindings.append({"observation_id": item["observation_id"], "subject_id": object_id,
                "status": "supported", "evidence": [evidence(quote)], "reason": "The source directly states this reactor and mass.",
                "target_match": "supported", "property_name": "mass", "statistic": None})
        return {"action": "decide", "question": "Which reactor owns each mass?", "query": None,
                "search_offset": 0, "passage_ids": [], "neighbors": 0,
                "objects": objects, "relations": [], "bindings": bindings}


class Entry:
    def __init__(self, root, name):
        self.root, self.name, self.service = root, name, Service()
        root.mkdir(parents=True)
        self.source, self.task, self.config = (root / filename for filename in ("source.md", "task.json", "models.toml"))
        self.source.write_bytes(SOURCE.encode("utf-8"))
        self.task.write_text(json.dumps(TASK), encoding="utf-8")
        self.config.write_text('[profiles.synthetic]\nbase_url = "https://synthetic.invalid/v1"\n'
            'model = "synthetic-verification"\nstructured_output = "json_object"\n'
            'api_key_env = "NICEEXTRACT_TEST_API_KEY"\nmax_attempts = 1\n'
            '[profiles.synthetic.generation]\nmax_output_tokens = 2048\n', encoding="utf-8")

    def invoke(self, name, *, retries=0, resume=None, selected=None, invalid=False):
        output = self.root / name
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            if self.name == "python":
                backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-verification",
                    api_key="synthetic-install-secret", structured_output="json_object", max_attempts=1,
                    transport=httpx.MockTransport(self.service.handle))
                try:
                    result = extract(SOURCE, TASK, backend=backend, output_dir=output, progress="quiet",
                        generation=GenerationOptions(max_output_tokens=2048), format_retries=retries,
                        execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1), max_chunk_chars=6000,
                        table_subject=SUBJECT, max_analysis_calls=4, resume_from=resume,
                        retry_analysis_jobs=selected)
                except ConfigurationError:
                    assert invalid, "Unexpected configuration failure in valid recovery."
                else:
                    assert not invalid, "An ineligible historical job must be rejected before any generation."
                    assert result.analysis is not None
            else:
                registry = BackendRegistry()

                def factory(*, config, api_key, trace):
                    return OpenAICompatibleBackend(config.base_url, config.model, api_key=api_key, trace=trace,
                        structured_output=config.structured_output, max_attempts=1,
                        transport=httpx.MockTransport(self.service.handle))

                registry.register("openai-compatible", factory)
                args = ["extract", "--input", str(self.source), "--task", str(self.task),
                    "--config", str(self.config), "--profile", "synthetic", "--progress", "quiet",
                    "--chunking", "fixed", "--max-model-calls", "1", "--max-chunk-chars", "6000",
                    "--table-subject", SUBJECT, "--max-analysis-calls", "4",
                    "--format-retries", str(retries), "--output-dir", str(output)]
                if resume is not None:
                    args += ["--resume", str(resume)]
                for job in selected or []:
                    args += ["--retry-analysis-job", job]
                code = cli.main(args, registry=registry)
                if invalid:
                    assert code == 1
                else:
                    status = read(output / "analysis/analysis.json")["status"]
                    assert code == (0 if status == "complete" else 2)
        (self.root / (name + ".terminal.txt")).write_text(captured.getvalue(), encoding="utf-8")
        return output


def accept(root, entry):
    runner = Entry(root, entry)
    service = runner.service
    first = runner.invoke("f")
    assert len(service.calls) == 3
    state = checkpoint(first)
    assert [job["status"] for job in state["jobs"]] == ["completed", "failed", "pending"]
    assert state["calls_used"] == 2
    rejected = (first / "diagnostics/analysis/responses/verify-0002.step-01.txt").read_text(encoding="utf-8")
    assert rejected.count('"objects"') == 2
    legacy_fixture(first)
    frozen = hashes(first)

    # This branch demonstrates that ordinary resume only handles pending jobs.
    service.mode = "good"
    ordinary = runner.invoke("o", retries=1, resume=first)
    assert len(service.calls) == 4
    assert [j["status"] for j in checkpoint(ordinary)["jobs"]] == ["completed", "failed", "completed"]
    assert checkpoint(ordinary)["calls_used"] == 3

    recovered = runner.invoke("r", retries=1, resume=first, selected=["verify-0002"])
    assert len(service.calls) == 6
    state = checkpoint(recovered)
    assert state["calls_used"] == 4 and all(j["status"] == "completed" for j in state["jobs"])
    assert [j["attempts"] for j in state["jobs"]] == [1, 2, 1]
    analysis = read(recovered / "analysis/analysis.json")
    assert analysis["status"] == "complete" and len(analysis["observations"]) == 13
    assert all(item["binding_status"] == "supported" for item in analysis["observations"])
    assert [item["value"] for item in analysis["observations"]] == [str(n) for n in range(1, 14)]
    assert checkpoint(recovered)["jobs"][0]["steps"] == checkpoint(first)["jobs"][0]["steps"]
    for evidence in analysis["evidence"]:
        assert SOURCE[evidence["start"]:evidence["end"]] == evidence["quote"]
    retry_request = service.calls[4]["body"]
    feedback = retry_request["messages"][0]["content"]
    assert "objects" in feedback and "duplicate" in feedback.lower()
    assert "correct_shape" not in feedback and "wrong_shape" not in feedback
    original_payload = json.loads(service.calls[2]["body"]["messages"][-1]["content"])
    retry_payload = json.loads(retry_request["messages"][-1]["content"])
    for key in ("observations", "target_scope", "initial_context", "known_objects", "lookup_history", "response_contract"):
        assert retry_payload[key] == original_payload[key]
    complete_hashes = hashes(recovered)
    runner.invoke("c", retries=1, resume=recovered)
    assert len(service.calls) == 6 and hashes(recovered) == complete_hashes

    # Going backwards after a later job was sent would change its saved context.
    runner.invoke("l", retries=1, resume=ordinary, selected=["verify-0002"], invalid=True)
    assert len(service.calls) == 6
    service.mode = "duplicate"
    failed = runner.invoke("b", retries=1, resume=first, selected=["verify-0002"])
    assert len(service.calls) == 7 and checkpoint(failed)["calls_used"] == 3
    assert checkpoint(failed)["jobs"][1]["status"] == "failed"
    failed_hashes = hashes(failed)
    runner.invoke("x", retries=1, resume=failed, selected=["verify-0002"], invalid=True)
    assert len(service.calls) == 7 and hashes(failed) == failed_hashes and hashes(first) == frozen
    return {"passed": True, "mock_http_calls": len(service.calls), "reused_jobs": 1,
        "recovery_new_calls": 2, "historical_analysis_calls": 2, "recovered_analysis_calls": 4,
        "ordinary_resume_only_pending": True, "completed_resume_new_calls": 0,
        "exhausted_resume_new_calls": 0, "non_tail_rejected": True, "history_unchanged": True,
        "original_values_and_evidence_preserved": True, "fixture": "Synthetic 0.4 checkpoint fields"}


@contextmanager
def offline_only():
    def blocked(*args, **kwargs):
        raise AssertionError("Network and credential files are forbidden in this synthetic acceptance.")
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ("NICEEXTRACT_", "ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_", "OPENAI_"))}
    env["NICEEXTRACT_TEST_API_KEY"] = "synthetic-install-secret"
    with patch.dict(os.environ, env, clear=True), patch("socket.socket.connect", blocked), \
         patch("socket.socket.connect_ex", blocked), patch("socket.create_connection", blocked), \
         patch("socket.getaddrinfo", blocked), patch("niceextract.envconfig.load_env_file", blocked), \
         patch.object(cli, "load_env_file", blocked):
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    with offline_only():
        cases = {entry: accept(root / entry[:2], entry) for entry in ("python", "cli")}
    summary = {"passed": True, "package_version": niceextract.__version__, "module_path": niceextract.__file__,
               "real_model_calls": 0, "network_attempts": 0, "mock_http_calls": sum(c["mock_http_calls"] for c in cases.values()),
               "entries": cases}
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()

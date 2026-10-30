"""Bounded D2 checks with synthetic sources; mock by default, live is explicit.

Run with the installed project Python. Output must be a new directory outside
the repository. Only source/task go to the model; expected results stay local.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from attestextract import __version__, cli
from attestextract.adapter_examples import synthetic_factory
from attestextract.chunking import chunk_document
from attestextract.config import resolve_backend_config
from attestextract.envconfig import load_env_file
from attestextract.errors import AttestExtractError, ConfigurationError
from attestextract.registry import default_registry


CASES = ("attribute", "observation", "relation")
LIMITS = {"timeout": 60, "max_attempts": 1, "format_retries": 0, "max_output_tokens": 2048}


def fixture(name):
    resource = files("attestextract").joinpath("examples", "d1", name)
    source = resource.joinpath("source.txt").read_bytes()
    task = resource.joinpath("task.json").read_bytes()
    expected = json.loads(resource.joinpath("expected.json").read_text(encoding="utf-8"))
    return source, task, expected


def mock_envelope(name):
    """Hand-written positive responses; do not replay the negative D1 80 W case."""
    if name == "attribute":
        records = [("product_attribute", "产品云杉灯的额定功率为8 W。",
                    {"product": "云杉灯", "rated_power": 8, "power_unit": "W"})]
    elif name == "observation":
        records = [
            ("flow_observation", "试样青禾在18 °C下的通量为7 L/h。",
             {"sample": "试样青禾", "flow": 7, "flow_unit": "L/h", "temperature": 18, "temperature_unit": "°C"}),
            ("flow_observation", "试样青禾在36 °C下的通量为7 L/h。",
             {"sample": "试样青禾", "flow": 7, "flow_unit": "L/h", "temperature": 36, "temperature_unit": "°C"}),
        ]
    else:
        records = [("supply_relation", "岚桥工厂供应霁灯组件。",
                    {"subject": "岚桥工厂", "relation": "供应", "object": "霁灯组件"})]
    text = json.dumps({"records": [
        {"record_type": record_type, "fields": {
            key: {"value": value, "quote": quote} for key, value in values.items()
        }} for record_type, quote, values in records
    ]}, ensure_ascii=False)
    return {"status": "completed", "deployment": "synthetic-observed-model", "id": "synthetic-request",
            "output": {"text": text}, "metrics": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}}


def check_result(result, source, expected):
    """Compare hand-written values and source spans independently of pipeline status."""
    candidates = result["candidates"]
    actual = [{"record_type": candidate["record_type"],
               "fields": {key: field["value"] for key, field in candidate["fields"].items()}}
              for candidate in candidates]
    remaining = list(actual)
    matched = 0
    for record in expected["records"]:
        wanted = {"record_type": record["record_type"],
                  "fields": {key: field["value"] for key, field in record["fields"].items()}}
        if wanted in remaining:
            remaining.remove(wanted)
            matched += 1
    evidence_count = 0
    located_count = 0
    for candidate in candidates:
        for field in candidate["fields"].values():
            evidence_count += 1
            evidence = field["evidence"]
            start, end = evidence["start"], evidence["end"]
            if (evidence["status"] == "matched" and type(start) is int and type(end) is int
                    and 0 <= start < end <= len(source) and source[start:end] == evidence["quote"]):
                located_count += 1
    return {
        "expected_records": len(expected["records"]), "candidate_count": len(candidates),
        "matching_records": matched, "unexpected_records": len(remaining),
        "content_values_passed": matched == len(expected["records"]) and not remaining,
        "evidence_fields": evidence_count, "exact_source_spans": located_count,
        "evidence_passed": evidence_count > 0 and evidence_count == located_count,
        "needs_review_count": sum(candidate["needs_review"] for candidate in candidates),
    }


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def preflight(args):
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    if output == root or output.is_relative_to(root):
        raise ConfigurationError("Acceptance output must be outside the public repository.")
    if output.exists():
        raise ConfigurationError("Acceptance output must be a new directory; existing runs are never overwritten.")
    for case in CASES:
        source, _, _ = fixture(case)
        if len(chunk_document(source.decode("utf-8"))) != 1:
            raise ConfigurationError("Acceptance sources must each produce exactly one chunk.")
    if args.mode == "mock":
        if args.config is not None or args.env_file is not None:
            raise ConfigurationError("Mock mode uses its own synthetic profile and does not load credentials.")
        return output, ("synthetic",)
    if args.config is None:
        raise ConfigurationError("Live acceptance requires --config with official endpoint profiles.")
    profiles = tuple(args.profiles)
    if not 1 <= len(profiles) <= 2 or len(set(profiles)) != len(profiles):
        raise ConfigurationError("Live acceptance allows one or two distinct profiles, three requests each.")
    if any(re.fullmatch(r"[a-z][a-z0-9_-]*", profile) is None for profile in profiles):
        raise ConfigurationError("Acceptance profile names must be simple lowercase labels.")
    environment = load_env_file(args.env_file) if args.env_file else dict(os.environ)
    for profile in profiles:
        config = resolve_backend_config(overrides=LIMITS, environment=environment,
                                        config_path=args.config, profile=profile)
        host = urlsplit(config.base_url).hostname or ""
        official = (host == "api.deepseek.com" or host == "dashscope.aliyuncs.com"
                    or host.endswith(".cn-beijing.maas.aliyuncs.com"))
        if config.adapter != "openai-compatible" or urlsplit(config.base_url).scheme != "https" or not official:
            raise ConfigurationError("Live acceptance is limited to official DeepSeek or Qwen Beijing HTTPS endpoints.")
        api_key = environment.get(config.api_key_env)
        if not api_key or not api_key.strip():
            raise ConfigurationError("A selected live profile has no credential; zero requests were sent.")
        default_registry().create(config.adapter, config=config, api_key=api_key)
    return output, profiles


def run(args):
    output, profiles = preflight(args)
    output.mkdir(parents=True)
    config_path = args.config
    if args.mode == "mock":
        config_path = output / "synthetic.toml"
        config_path.write_text('[profiles.synthetic]\nadapter="synthetic-envelope"\n'
                               'base_url="https://synthetic.invalid"\nmodel="synthetic-deployment"\n'
                               'api_key_env="D2_SYNTHETIC_UNUSED"\nmax_attempts=1\n', encoding="utf-8")
    summary = {"package_version": __version__, "mode": args.mode,
               "started_at": datetime.now(timezone.utc).isoformat(),
               "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
               "limits": LIMITS, "real_endpoint_calls": 0, "mock_http_calls": 0, "cases": [],
               "assessment": "Synthetic fixture values and exact source spans; no Jev or human acceptance."}
    for profile in profiles:
        for case in CASES:
            directory = output / profile / case
            directory.mkdir(parents=True)
            source, task, expected = fixture(case)
            source_path, task_path = directory / "source.txt", directory / "task.json"
            source_path.write_bytes(source)
            task_path.write_bytes(task)
            logs = directory / "diagnostics"
            arguments = ["extract", "--config", str(config_path), "--profile", profile,
                         "--input", str(source_path), "--task", str(task_path),
                         "--output", str(directory / "extraction.json"), "--report", str(directory / "review.md"),
                         "--log-dir", str(logs), "--timeout", "60", "--max-attempts", "1",
                         "--format-retries", "0", "--max-output-tokens", "2048"]
            if args.env_file is not None:
                arguments.extend(["--env-file", str(args.env_file)])
            registry = default_registry()
            if args.mode == "mock":
                registry.register("synthetic-envelope", synthetic_factory([mock_envelope(case)]))
            status = cli.main(arguments, registry=registry)
            events_path = logs / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
            http_attempts = sum(event.get("stage") == "http" and event.get("event") == "started" for event in events)
            mock_attempts = sum(event.get("event") == "synthetic_envelope" for event in events)
            statuses = [event["status_code"] for event in events if event.get("stage") == "http" and event.get("event") == "completed"]
            metadata = _read_json(logs / "responses" / "chunk-0001.meta.json")
            entry = {"profile": profile, "case": case, "exit_code": status, "http_attempts": http_attempts,
                     "mock_http_attempts": mock_attempts,
                     "http_statuses": statuses, "source_sha256": hashlib.sha256(source).hexdigest(),
                     "task_sha256": hashlib.sha256(task).hexdigest(),
                     "adapter_path_passed": status == 0,
                     "http_passed": any(200 <= code < 300 for code in statuses) if args.mode == "live" else None,
                     "format_passed": status == 0, "response_metadata": metadata,
                     "error": _read_json(logs / "error.json")}
            if status == 0:
                result = _read_json(directory / "extraction.json")
                entry.update(check_result(result, source.decode("utf-8"), expected))
            else:
                entry.update(content_values_passed=None, evidence_passed=None)
            entry["passed"] = (status == 0 and entry["content_values_passed"] and entry["evidence_passed"])
            summary["cases"].append(entry)
            summary["real_endpoint_calls"] += http_attempts if args.mode == "live" else 0
            summary["mock_http_calls"] += mock_attempts
            summary["passed"] = all(item["passed"] for item in summary["cases"]) and len(summary["cases"]) == len(profiles) * len(CASES)
            (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--profiles", nargs="+", default=["deepseek", "qwen"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except AttestExtractError as exc:
        print(f"D2 acceptance: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("D2 acceptance could not start or save its evidence; check configuration, credentials and a new external output directory.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

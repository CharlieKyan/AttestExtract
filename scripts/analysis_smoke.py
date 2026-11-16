"""Installed-package analysis acceptance with synthetic HTTP and no network.

Run from any directory with an installed attestextract wheel. This script never
imports repository tests or inserts the checkout into Python's import path.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

import httpx

import attestextract
from attestextract import ExecutionOptions, GenerationOptions, cli, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.registry import BackendRegistry


EAST = "East site contains reactor R."
WEST = "West site contains reactor R."
SAMPLE = "Sample S1 is taken from West reactor R."
CLAIMS = (
    ("East reactor R has yield 18% at 20 C.", "R", "18", "20 C"),
    ("East reactor R has yield 22% at 30 C.", "R", "22", "30 C"),
    ("Sample S1 has yield 12% at 20 C.", "S1", "12", "20 C"),
)
SOURCE = "\n\n".join([EAST, *(claim[0] for claim in CLAIMS),
    *[f"Background note {number}: storage maintenance is unrelated to the reported measurements." for number in range(6)],
    WEST, SAMPLE])
TASK = {"prompt_description": "Extract each directly reported yield, preserving its owner and temperature.",
        "field_list": {"yield": "Reported yield of each object", "mass": "Reported mass, if present"}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class Service:
    def __init__(self, *, forbidden=False):
        self.calls = []
        self.forbidden = forbidden

    def handle(self, request):
        if self.forbidden:
            raise AssertionError("A completed extraction/analysis resume must not send a request")
        assert request.url.host == "synthetic.invalid" and request.method == "POST"
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        assert body["response_format"]["type"] == "json_schema"
        if "source" in payload:
            self.calls.append("extraction")
            records = []
            for quote, entity, value, conditions in CLAIMS:
                if quote not in payload["source"]:
                    continue
                values = {"entity": entity, "value": value, "unit": "%", "conditions": conditions}
                records.append({"record_type": "yield", "values": values,
                                "quotes": {name: quote for name in values}})
            response = {"records": records}
        else:
            assert "document" not in payload
            assert SOURCE not in body["messages"][-1]["content"]
            history = payload["lookup_history"]
            response = {"action": "decide", "question": "Does the sample belong to the West reactor occurrence?",
                        "query": None, "search_offset": 0, "passage_ids": [], "neighbors": 0,
                        "objects": [], "relations": [], "bindings": []}
            if not history:
                self.calls.append("search")
                response.update(action="search", query="reactor R")
            elif history[-1]["action"] == "search":
                self.calls.append("read")
                response.update(action="read", passage_ids=list(dict.fromkeys(
                    match["passage_id"] for match in history[-1]["result"]["matches"]
                    if any(quote in match["text"] for quote in (EAST, WEST, SAMPLE)))))
            else:
                self.calls.append("decide")
                response.update(self.decision(payload))
        return httpx.Response(200, json={"id": "synthetic-response", "model": "synthetic-analysis",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(response)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    @staticmethod
    def decision(payload):
        passages = list(payload["initial_context"]["passages"])
        for frame in payload["lookup_history"]:
            passages.extend(frame["result"].get("matches", []))
            passages.extend(frame["result"].get("passages", []))

        def cite(quote):
            found = next(part for part in passages if quote in part["text"])
            return {"passage_id": found["passage_id"], "quote": quote, "occurrence": None}

        objects = [
                {"object_id": "east", "name": "R", "kind": "instance", "scope": "East",
                 "is_table_subject": True, "mentions": [cite(EAST)]},
                {"object_id": "west", "name": "R", "kind": "instance", "scope": "West",
                 "is_table_subject": True, "mentions": [cite(WEST)]},
                {"object_id": "sample", "name": "S1", "kind": "sample", "scope": "West sample",
                 "is_table_subject": False, "mentions": [cite(SAMPLE)]},
        ]
        bindings = []
        for item in payload["observations"]:
            sample = item["entity"] == "S1"
            claim = next(quote for quote, _, value, _ in CLAIMS if value == item["value"])
            bindings.append({"observation_id": item["observation_id"],
                "subject_id": "sample" if sample else "east", "status": "supported",
                "evidence": [cite(claim), cite(SAMPLE if sample else EAST)],
                "reason": "Keep direct owner; separate identity and measurement paragraphs supplied.",
                "target_match": "supported", "property_name": "yield", "statistic": None})
        return {"objects": objects, "bindings": bindings,
                "relations": [{"source_id": "sample", "target_id": "west", "predicate": "sample_of",
                               "evidence": [cite(SAMPLE)]}]}


def run(root, service, *, resume=None):
    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-analysis",
                                      transport=httpx.MockTransport(service.handle),
                                      structured_output="json_schema", max_attempts=1)
    return extract(SOURCE, TASK, backend=backend, output_dir=root, progress="quiet",
                   generation=GenerationOptions(max_output_tokens=2048),
                   execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=20),
                   max_chunk_chars=110, table_subject="one reactor occurrence in its original site",
                   max_analysis_calls=3, resume_from=resume)


def accept(root):
    service = Service()
    first = root / "first"
    result = run(first, service)
    assert result.run.chunk_count > 1, "Acceptance must exercise multiple source chunks"
    assert len(result.candidates) == 3 and result.analysis["status"] == "complete"
    assert [call for call in service.calls if call != "extraction"] == ["search", "read", "decide"]
    assert "analysis" not in read(first / "result.json")
    analysis = read(first / "analysis/analysis.json")
    assert analysis["semantic_status"] == "model_proposed"
    view = read(first / "analysis/objects.json")
    assert len(view["objects"]) == 2 and {obj["name"] for obj in view["objects"]} == {"R"}
    assert len({obj["object_id"] for obj in view["objects"]}) == 2
    by_scope = {obj["scope"]: obj for obj in view["objects"]}
    assert [obs["value"] for obs in by_scope["East"]["fields"]["yield"]] == ["18", "22"]
    assert [obs["conditions"] for obs in by_scope["East"]["fields"]["yield"]] == ["20 C", "30 C"]
    assert by_scope["West"]["fields"]["yield"] == []
    assert [obs["value"] for obs in view["related_observations"]] == ["12"]
    assert not view["unbound_observations"]
    with (first / "analysis/object_table.csv").open(encoding="utf-8-sig", newline="") as stream:
        wide = list(csv.DictReader(stream))
    assert len(wide) == 2 and all(json.loads(row["mass"]) == [] for row in wide)
    with sqlite3.connect(first / "analysis/analysis.sqlite") as db:
        rows = db.execute("""SELECT o.kind,v.value_json,v.source_record FROM observations v
                             JOIN objects o ON v.subject_id=o.object_id WHERE o.kind='sample'""").fetchall()
        assert len(rows) == 1 and json.loads(rows[0][1]) == "12" and rows[0][2].startswith("/candidates/")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    old = hashes(first)
    replay_service = Service(forbidden=True)
    replay = run(root / "replay", replay_service, resume=first)
    assert replay.analysis["status"] == "complete" and replay_service.calls == []
    assert replay.analysis["objects"] == analysis["objects"]
    assert replay.analysis["observations"] == analysis["observations"]
    assert replay.analysis["budget"]["reserved"] == 3
    assert hashes(first) == old
    return {"extraction_chunks": result.run.chunk_count, "synthetic_http_calls": len(service.calls),
            "analysis_calls": 3, "resume_new_calls": 0, "observations": 3, "table_objects": 2,
            "same_names_distinct_ids": True, "sample_not_promoted": True,
            "multiple_conditions_preserved": True, "sqlite_references_valid": True,
            "historical_artifacts_unchanged": True}


def accept_cli(root):
    inputs = root / "cli-inputs"
    inputs.mkdir()
    source, task, config = inputs / "source.md", inputs / "task.json", inputs / "models.toml"
    source.write_bytes(SOURCE.encode("utf-8"))
    task.write_text(json.dumps(TASK), encoding="utf-8")
    config.write_text('[profiles.synthetic]\nbase_url = "https://synthetic.invalid/v1"\n'
                      'model = "synthetic-analysis"\nstructured_output = "json_schema"\n'
                      'max_attempts = 1\nformat_retries = 0\n'
                      '[profiles.synthetic.generation]\nmax_output_tokens = 2048\n', encoding="utf-8")
    service = Service()
    registry = BackendRegistry()

    def factory(*, config, api_key, trace):
        return OpenAICompatibleBackend(config.base_url, config.model, api_key=api_key, trace=trace,
            structured_output=config.structured_output, max_attempts=config.max_attempts,
            transport=httpx.MockTransport(service.handle))

    registry.register("openai-compatible", factory)
    arguments = ["extract", "--input", str(source), "--task", str(task),
                 "--config", str(config), "--profile", "synthetic", "--progress", "quiet",
                 "--chunking", "fixed", "--max-chunk-chars", "110", "--max-model-calls", "20",
                 "--table-subject", "one reactor occurrence in its original site", "--max-analysis-calls", "3",
                 "--analysis-max-steps", "4", "--analysis-input-budget", "48000"]
    captured = io.StringIO()
    with redirect_stdout(captured):
        assert cli.main([*arguments, "--output-dir", str(root / "cli-check"), "--check"], registry=registry) == 0
        assert service.calls == []
        assert cli.main([*arguments, "--output-dir", str(root / "cli")], registry=registry) == 0
    before = len(service.calls)
    analysis = read(root / "cli/analysis/analysis.json")
    assert analysis["status"] == "complete" and len(analysis["observations"]) == 3
    assert [call for call in service.calls if call != "extraction"] == ["search", "read", "decide"]
    old = hashes(root / "cli")
    service.forbidden = True
    with redirect_stdout(captured):
        assert cli.main([*arguments, "--output-dir", str(root / "cli-replay"),
                         "--resume", str(root / "cli")], registry=registry) == 0
    assert len(service.calls) == before and hashes(root / "cli") == old
    (root / "cli-terminal.txt").write_text(captured.getvalue(), encoding="utf-8")
    return {"cli_check_new_calls": 0, "cli_synthetic_http_calls": before,
            "cli_analysis_calls": 3, "cli_resume_new_calls": 0, "cli_exit_status": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    root = parser.parse_args().output.resolve()
    module_path = Path(attestextract.__file__).resolve()
    assert any(part in {"site-packages", "dist-packages"} for part in module_path.parts), \
        "Run this acceptance with a normally installed wheel, not an editable/source checkout"
    root.mkdir(parents=True, exist_ok=False)
    network_attempts = []

    def blocked(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Network access is forbidden in installed analysis acceptance")

    with patch.dict(os.environ, {}, clear=True), patch("socket.socket.connect", blocked), \
            patch("socket.socket.connect_ex", blocked), patch("socket.create_connection", blocked), \
            patch("socket.getaddrinfo", blocked):
        details = accept(root)
        details.update(accept_cli(root))
    assert not network_attempts
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": str(module_path), "installed_package_verified": True,
               "network_attempts": 0, "real_model_calls": 0, **details,
               "notice": "Synthetic offline acceptance; not provider or scientific-quality validation."}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

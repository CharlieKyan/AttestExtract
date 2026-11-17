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
    def __init__(self, *, mode="json_schema", forbidden=False):
        self.calls = []
        self.requests = []
        self.mode = mode
        self.forbidden = forbidden

    def handle(self, request):
        if self.forbidden:
            raise AssertionError("A completed extraction/analysis resume must not send a request")
        assert request.url.host == "synthetic.invalid" and request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-install-secret"
        body = json.loads(request.content)
        self.requests.append(body)
        assert body["response_format"]["type"] == self.mode
        if self.mode == "json_object" and not any(
            "json" in message["content"].casefold() for message in body["messages"]
        ):
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                "message": "Prompt must contain the word 'json' in some form to use "
                           "'response_format' of type 'json_object'."}})
        payload = json.loads(body["messages"][-1]["content"])
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
                                      api_key="synthetic-install-secret",
                                      transport=httpx.MockTransport(service.handle),
                                      structured_output=service.mode, max_attempts=1)
    return extract(SOURCE, TASK, backend=backend, output_dir=root, progress="quiet",
                   generation=GenerationOptions(max_output_tokens=2048),
                   execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=20),
                   max_chunk_chars=110, table_subject="one reactor occurrence in its original site",
                   max_analysis_calls=3, resume_from=resume)


def accept(root, mode):
    service = Service(mode=mode)
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
    replay_service = Service(mode=mode, forbidden=True)
    replay = run(root / "replay", replay_service, resume=first)
    assert replay.analysis["status"] == "complete" and replay_service.calls == []
    assert replay.analysis["objects"] == analysis["objects"]
    assert replay.analysis["observations"] == analysis["observations"]
    assert replay.analysis["budget"]["reserved"] == 3
    assert hashes(first) == old
    assert len(service.requests) == len(service.calls)
    if mode == "json_object":
        assert all("json" in body["messages"][0]["content"].casefold() for body in service.requests)
    for artifact in first.rglob("*"):
        if artifact.is_file():
            assert b"synthetic-install-secret" not in artifact.read_bytes()
    return {"structured_output": mode, "extraction_chunks": result.run.chunk_count,
            "synthetic_http_calls": len(service.requests),
            "analysis_calls": 3, "resume_new_calls": 0, "observations": 3, "table_objects": 2,
            "analysis_budget_limit": 3, "analysis_budget_reserved": analysis["budget"]["reserved"],
            "json_object_prompt_gate": mode == "json_object", "credential_isolation": True,
            "same_names_distinct_ids": True, "sample_not_promoted": True,
            "multiple_conditions_preserved": True, "sqlite_references_valid": True,
            "historical_artifacts_unchanged": True}


def accept_cli(root, mode):
    inputs = root / "cli-inputs"
    inputs.mkdir()
    source, task, config = inputs / "source.md", inputs / "task.json", inputs / "models.toml"
    source.write_bytes(SOURCE.encode("utf-8"))
    task.write_text(json.dumps(TASK), encoding="utf-8")
    config.write_text('[profiles.synthetic]\nbase_url = "https://synthetic.invalid/v1"\n'
                      f'model = "synthetic-analysis"\nstructured_output = "{mode}"\n'
                      'api_key_env = "ATTESTEXTRACT_TEST_API_KEY"\n'
                      'max_attempts = 1\nformat_retries = 0\n'
                      '[profiles.synthetic.generation]\nmax_output_tokens = 2048\n', encoding="utf-8")
    service = Service(mode=mode)
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
    credentials = {"ATTESTEXTRACT_TEST_API_KEY": "synthetic-install-secret",
                   "DEEPSEEK_API_KEY": "unselected-synthetic-account"}
    with patch.dict(os.environ, credentials), redirect_stdout(captured):
        assert cli.main([*arguments, "--output-dir", str(root / "cli-check"), "--check"], registry=registry) == 0
        assert service.calls == []
        assert cli.main([*arguments, "--output-dir", str(root / "cli")], registry=registry) == 0
    before = len(service.calls)
    analysis = read(root / "cli/analysis/analysis.json")
    assert analysis["status"] == "complete" and len(analysis["observations"]) == 3
    assert [call for call in service.calls if call != "extraction"] == ["search", "read", "decide"]
    old = hashes(root / "cli")
    service.forbidden = True
    with patch.dict(os.environ, credentials), redirect_stdout(captured):
        assert cli.main([*arguments, "--output-dir", str(root / "cli-replay"),
                         "--resume", str(root / "cli")], registry=registry) == 0
    assert len(service.calls) == before and hashes(root / "cli") == old
    assert len(service.requests) == before
    if mode == "json_object":
        assert all("json" in body["messages"][0]["content"].casefold() for body in service.requests)
    (root / "cli-terminal.txt").write_text(captured.getvalue(), encoding="utf-8")
    for artifact in (root / "cli").rglob("*"):
        if artifact.is_file():
            assert b"synthetic-install-secret" not in artifact.read_bytes()
            assert b"unselected-synthetic-account" not in artifact.read_bytes()
    return {"cli_check_new_calls": 0, "cli_synthetic_http_calls": before,
            "cli_analysis_calls": 3, "cli_resume_new_calls": 0, "cli_exit_status": 0}


SCOPE_GROUPS = ((1, 2, 3, 4, 5, 6), (7, 8, 9, 10, 11, 12), (13, 14, 15, 16, 17, 18))
SCOPE_UNKNOWN = {6, 12, 18}
SCOPE_IDENTITY = "Measurement M01 was taken on specimen S01."
SCOPE_CLAIMS = tuple(
    ("Measurement M01 has mass 1 g." if number == 1 else
     f"Unassigned report {number} lists mass {number} g; owner not given." if number in SCOPE_UNKNOWN else
     f"Specimen S{number:02d} has mass {number} g.",
     "M01" if number == 1 else None if number in SCOPE_UNKNOWN else f"S{number:02d}", str(number))
    for number in range(1, 19)
)
SCOPE_SOURCE = "\n\n".join([*(item[0] for item in SCOPE_CLAIMS), SCOPE_IDENTITY])
SCOPE_TASK = {"prompt_description": "Extract reported specimen masses; preserve unknown ownership.",
              "field_list": {"mass": "The original mass of each measured specimen"}}


class ScopeService(Service):
    """A frozen 18-observation exercise with independent six-item answer groups."""

    def __init__(self, *, mode, answer_references=False):
        super().__init__(mode=mode)
        self.answer_references = answer_references
        self.decisions = 0
        self.target_ids = []
        self.reference_ids = []
        self.scope_schema = None
        self.identity_by_value = None

    def handle(self, request):
        assert not self.forbidden, "Completed scope acceptance must resume without generation"
        assert request.url.host == "synthetic.invalid" and request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-install-secret"
        body = json.loads(request.content)
        self.requests.append(body)
        assert body["response_format"]["type"] == self.mode
        assert "json" in body["messages"][0]["content"].casefold()
        payload = json.loads(body["messages"][-1]["content"])
        if "source" in payload:
            self.calls.append("extraction")
            records = []
            for quote, entity, value in SCOPE_CLAIMS:
                if quote not in payload["source"]:
                    continue
                values = {"entity": entity, "value": value, "unit": "g", "conditions": None}
                records.append({"record_type": "mass", "values": values,
                                "quotes": {key: quote if val is not None else "" for key, val in values.items()}})
            response = {"records": records}
        else:
            expected = SCOPE_GROUPS[self.decisions]
            targets, references = payload["observations"], payload["extraction_hints"]
            assert tuple(int(item["value"]) for item in targets) == expected
            assert {int(item["value"]) for item in references} == set(range(1, 19)) - set(expected)
            assert len(targets) == 6 and len(references) == 12
            self.target_ids = [item["observation_id"] for item in targets]
            self.reference_ids = [item["reference_observation_id"] for item in references]
            assert payload["target_scope"] == {"observation_ids": self.target_ids, "count": 6}
            assert not set(self.target_ids) & set(self.reference_ids)
            assert all("observation_id" not in item for item in references)
            assert len(set(self.target_ids + self.reference_ids)) == 18
            identities = {int(item["value"]): item["observation_id"] for item in targets}
            identities.update({int(item["value"]): item["reference_observation_id"] for item in references})
            if self.identity_by_value is None:
                self.identity_by_value = identities
                self.scope_schema = payload["response_contract"]
            assert identities == self.identity_by_value
            assert payload["response_contract"] == self.scope_schema
            if self.mode == "json_schema":
                assert body["response_format"]["json_schema"]["schema"] == self.scope_schema
            # Literal task scope must also be visible in the transmitted system
            # instruction, not only understood by this synthetic service.
            system = body["messages"][0]["content"]
            assert "target_scope" in system and "reference_observation_id" in system
            assert "EVERY supplied observation_id" not in system
            response = {"action": "decide", "question": "Identify direct owners within this task scope.",
                        "query": None, "search_offset": 0, "passage_ids": [], "neighbors": 0,
                        "objects": [], "relations": [], "bindings": []}
            history = payload["lookup_history"]
            if expected == SCOPE_GROUPS[0] and not history:
                self.calls.append("search")
                response.update(action="search", query="Measurement M01 was taken")
            elif expected == SCOPE_GROUPS[0] and history[-1]["action"] == "search":
                self.calls.append("read")
                response.update(action="read", passage_ids=[history[-1]["result"]["matches"][0]["passage_id"]])
            else:
                self.calls.append("decide")
                self.decisions += 1
                response.update(self.scoped_decision(payload, expected))
        return httpx.Response(200, json={"model": "synthetic-scope-model", "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(response)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    def scoped_decision(self, payload, expected):
        passages = list(payload["initial_context"]["passages"])
        for frame in payload["lookup_history"]:
            passages.extend(frame["result"].get("matches", []))
            passages.extend(frame["result"].get("passages", []))

        def cite(quote):
            found = next(part for part in passages if quote in part["text"])
            return {"passage_id": found["passage_id"], "quote": quote, "occurrence": None}

        ids = {int(item["value"]): item["observation_id"] for item in payload["observations"]}
        objects, bindings = [], []
        for number in expected:  # Independent answer groups, not every supplied ID.
            unknown = number in SCOPE_UNKNOWN
            evidence = [] if unknown else [cite(SCOPE_CLAIMS[number - 1][0])]
            if number == 1:
                evidence.append(cite(SCOPE_IDENTITY))
            local_id = f"specimen-{number}"
            if not unknown:
                objects.append({"object_id": local_id, "name": f"S{number:02d}", "kind": "sample",
                    "scope": "Synthetic specimen register", "is_table_subject": True, "mentions": evidence})
            bindings.append({"observation_id": ids[number], "subject_id": None if unknown else local_id,
                "status": "unknown" if unknown else "supported", "evidence": evidence,
                "reason": "Owner was not reported." if unknown else "The original statement identifies this specimen.",
                "target_match": "supported", "property_name": "mass", "statistic": None})
        if self.answer_references:
            bindings.extend({"observation_id": item["reference_observation_id"], "subject_id": None,
                "status": "unknown", "evidence": [], "reason": "Synthetic reference-scope violation.",
                "target_match": "unknown", "property_name": None, "statistic": None}
                for item in payload["extraction_hints"])
        return {"objects": objects, "bindings": bindings}


def accept_scope(root, mode, entry):
    root.mkdir()
    service = ScopeService(mode=mode)
    first, replay = root / "first", root / "replay"
    if entry == "python":
        backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-scope-model",
            api_key="synthetic-install-secret", structured_output=mode,
            max_attempts=1, transport=httpx.MockTransport(service.handle))

        def invoke(destination, resume=None):
            return extract(SCOPE_SOURCE, SCOPE_TASK, backend=backend, output_dir=destination, progress="quiet",
                execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1), max_chunk_chars=6000,
                table_subject="one individual specimen", max_analysis_calls=5, resume_from=resume)

        assert invoke(first).analysis["status"] == "complete"
    else:
        source, task, config = root / "source.md", root / "task.json", root / "models.toml"
        source.write_bytes(SCOPE_SOURCE.encode("utf-8"))
        task.write_text(json.dumps(SCOPE_TASK), encoding="utf-8")
        config.write_text('[profiles.synthetic]\nbase_url = "https://synthetic.invalid/v1"\n'
            f'model = "synthetic-scope-model"\nstructured_output = "{mode}"\n'
            'api_key_env = "ATTESTEXTRACT_TEST_API_KEY"\nmax_attempts = 1\nformat_retries = 0\n', encoding="utf-8")
        registry = BackendRegistry()

        def factory(*, config, api_key, trace):
            return OpenAICompatibleBackend(config.base_url, config.model, api_key=api_key, trace=trace,
                structured_output=config.structured_output, transport=httpx.MockTransport(service.handle))

        registry.register("openai-compatible", factory)
        arguments = ["extract", "--input", str(source), "--task", str(task), "--config", str(config),
            "--profile", "synthetic", "--chunking", "fixed", "--max-chunk-chars", "6000",
            "--max-model-calls", "1", "--max-analysis-calls", "5", "--table-subject", "one individual specimen",
            "--progress", "quiet"]

        def invoke(destination, resume=None):
            extra = ["--resume", str(resume)] if resume else []
            with patch.dict(os.environ, {"ATTESTEXTRACT_TEST_API_KEY": "synthetic-install-secret"}), redirect_stdout(io.StringIO()):
                return cli.main([*arguments, *extra, "--output-dir", str(destination)], registry=registry)

        assert invoke(first) == 0
    analysis = read(first / "analysis/analysis.json")
    assert analysis["status"] == "complete" and len(analysis["observations"]) == 18
    assert service.calls == ["extraction", "search", "read", "decide", "decide", "decide"]
    by_value = {int(item["value"]): item for item in analysis["observations"]}
    names = {item["object_id"]: item["name"] for item in analysis["objects"]}
    for number in range(1, 19):
        item = by_value[number]
        assert item["unit"] == "g" and item["value"] == str(number)
        assert item["binding_status"] == ("unknown" if number in SCOPE_UNKNOWN else "supported")
        if number in SCOPE_UNKNOWN:
            assert item["subject_id"] is None
        else:
            assert names[item["subject_id"]] == f"S{number:02d}"
    assert len(by_value[1]["binding_evidence_ids"]) == 2
    old = hashes(first)
    service.forbidden = True
    resumed = invoke(replay, first)
    if entry == "cli":
        assert resumed == 0
    else:
        assert resumed.analysis["status"] == "complete"
    assert len(service.calls) == 6 and hashes(first) == old
    for artifact in first.rglob("*"):
        if artifact.is_file():
            assert b"synthetic-install-secret" not in artifact.read_bytes()
    return {"observations": 18, "targets_per_job": 6, "readonly_references_per_job": 12,
            "supported": 15, "unknown": 3, "analysis_calls": 5, "synthetic_http_calls": 6,
            "resume_new_calls": 0, "cross_paragraph_evidence": True, "passed": True}


AMBIGUITY_CLAIMS = tuple(
    (f"Specimen A{number:02d} has mass {number} g." +
     (" Its label is Specimen A01." if number == 1 else ""), f"A{number:02d}", str(number))
    for number in range(1, 13)
)
AMBIGUITY_SOURCE = "\n\n".join(item[0] for item in AMBIGUITY_CLAIMS)


class AmbiguityService(Service):
    """Two independent answer groups; the first has one repeated owner quote."""

    def __init__(self, *, mode, invalid_after_ambiguity=False, lookup_only=False):
        super().__init__(mode=mode)
        self.invalid_after_ambiguity = invalid_after_ambiguity
        self.lookup_only = lookup_only

    def handle(self, request):
        assert not self.forbidden, "Quarantined and completed groups must resume without generation"
        assert request.url.host == "synthetic.invalid" and request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-install-secret"
        body = json.loads(request.content)
        self.requests.append(body)
        assert body["response_format"]["type"] == self.mode
        payload = json.loads(body["messages"][-1]["content"])
        if "source" in payload:
            self.calls.append("extraction")
            records = []
            for quote, entity, value in AMBIGUITY_CLAIMS:
                assert quote in payload["source"]
                values = {"entity": entity, "value": value, "unit": "g", "conditions": None}
                records.append({"record_type": "mass", "values": values,
                                "quotes": {key: quote if val is not None else "" for key, val in values.items()}})
            response = {"records": records}
        else:
            targets = payload["observations"]
            numbers = tuple(int(item["value"]) for item in targets)
            assert numbers in ((1, 2, 3, 4, 5, 6), (7, 8, 9, 10, 11, 12))
            # The rejected first group's proposals must not become context for
            # the second group, even though they were valid object declarations.
            assert payload["known_objects"] == []
            self.calls.append("search" if self.lookup_only else "decide")
            response = {"action": "search" if self.lookup_only else "decide",
                "question": "Check the reported owner of each specimen mass.",
                "query": "Specimen" if self.lookup_only else None, "search_offset": 0,
                "passage_ids": [], "neighbors": 0, "objects": [], "relations": [], "bindings": []}
            if not self.lookup_only:
                passages = payload["initial_context"]["passages"]

                def cite(quote):
                    passage = next(item for item in passages if quote in item["text"])
                    return {"passage_id": passage["passage_id"], "quote": quote, "occurrence": None}

                for number, target in zip(numbers, targets):
                    quote, name, _ = AMBIGUITY_CLAIMS[number - 1]
                    local_id = f"specimen-{number}"
                    response["objects"].append({"object_id": local_id, "name": name, "kind": "sample",
                        "scope": "Synthetic specimen register", "is_table_subject": True,
                        "mentions": [cite("Specimen A01" if number == 1 else quote)]})
                    response["bindings"].append({"observation_id": target["observation_id"],
                        "subject_id": local_id, "status": "supported", "evidence": [cite(quote)],
                        "reason": "The stated specimen owns this mass.", "target_match": "supported",
                        "property_name": "mass", "statistic": None})
                if numbers[0] == 1 and self.invalid_after_ambiguity:
                    response["bindings"][-1]["subject_id"] = "undeclared-owner"
        return httpx.Response(200, json={"model": "synthetic-ambiguity-model", "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(response)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})


def ambiguity_entry(root, mode, entry, *, service=None, max_calls=2):
    """Build an ordinary public entry with a synthetic transport only."""
    root.mkdir()
    service = service or AmbiguityService(mode=mode)
    if entry == "python":
        backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-ambiguity-model",
            api_key="synthetic-install-secret", structured_output=mode,
            max_attempts=1, transport=httpx.MockTransport(service.handle))

        def invoke(destination, resume=None):
            return extract(AMBIGUITY_SOURCE, SCOPE_TASK, backend=backend, output_dir=destination, progress="quiet",
                execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1), max_chunk_chars=6000,
                table_subject="one individual specimen", max_analysis_calls=max_calls, resume_from=resume)
    else:
        source, task, config = root / "source.md", root / "task.json", root / "models.toml"
        source.write_bytes(AMBIGUITY_SOURCE.encode("utf-8"))
        task.write_text(json.dumps(SCOPE_TASK), encoding="utf-8")
        config.write_text('[profiles.synthetic]\nbase_url = "https://synthetic.invalid/v1"\n'
            f'model = "synthetic-ambiguity-model"\nstructured_output = "{mode}"\n'
            'api_key_env = "ATTESTEXTRACT_TEST_API_KEY"\nmax_attempts = 1\nformat_retries = 0\n', encoding="utf-8")
        registry = BackendRegistry()

        def factory(*, config, api_key, trace):
            return OpenAICompatibleBackend(config.base_url, config.model, api_key=api_key, trace=trace,
                structured_output=config.structured_output, transport=httpx.MockTransport(service.handle))

        registry.register("openai-compatible", factory)
        arguments = ["extract", "--input", str(source), "--task", str(task), "--config", str(config),
            "--profile", "synthetic", "--chunking", "fixed", "--max-chunk-chars", "6000",
            "--max-model-calls", "1", "--max-analysis-calls", str(max_calls),
            "--table-subject", "one individual specimen", "--progress", "quiet"]

        def invoke(destination, resume=None):
            extra = ["--resume", str(resume)] if resume else []
            with patch.dict(os.environ, {"ATTESTEXTRACT_TEST_API_KEY": "synthetic-install-secret"}), redirect_stdout(io.StringIO()):
                return cli.main([*arguments, *extra, "--output-dir", str(destination)], registry=registry)
    return service, invoke


def accept_ambiguity(root, mode, entry):
    service, invoke = ambiguity_entry(root, mode, entry)
    first, replay = root / "first", root / "replay"
    outcome = invoke(first)
    if entry == "cli":
        assert outcome == 3
    else:
        assert outcome.analysis["status"] == "partial"
    analysis = read(first / "analysis/analysis.json")
    assert analysis["analysis_version"] == "0.3" and analysis["status"] == "partial"
    assert [job["status"] for job in analysis["jobs"]] == ["review_required", "completed"]
    assert analysis["budget"]["reserved"] == 2 and service.calls == ["extraction", "decide", "decide"]
    by_value = {int(item["value"]): item for item in analysis["observations"]}
    assert set(by_value) == set(range(1, 13))
    for number, item in by_value.items():
        assert item["value"] == str(number) and item["unit"] == "g"
        assert item["binding_status"] == ("review_required" if number <= 6 else "supported")
        if number <= 6:
            assert item["subject_id"] is None and item["binding_evidence_ids"] == []
    assert {item["name"] for item in analysis["objects"]} == {f"A{n:02d}" for n in range(7, 13)}
    assert analysis["relations"] == []
    assert len(analysis["review_groups"]) == 1
    assert set(analysis["review_groups"][0]["observation_ids"]) == {by_value[n]["observation_id"] for n in range(1, 7)}
    step = analysis["jobs"][0]["steps"][-1]
    diagnostic = first / "diagnostics" / step["validation_details"]
    details = read(diagnostic)
    assert details["code"] == "quote_ambiguous" and details["ambiguity_count"] == 1
    ambiguous = details["ambiguities"][0]
    assert ambiguous["total_match_count"] == ambiguous["visible_match_count"] == 2
    assert ambiguous["quote"] == "Specimen A01"
    assert len({match["start"] for match in ambiguous["matches"]}) == 2
    for match in ambiguous["matches"]:
        assert AMBIGUITY_SOURCE[match["start"]:match["end"]] == "Specimen A01"
        context = match["context"]
        assert AMBIGUITY_SOURCE[context["start"]:context["end"]] == context["text"]
        assert context["model_visible"] is True
    raw_path = first / "diagnostics/analysis/responses/verify-0001.step-01.txt"
    raw = read(raw_path)
    assert len(raw["objects"]) == len(raw["bindings"]) == 6
    assert raw["objects"][0]["mentions"][0]["quote"] == "Specimen A01"
    assert raw["objects"][0]["mentions"][0]["occurrence"] is None
    with (first / "analysis/object_table.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 6
    with sqlite3.connect(first / "analysis/analysis.sqlite") as db:
        assert db.execute("SELECT count(*) FROM objects").fetchone()[0] == 6
        assert db.execute("SELECT count(*) FROM observations WHERE subject_id IS NULL").fetchone()[0] == 6
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    old = hashes(first)
    service.forbidden = True
    resumed = invoke(replay, first)
    if entry == "cli":
        assert resumed == 3
    else:
        assert resumed.analysis["status"] == "partial"
    assert hashes(first) == old and len(service.calls) == 3
    restored = read(replay / "analysis/analysis.json")
    assert restored["objects"] == analysis["objects"] and restored["observations"] == analysis["observations"]
    assert (replay / "diagnostics/analysis/responses/verify-0001.step-01.txt").read_bytes() == raw_path.read_bytes()
    assert read(replay / "diagnostics" / step["validation_details"]) == read(diagnostic)
    for artifact in first.rglob("*"):
        if artifact.is_file():
            assert b"synthetic-install-secret" not in artifact.read_bytes()
    return {"passed": True, "observations": 12, "review_groups": 1, "review_observations": 6,
            "supported_observations": 6, "analysis_calls": 2, "synthetic_http_calls": 3,
            "resume_new_calls": 0, "analysis_status": "partial", "cli_exit_status": 3 if entry == "cli" else None,
            "known_objects_unpolluted": True, "original_response_preserved": True}


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
        assert "json" not in SOURCE.casefold() and "json" not in json.dumps(TASK).casefold()
        modes = {}
        for mode in ("json_schema", "json_object"):
            directory = root / mode
            directory.mkdir()
            modes[mode] = accept(directory, mode)
            modes[mode].update(accept_cli(directory, mode))
            modes[mode]["scope_acceptance"] = {entry: accept_scope(directory / ("scope-" + entry), mode, entry)
                                                for entry in ("python", "cli")}
            modes[mode]["ambiguity_acceptance"] = {entry: accept_ambiguity(directory / ("ambiguity-" + entry), mode, entry)
                                                    for entry in ("python", "cli")}
    assert not network_attempts
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": str(module_path), "installed_package_verified": True,
               "network_attempts": 0, "real_model_calls": 0, "modes": modes,
               "synthetic_http_calls": sum(item["synthetic_http_calls"] + item["cli_synthetic_http_calls"] +
                                           sum(scope["synthetic_http_calls"] for scope in item["scope_acceptance"].values())
                                           + sum(check["synthetic_http_calls"] for check in item["ambiguity_acceptance"].values())
                                           for item in modes.values()),
               "notice": "Synthetic offline acceptance; not provider or scientific-quality validation."}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

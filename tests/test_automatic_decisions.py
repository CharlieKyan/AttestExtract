"""Synthetic multi-model provenance survives reports and offline decision export."""

import json

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.archive import create_archive
from attestextract.automatic import ModelConnection
from attestextract.decision_bundle import DecisionBundle, prepare_decision_bundle
from attestextract.decision_reporting import render_decision_bundle
from attestextract.diagnostics import RunTrace
from attestextract.documents import Document
from attestextract.execution import digest
from attestextract.reporting import render_markdown_report
from attestextract.storage import load_archive


TEXT = "Lamp A: 8 W.\n\nLamp B: 9 W."
TASK = {"name": "Synthetic power", "record_types": {"reading": {"fields": {
    "subject": {"type": "string", "required": True},
    "power": {"type": "integer", "required": True},
}}}}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _record(subject, power):
    quote = f"{subject}: {power} W."
    return json.dumps({"records": [{"record_type": "reading",
        "values": {"subject": subject, "power": power},
        "quotes": {"subject": quote, "power": quote}}]})


def _run(tmp_path, monkeypatch):
    """Two blocks, three HTTP generations; only block two uses the fallback."""
    posts = []

    def send(client, request, **kwargs):
        if request.method == "GET":
            return httpx.Response(401, request=request)
        payload = json.loads(request.content)
        posts.append((request.url.host, payload["model"]))
        text = [_record("Lamp A", 8), "{broken", _record("Lamp B", 9)][len(posts) - 1]
        return httpx.Response(200, request=request, json={
            "id": f"test-{len(posts)}", "model": payload["model"] + "-reported",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        })

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "cache"))
    directory = tmp_path / "run"
    trace = RunTrace(directory / "diagnostics")
    result = extract(TEXT, TASK, model="novel-primary", base_url="https://primary.invalid/v1",
                     api_key="synthetic-primary-key-not-real", trace=trace,
                     fallback=ModelConnection(base_url="https://backup.invalid/v1", model="novel-backup",
                                              api_key="synthetic-backup-key-not-real"),
                     max_chunk_chars=14, source_name="synthetic.txt",
                     execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1))
    trace.finish(result.model_dump(mode="json"))
    assert posts == [("primary.invalid", "novel-primary"),
                     ("primary.invalid", "novel-primary"), ("backup.invalid", "novel-backup")]
    return directory, result


def test_mixed_sources_are_preserved_without_exporting_connections(tmp_path, monkeypatch):
    directory, result = _run(tmp_path, monkeypatch)
    assert result.schema_version == "0.5"
    assert result.run.model is None
    bundle = prepare_decision_bundle(directory)
    assert bundle.extraction == result
    assert len(bundle.batches) == len(bundle.tasks) == 2
    assert [candidate.fields["power"].value for candidate in result.candidates] == [8, 9]
    assert [source.model for source in bundle.extraction.run.chunk_provenance.values()] == [
        "novel-primary-reported", "novel-backup-reported"]
    assert [bundle.extraction.run.routes[source.route_id]["config"]["model"]
            for source in bundle.extraction.run.chunk_provenance.values()] == ["novel-primary", "novel-backup"]
    serialized = bundle.model_dump_json()
    for private in ("primary.invalid", "backup.invalid", "synthetic-primary-key-not-real",
                    "synthetic-backup-key-not-real", "provider_options", "api_key_env"):
        assert private not in serialized
    report = render_markdown_report(result)
    decision_report = render_decision_bundle(bundle)
    for rendered in (report, decision_report):
        assert r"novel\-primary" in rendered and r"novel\-backup" in rendered
        assert r"novel\-backup\-reported" in rendered
        assert "实际" in rendered
    assert "已预留生成尝试：3" in report


@pytest.mark.parametrize("kind", ["cache_hash", "cache_format", "provenance", "route"])
def test_corrupted_automatic_sources_stop_decision_export(tmp_path, monkeypatch, kind):
    directory, result = _run(tmp_path, monkeypatch)
    diagnostics = directory / "diagnostics"
    checkpoint_path = diagnostics / "execution/checkpoint.json"
    checkpoint = _read(checkpoint_path)
    if kind.startswith("cache"):
        cache_path = diagnostics / "execution/cache/chunk-0002.json"
        cache = _read(cache_path)
        cache["text"] = "invalid json"
        _write(cache_path, cache)
        if kind == "cache_format":
            checkpoint["state"]["nodes"]["chunk-0002"]["cache_hash"] = digest(cache)
    elif kind == "provenance":
        checkpoint["state"]["chunk_provenance"]["chunk-0002"]["route_id"] = "missing-route"
    else:
        checkpoint["state"]["route_plan"]["routes"][0]["config"]["model"] = "modified-model"
    checkpoint["sha256"] = digest(checkpoint["state"])
    _write(checkpoint_path, checkpoint)
    with pytest.raises(ValueError):
        prepare_decision_bundle(directory)


@pytest.mark.parametrize("kind", ["duplicate_attempt", "missing_source", "foreign_source", "service_config"])
def test_self_contained_bundle_rejects_false_source_maps(tmp_path, monkeypatch, kind):
    directory, result = _run(tmp_path, monkeypatch)
    payload = prepare_decision_bundle(directory).model_dump()
    run = payload["extraction"]["run"]
    if kind == "duplicate_attempt":
        run["generation_attempts"].append(run["generation_attempts"][0])
    elif kind == "missing_source":
        run["chunk_provenance"].pop("chunk-0002")
    elif kind == "foreign_source":
        run["chunk_provenance"]["chunk-0002"] = run["chunk_provenance"]["chunk-0001"]
    else:
        next(iter(run["routes"].values()))["config"]["api_key"] = "must-not-export"
    # A checksum is not a substitute for the structural source-map validation.
    payload["bundle_id"] = "decision-bundle-" + digest({k: v for k, v in payload.items() if k != "bundle_id"})[:24]
    with pytest.raises(ValueError):
        DecisionBundle.model_validate(payload)


@pytest.mark.parametrize("kind", ["missing_route", "duplicate_attempt", "wrong_totals", "unknown_chunk", "http_multiplier"])
def test_archive_and_report_reject_corrupted_source_maps(tmp_path, monkeypatch, kind):
    directory, result = _run(tmp_path, monkeypatch)
    document = Document.model_validate(_read(directory / "diagnostics/document.json"))
    archive = create_archive(document, result)
    damaged = result.model_copy(deep=True)
    if kind == "missing_route":
        damaged.run.chunk_provenance["chunk-0002"].route_id = "nonexistent-route"
    elif kind == "duplicate_attempt":
        damaged.run.generation_attempts.append(damaged.run.generation_attempts[0])
    elif kind == "wrong_totals":
        damaged.run.usage["completion_tokens"] += 1
    elif kind == "unknown_chunk":
        damaged.candidates[0].chunk_ids = ["chunk-9999"]
    else:
        damaged.run.generation_attempts[0].http_attempts = 2
        damaged.run.request_attempts += 1
    with pytest.raises(ValueError):
        create_archive(document, damaged)
    with pytest.raises(ValueError):
        render_markdown_report(damaged)
    payload = archive.model_dump(mode="json")
    payload["extraction"] = damaged.model_dump(mode="json")
    destination = tmp_path / "tampered-archive.json"
    _write(destination, payload)
    with pytest.raises(ValueError):
        load_archive(destination)

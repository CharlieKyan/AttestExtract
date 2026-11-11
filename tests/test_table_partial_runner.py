"""Versioned partial parsing and historical request provenance, without network."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse
from attestextract.table_routes import build_table_input

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import table_route_runner as runner


def material():
    source = "| Sample | Fraction |\n|---|---|\n| S | 0.2 |\n"
    index = build_table_input(source)
    cell = next(c for c in index["entries"] if c["quote"] == "0.2")
    selection = {"ref": cell["ref"], "quote": "0.2", "offset": 0}
    record = {"record_id": "valid", "record_type": "measurement",
              "fields": {"value": {"value": "0.2", "sources": [selection], "relation": "anchor"},
                         "unit": {"value": None, "sources": []}},
              "anchors": [selection], "scope": "individual", "scope_sources": []}
    case = {"id": "sample", "source": source, "task": {"name": "Fraction",
            "record_types": {"measurement": {"fields": {"value": {"type": "string"},
            "unit": {"type": "string", "required": False}}}}}, "gold": [],
            "replays": {"A": {"records": [record]}}}
    return case, record


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    monkeypatch.setattr(runner, "runtime_identity", lambda: {"test": "partial-v2"})


def test_partial_error_preserves_valid_records_and_is_not_resent(tmp_path):
    case, good = material()
    bad = deepcopy(good)
    bad["record_id"] = "bad"
    del bad["fields"]["value"]["relation"]
    case["replays"]["A"]["records"].append(bad)
    root = tmp_path / "partial"
    runner.prepare_bundle(root, [case])
    result = runner.run_bundle(root)
    assert result["statuses"] == {"partial_format_error": 1}
    trial = root / "trials/r1-sample-A"
    parsed = runner.read_json(trial / "parsed.json")
    assert parsed["record_indices"] == [0]
    assert parsed["rejections"][0]["index"] == 1
    assert parsed["rejections"][0]["raw"] == bad
    assert "relation" in str(parsed["rejections"][0]["errors"])
    exported = runner.read_json(trial / "results.json")
    assert len(exported["candidates"]) == 1
    assert exported["candidates"][0]["values"] == {"value": "0.2", "unit": None}
    assert exported["semantic_verification"] == "not_run"
    before = (trial / "outcome.json").read_bytes()
    assert runner.run_bundle(root)["remaining_slots"] == 0
    assert (trial / "outcome.json").read_bytes() == before


def test_all_invalid_records_are_format_error_with_visible_rejections(tmp_path):
    case, good = material()
    case["replays"]["A"]["records"] = [good, deepcopy(good)]
    root = tmp_path / "duplicates"
    runner.prepare_bundle(root, [case])
    assert runner.run_bundle(root)["statuses"] == {"format_error": 1}
    trial = root / "trials/r1-sample-A"
    parsed = runner.read_json(trial / "parsed.json")
    assert parsed["records"] == []
    assert [r["index"] for r in parsed["rejections"]] == [0, 1]
    assert runner.read_json(trial / "results.json")["candidates"] == []


@pytest.mark.parametrize("text", ['{"records":[],"records":[]}', '{"records":[',
                                  '{"records":[],"extra":true}'])
def test_invalid_envelope_remains_whole_response_error(tmp_path, text):
    case, _ = material()
    case["replays"]["A"] = text
    root = tmp_path / "bad-envelope"
    runner.prepare_bundle(root, [case])
    assert runner.run_bundle(root)["statuses"] == {"format_error": 1}
    trial = root / "trials/r1-sample-A"
    assert not (trial / "parsed.json").exists()
    assert runner.read_json(trial / "error.json")["phase"] == "parse"


def test_previous_a_manifest_only_imports_offline_and_preserves_original_request(tmp_path):
    case, _ = material()
    old, new = tmp_path / "old", tmp_path / "new"
    runner.prepare_bundle(old, [case])
    runner.run_bundle(old)
    manifest = runner.read_json(old / "manifest.json")
    manifest["schema_version"] = "table-a-0.1"
    (old / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    with pytest.raises(ConfigurationError, match="read-only"):
        runner.run_bundle(old)
    runner.fork_replay(old, new, {})
    files = runner.load_bundle(new)["replay_provenance"]["r1-sample-A"]
    assert files["request.json"] == runner.read_json(old / "trials/r1-sample-A/request.json")
    assert files["response.json"] == runner.read_json(old / "trials/r1-sample-A/response.json")
    assert files["origin.json"]["mode"] == "replay"
    assert not files["origin.json"]["overridden"]
    assert runner.run_bundle(new)["statuses"] == {"success": 1}
    second = tmp_path / "second"
    runner.fork_replay(new, second, {})
    assert runner.load_bundle(second)["replay_provenance"]["r1-sample-A"] == files
    assert {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()} == before
    (new / "replay-provenance/r1-sample-A/request.json").write_text('{}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="provenance changed"):
        runner.load_bundle(new)


def test_import_preserves_truncation_even_when_saved_text_is_valid_json(tmp_path):
    case, _ = material()
    old, new = tmp_path / "old", tmp_path / "new"
    runner.prepare_bundle(old, [case])
    class Backend:
        def generate(self, request):
            return BackendResponse(text=json.dumps(case["replays"]["A"]), finish_reason="length")
    runner.run_bundle(old, mode="live", backend_factory=lambda **kwargs: Backend())
    runner.fork_replay(old, new, {})
    assert runner.run_bundle(new)["statuses"] == {"format_error": 1}
    assert runner.read_json(new / "trials/r1-sample-A/error.json")["code"] == "output_truncated"
    explicit = tmp_path / "override"
    runner.fork_replay(old, explicit, {"r1-sample-A": case["replays"]["A"]})
    assert runner.load_bundle(explicit)["replay_provenance"]["r1-sample-A"]["origin.json"]["overridden"]
    assert runner.run_bundle(explicit)["statuses"] == {"success": 1}


def test_import_reuses_exact_model_visible_index_without_rebuilding(tmp_path, monkeypatch):
    import attestextract.table_routes as core
    case, _ = material()
    old, new = tmp_path / "old", tmp_path / "new"
    runner.prepare_bundle(old, [case])
    runner.run_bundle(old)
    original = runner.read_json(old / "table-inputs.json")
    def forbidden_rebuild(*args, **kwargs):
        raise AssertionError("Old model answers must use their original visible table index")
    monkeypatch.setattr(core, "build_table_input", forbidden_rebuild)
    runner.fork_replay(old, new, {})
    assert runner.load_bundle(new)["table_inputs"] == original
    runner.run_bundle(new)
    exported = runner.read_json(new / "trials/r1-sample-A/results.json")
    assert exported["evidence"]["table_input"] == original["sample"]

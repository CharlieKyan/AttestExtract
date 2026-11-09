"""Synthetic frozen plans: no provider requests, credentials or research files."""

from collections import Counter
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from attestextract.errors import ConfigurationError


SCRIPT = Path(__file__).parents[1] / "scripts/provider_experiment_bundle.py"
spec = importlib.util.spec_from_file_location("provider_experiment_bundle_under_test", SCRIPT)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Runtime-change tests isolate the fingerprint; concurrent editor changes
    # and machine-specific proxy settings are not part of this synthetic case.
    monkeypatch.setattr(bundle, "runtime_identity", lambda: {"engine": "synthetic-frozen-code"})
    monkeypatch.setattr(bundle, "network_identity", lambda: {"proxy": "synthetic-frozen-network"})
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Preparation must never send requests"))
    source, task = tmp_path / "paper.md", tmp_path / "task.json"
    source.write_text("\n\n".join(f"Item {index:02} reads 11. " + "Synthetic background. " * 170 for index in range(16)), encoding="utf-8")
    task.write_text(json.dumps({"name": "Synthetic paper", "record_types": {"reading": {
        "fields": {"value": {"type": "integer"}}}}}), encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "secret-marker-deepseek", "VLM_API_KEY": "secret-marker-qwen",
                   "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    output = tmp_path / "bundle"
    manifest = bundle.prepare_bundle(output, source, task, environment)
    return output, environment, manifest, source, task


def test_exact_budget_and_request_deduplication(prepared):
    root, environment, manifest, _, _ = prepared
    data = bundle.load_bundle(root, environment)
    assert manifest["network_calls"] == 0
    assert Counter(row["stage"] for row in data["rows"]) == {"A": 16, "B": 36, "C": 24, "D": 32}
    assert len(data["requests"]) == 19 and len(data["configs"]) == 12
    assert manifest["max_model_calls"] == 108
    assert manifest["max_output_tokens_total"] == 626688
    assert [row["sequence"] for row in data["rows"]] == list(range(1, 109))
    assert len({row["id"] for row in data["rows"]}) == 108
    assert data["extraction"]["options"]["max_split_depth"] == 0
    assert data["extraction"]["options"]["max_model_calls"] == 16
    assert data["cases"]["paper"]["source"] == data["sources"]["paper"]["text"]
    assert data["cases"]["paper"]["gold"] is None
    assert all(row["request"]["output_protocol"] == "flat-v1" for row in data["rows"])
    assert all(row["request"]["generation"]["max_output_tokens"] == 8192
               for row in data["rows"] if row["stage"] in {"A", "D"})


def test_b_pairs_share_group_and_rotate_tls_instead_of_content(prepared):
    root, _, _, _, _ = prepared
    data = bundle.load_bundle(root)
    rows = [row for row in data["rows"] if row["stage"] == "B"]
    expected_orders = [("auto", "1.2", "auto_fallback"), ("1.2", "auto_fallback", "auto"),
                       ("auto_fallback", "auto", "1.2")]
    for round_id, order in enumerate(expected_orders, 1):
        selected = [row for row in rows if row["round"] == round_id]
        providers = ["deepseek", "qwen"] if round_id == 2 else ["qwen", "deepseek"]
        assert [selected[index]["provider"] for index in range(0, 12, 2)] == providers * 3
        assert [data["configs"][selected[index]["config_key"]]["tls_mode"] for index in range(0, 12, 4)] == list(order)
        for index in range(0, 12, 2):
            short, long = selected[index:index + 2]
            assert short["group_id"] == long["group_id"]
            assert short["case_id"] == "d1-observation"
            assert long["case_id"] == "paper-chunk-0012"
    assert len({row["group_id"] for row in rows}) == 18


def test_c_and_d_freeze_all_choices_and_keep_gold_out_of_prompts(prepared):
    root, _, _, _, _ = prepared
    data = bundle.load_bundle(root)
    c = [row for row in data["rows"] if row["stage"] == "C"]
    assert Counter(row["path_id"] for row in c) == dict.fromkeys(bundle.PATHS, 6)
    assert len(data["cases"]["d1-attribute"]["gold"]) == 1
    assert len(data["cases"]["d1-observation"]["gold"]) == 2
    for row in c:
        assert len(row["config_alternatives"]) == 3
        assert row["request"]["generation"]["max_output_tokens"] == 2048
        content = json.loads(row["request"]["messages"][-1]["content"])
        assert "gold" not in content and "expected" not in content
        assert content["source"] == data["cases"][row["case_id"]]["chunk"]["text"]
    for row in [row for row in data["rows"] if row["stage"] == "D"]:
        assert len(row["config_alternatives"]) == (3 if row["provider"] == "qwen" else 9)


def test_no_credentials_serialized_and_rotation_does_not_change_identity(prepared):
    root, environment, _, _, _ = prepared
    assert "secret-marker" not in "".join(p.read_text(encoding="utf-8") for p in root.iterdir())
    changed = {**environment, "DEEPSEEK_API_KEY": "rotated-deepseek", "VLM_API_KEY": "rotated-qwen"}
    assert bundle.load_bundle(root, changed)["manifest"]["network_calls"] == 0
    changed["VLM_BASE_URL"] = "https://another.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    with pytest.raises(ConfigurationError, match="bindings"):
        bundle.load_bundle(root, changed)


@pytest.mark.parametrize("name", bundle.ARTIFACTS)
def test_changed_artifact_rejected_before_execution(prepared, name):
    root, _, _, _, _ = prepared
    (root / name).write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="damaged"):
        bundle.load_bundle(root)


def test_manifest_path_traversal_rejected_even_with_recalculated_id(prepared):
    root, _, manifest, _, _ = prepared
    manifest["files"]["../outside.json"] = "a" * 64
    manifest["bundle_id"] = "provider-experiment-" + bundle.digest({k: v for k, v in manifest.items() if k != "bundle_id"})
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="damaged"):
        bundle.load_bundle(root)


def test_changed_engine_or_network_refuses_reuse(prepared, monkeypatch):
    root, _, _, _, _ = prepared
    monkeypatch.setattr(bundle, "runtime_identity", lambda: {"engine": "changed"})
    with pytest.raises(ConfigurationError, match="changed"):
        bundle.load_bundle(root)
    monkeypatch.setattr(bundle, "runtime_identity", lambda: {"engine": "synthetic-frozen-code"})
    monkeypatch.setattr(bundle, "network_identity", lambda: {"proxy": "changed"})
    with pytest.raises(ConfigurationError, match="changed"):
        bundle.load_bundle(root)


def test_existing_bundle_and_nonapproved_chunk_count_never_overwritten(prepared):
    root, environment, _, source, task = prepared
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(ConfigurationError, match="new experiment"):
        bundle.prepare_bundle(root, source, task, environment)
    assert (root / "manifest.json").read_bytes() == before
    other = root.parent / "unapproved"
    with pytest.raises(ConfigurationError, match="exactly 16"):
        bundle.prepare_bundle(other, source, task, environment, expected_chunks=15)
    assert not other.exists()
    source.write_text("One short synthetic statement.", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="16-chunk"):
        bundle.prepare_bundle(other, source, task, environment)
    assert not other.exists()


def test_public_repo_output_refused_before_any_material_is_written(prepared):
    _, environment, _, source, task = prepared
    with pytest.raises(ConfigurationError, match="outside the public"):
        bundle.prepare_bundle(SCRIPT.parents[1] / "outputs/forbidden-private-bundle", source, task, environment)


def test_manual_checklist_is_separate_from_model_requests_and_not_automatic_gold(prepared):
    root, environment, _, source, task = prepared
    first = bundle.load_bundle(root)
    checklist = first["paper_checklist"]
    assert checklist["status"] == "not_reviewed"
    assert len(checklist["items"]) == 16
    quote = "Item 00 reads 11."
    checklist["items"][0]["values_and_units"] = [{"quote": quote, "start": 0, "end": len(quote)}]
    checklist["items"][0]["status"] = "manual_source_checked"
    checklist["items"][0]["notes"] = "synthetic manual note not for the model"
    other = root.parent / "with-checklist"
    bundle.prepare_bundle(other, source, task, environment, paper_checklist=checklist)
    loaded = bundle.load_bundle(other)
    assert loaded["paper_checklist"] == checklist
    assert loaded["requests"] == first["requests"]
    assert loaded["cases"]["paper"]["gold"] is None


@pytest.mark.parametrize("damage", ["source", "position", "review_status", "chunk_count"])
def test_invalid_checklist_cannot_be_frozen(prepared, damage):
    root, environment, _, source, task = prepared
    checklist = bundle.load_bundle(root)["paper_checklist"]
    if damage == "source":
        checklist["source_sha256"] = "a" * 64
    elif damage == "position":
        checklist["items"][0]["conditions"] = [{"quote": "wrong", "start": 0, "end": 5}]
    elif damage == "review_status":
        checklist["status"] = "manual_source_checked"
    else:
        checklist["items"].pop()
    other = root.parent / "bad-checklist"
    with pytest.raises(ConfigurationError, match="checklist|Checklist"):
        bundle.prepare_bundle(other, source, task, environment, paper_checklist=checklist)
    assert not other.exists()


def test_runtime_and_network_bind_dependency_and_default_ca_contents(tmp_path, monkeypatch):
    identity = bundle.runtime_identity()
    assert identity["httpcore"] == bundle.httpcore.__version__
    assert identity["pydantic"] == bundle.pydantic.__version__
    assert identity["certifi"] == bundle.certifi.__version__
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    ca_file = tmp_path / "synthetic-ca.pem"
    ca_file.write_bytes(b"synthetic certificate bundle one")
    monkeypatch.setattr(bundle.certifi, "where", lambda: str(ca_file))
    first = bundle.network_identity()
    ca_file.write_bytes(b"synthetic certificate bundle two")
    second = bundle.network_identity()
    assert first["default_ca_sha256"] != second["default_ca_sha256"]
    assert str(ca_file) not in json.dumps(second)

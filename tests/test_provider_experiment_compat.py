"""Bounded legacy reads never relax extraction, material or transport identity."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

from attestextract.errors import ConfigurationError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import provider_experiment_bundle as bundle
import provider_experiment_compat as compat


def historical_identity():
    return {
        "package_version": "0.1.0.dev22", "python": "3.13.11", "system": "Windows",
        "openssl": "synthetic OpenSSL", "httpx": "0.28.1", "httpcore": "1.0.9",
        "pydantic": "2.14.1", "certifi": "2026.10.19",
        "package_files": {name: hashes[0] for name, hashes in compat.DEV22_PACKAGE_HASHES.items()},
        "experiment_scripts": {name: hashes[0] for name, hashes in compat.DEV22_SCRIPT_HASHES.items()},
    }


def current_identity():
    identity = historical_identity()
    identity["package_version"] = "0.1.0.dev23"
    identity["package_files"]["__init__.py"] = compat.DEV23_INIT_HASHES[0]
    for name in ("provider_experiment.py", "provider_experiment_bundle.py", "provider_experiment_runner.py",
                 "provider_experiment_compat.py", "provider_experiment_review.py"):
        identity["experiment_scripts"][name] = "c" * 64
    return identity


def test_exact_current_identity_needs_no_legacy_exception():
    current = current_identity()
    assert compat.runtime_compatibility(current, current)["legacy"] is False


def test_known_historical_release_allows_only_review_upgrade():
    result = compat.runtime_compatibility(historical_identity(), current_identity())
    assert result == {
        "legacy": True, "revision": "dev22-review-only-v1",
        "source_version": "0.1.0.dev22", "current_version": "0.1.0.dev23",
        "baseline_tag": "v0.1.0.dev22", "baseline_commit": "807cc0432bced1d78450efa46dd78358e11494ab",
    }


def test_known_windows_line_endings_are_supported():
    historical, current = historical_identity(), current_identity()
    for name, hashes in compat.DEV22_PACKAGE_HASHES.items():
        historical["package_files"][name] = hashes[-1]
        current["package_files"][name] = hashes[-1]
    current["package_files"]["__init__.py"] = compat.DEV23_INIT_HASHES[-1]
    for name, hashes in compat.DEV22_SCRIPT_HASHES.items():
        historical["experiment_scripts"][name] = hashes[-1]
    assert compat.runtime_compatibility(historical, current)["legacy"] is True


@pytest.mark.parametrize("field", ["python", "system", "openssl", "httpx", "httpcore", "pydantic", "certifi"])
def test_dependency_or_runtime_change_is_not_review_compatibility(field):
    current = current_identity()
    current[field] = "changed"
    with pytest.raises(ConfigurationError, match="outside the supported"):
        compat.runtime_compatibility(historical_identity(), current)


@pytest.mark.parametrize("damage", ["old_version", "new_version", "old_module", "old_script",
                                   "new_module", "new_scoring", "new_init", "extra_module",
                                   "extra_script", "missing_script", "runtime_field"])
def test_unknown_history_or_extraction_changes_cannot_reuse_archive(damage):
    historical, current = historical_identity(), current_identity()
    if damage == "old_version":
        historical["package_version"] = "0.1.0.dev21"
    elif damage == "new_version":
        current["package_version"] = "0.1.0.dev24"
    elif damage == "old_module":
        historical["package_files"]["prompting.py"] = "d" * 64
    elif damage == "old_script":
        historical["experiment_scripts"]["provider_experiment_runner.py"] = "d" * 64
    elif damage == "new_module":
        current["package_files"]["prompting.py"] = "d" * 64
    elif damage == "new_scoring":
        current["experiment_scripts"]["provider_experiment_reporting.py"] = "d" * 64
    elif damage == "new_init":
        current["package_files"]["__init__.py"] = "d" * 64
    elif damage == "extra_module":
        current["package_files"]["unreviewed.py"] = "d" * 64
    elif damage == "extra_script":
        current["experiment_scripts"]["provider_experiment_unreviewed.py"] = "d" * 64
    elif damage == "missing_script":
        del current["experiment_scripts"]["provider_experiment_review.py"]
    else:
        historical["new_runtime_field"] = "ignored?"
    with pytest.raises(ConfigurationError, match="outside the supported"):
        compat.runtime_compatibility(historical, current)


@pytest.fixture
def archived(tmp_path, monkeypatch):
    historical = historical_identity()
    monkeypatch.setattr(bundle, "runtime_identity", lambda: deepcopy(historical))
    monkeypatch.setattr(bundle, "network_identity", lambda: {"network": "frozen-synthetic"})
    monkeypatch.setattr(httpx.Client, "send", lambda *args, **kwargs: pytest.fail("Offline inspection sent HTTP"))
    source = tmp_path / "source.md"
    source.write_text("\n\n".join(f"Item {i:02} reads 11. " + "Synthetic background. " * 170
                                  for i in range(16)), encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"name": "Synthetic paper", "record_types": {
        "reading": {"fields": {"value": {"type": "integer"}}}}}), encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "synthetic-key-one", "VLM_API_KEY": "synthetic-key-two",
                   "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    root = tmp_path / "bundle"
    bundle.prepare_bundle(root, source, task, environment)
    monkeypatch.setattr(bundle, "runtime_identity", current_identity)
    return root, environment


def test_compatible_archive_keeps_manifest_and_frozen_rows_unchanged(archived):
    root, environment = archived
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = bundle.load_bundle(root, environment)
    assert result["runtime_compatibility"]["legacy"] is True
    assert result["network_identity_matches"] is True
    assert result["manifest"]["runtime"]["package_version"] == "0.1.0.dev22"
    assert len(result["rows"]) == 108 and result["manifest"]["max_model_calls"] == 108
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def test_offline_inspection_exposes_network_difference_without_overriding_run(archived, monkeypatch):
    root, environment = archived
    monkeypatch.setattr(bundle, "network_identity", lambda: {"network": "another-process"})
    result = bundle.load_bundle(root, environment, check_network=False)
    assert result["network_identity_matches"] is False
    with pytest.raises(ConfigurationError, match="Network environment changed"):
        bundle.load_bundle(root, environment)
    changed_endpoint = {**environment, "VLM_BASE_URL": "https://different.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    with pytest.raises(ConfigurationError, match="bindings"):
        bundle.load_bundle(root, changed_endpoint, check_network=False)


def test_offline_inspection_does_not_relax_material_validation(archived):
    root, _ = archived
    (root / "requests.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="damaged"):
        bundle.load_bundle(root, check_network=False)


def test_rehashed_request_mutation_still_fails_reconstruction(archived):
    root, _ = archived
    requests = json.loads((root / "requests.json").read_text(encoding="utf-8"))
    first = next(iter(requests.values()))
    first["messages"][-1]["content"] = "Changed synthetic instruction"
    raw = json.dumps(requests).encode()
    (root / "requests.json").write_bytes(raw)
    import hashlib
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["requests.json"] = hashlib.sha256(raw).hexdigest()
    manifest["bundle_id"] = "provider-experiment-" + bundle.digest({k: v for k, v in manifest.items() if k != "bundle_id"})
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="damaged"):
        bundle.load_bundle(root, check_network=False)

"""Offline decision CLI boundaries, without credentials or network calls."""

import importlib.util
import json
from pathlib import Path

import pytest

from attestextract import cli


@pytest.fixture
def prepared(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/decision_bundle_smoke.py"
    specification = importlib.util.spec_from_file_location("decision_acceptance", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    directory = tmp_path / "acceptance"
    summary = module.run_acceptance(directory)
    assert summary["passed"] and summary["real_endpoint_calls"] == 0
    return directory


def test_default_outputs_are_new_siblings_and_need_no_credentials(prepared, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline commands must not load credentials or create a model backend")
    monkeypatch.setattr(cli, "load_env_file", forbidden)
    monkeypatch.setattr(cli, "default_registry", forbidden)
    capsys.readouterr()
    assert cli.main(["prepare-decisions", "--run", str(prepared / "run")]) == 0
    summary = json.loads(capsys.readouterr().out)
    directory = Path(summary["output_directory"])
    assert directory.parent == prepared and directory.name.startswith("run-decisions-")
    assert summary["candidate_count"] == 2 and summary["empty_chunk_count"] == 1
    assert summary["model_calls"] == 0
    report = (directory / "decision-report.md").read_text(encoding="utf-8")
    assert "合法空块" in report and "没有关联不表示没有冲突" in report
    assert "未评估（回传非法）" in (prepared / "invalid/decision-report.md").read_text(encoding="utf-8")
    assert cli.main(["check-decisions", "--bundle", str(directory / "decision-input.json"),
                     "--responses", str(prepared / "responses.json")]) == 2
    checked = Path(json.loads(capsys.readouterr().out)["output_directory"])
    assert checked.parent == prepared and checked != directory


@pytest.mark.parametrize("target", ["run", "run/new-materials", "materials"])
def test_prepare_refuses_input_overlap_or_existing_output(prepared, target):
    before = (prepared / "run/result.json").read_bytes()
    assert cli.main(["prepare-decisions", "--run", str(prepared / "run"),
                     "--output", str(prepared / target)]) == 1
    assert (prepared / "run/result.json").read_bytes() == before


def test_missing_run_does_not_create_output(tmp_path, capsys):
    output = tmp_path / "new-materials"
    assert cli.main(["prepare-decisions", "--run", str(tmp_path / "missing"), "--output", str(output)]) == 1
    assert not output.exists()
    assert "attestextract:" in capsys.readouterr().err


def test_malformed_manifest_fails_clearly_without_creating_output(prepared, capsys):
    manifest = prepared / "run/diagnostics/manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"] = [{}]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    output = prepared / "must-not-exist"
    assert cli.main(["prepare-decisions", "--run", str(prepared / "run"), "--output", str(output)]) == 1
    assert not output.exists()
    assert "attestextract:" in capsys.readouterr().err

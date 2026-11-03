"""Result metadata follows the installed code; old explicit versions survive loading."""

import json

import attestextract
from attestextract import cli
from attestextract.models import ExtractionResult


def test_demo_and_d1_report_current_package_version(tmp_path):
    for command in ("demo", "d1"):
        output = tmp_path / f"{command}.json"
        assert cli.main([command, "--output", str(output)]) == 0
        examples = json.loads(output.read_text(encoding="utf-8"))["examples"]
        for payload in examples.values():
            result = payload if command == "demo" else payload["extraction"]
            assert result["package_version"] == attestextract.__version__


def test_runtime_default_and_explicit_historical_version_are_distinct(tmp_path, monkeypatch):
    output = tmp_path / "demo.json"
    assert cli.main(["demo", "--example", "procurement", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["package_version"] = "0.1.0.dev12"
    historical = ExtractionResult.model_validate(payload)
    assert historical.model_dump()["package_version"] == "0.1.0.dev12"
    payload.pop("package_version")
    monkeypatch.setattr(attestextract, "__version__", "0.1.0.dev999")
    assert ExtractionResult.model_validate(payload).package_version == "0.1.0.dev999"

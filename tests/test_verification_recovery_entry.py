"""Use the distributable synthetic runner through both public entry points."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", ["python", "cli"])
def test_bounded_verification_recovery_public_entry(tmp_path, entry):
    script = Path(__file__).resolve().parents[1] / "scripts/verification_recovery_smoke.py"
    spec = importlib.util.spec_from_file_location("verification_recovery_acceptance", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with runner.offline_only():
        result = runner.accept(tmp_path / entry, entry)
    assert result["passed"] and result["reused_jobs"] == 1
    assert result["recovery_new_calls"] == 2 and result["recovered_analysis_calls"] == 4
    assert result["history_unchanged"] and result["original_values_and_evidence_preserved"]

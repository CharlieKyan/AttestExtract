"""Public entry regressions use the same synthetic runner as wheel acceptance."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", ["cli", "python"])
def test_legacy_feedback_recovery_through_public_entry(tmp_path, entry):
    script = Path(__file__).resolve().parents[1] / "scripts/format_recovery_smoke.py"
    spec = importlib.util.spec_from_file_location("format_recovery_acceptance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module._offline_only():
        result = module._legacy_entry_case(tmp_path, entry)
    assert result["initial_calls"] == 4 and result["resume_new_calls"] == 1
    assert result["reused_chunks"] == 3 and result["completed_chunks"] == 4
    assert result["completed_resume_new_calls"] == result["exhausted_resume_new_calls"] == 0
    assert result["legacy_feedback_removed_from_prompt"] and result["partial_prefix_not_applied"]

"""Public entry coverage for a six-target / twelve-reference task boundary."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from niceextract import ExecutionOptions, extract
from niceextract.backends import OpenAICompatibleBackend


_SPEC = importlib.util.spec_from_file_location(
    "scope_acceptance", Path(__file__).resolve().parents[1] / "scripts" / "analysis_smoke.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
@pytest.mark.parametrize("entry", ["python", "cli"])
def test_public_entry_separates_targets_from_references_and_preserves_unknown(tmp_path, mode, entry):
    # The service's answer groups are frozen independently as 1..6, 7..12,
    # 13..18. It reads IDs only to address those expected scientific records.
    summary = acceptance.accept_scope(tmp_path / "scope", mode, entry)
    assert summary["supported"] == 15 and summary["unknown"] == 3
    assert summary["analysis_calls"] == 5 and summary["resume_new_calls"] == 0


def test_answering_readonly_references_is_diagnosed_without_partial_commit_or_retry(tmp_path):
    service = acceptance.ScopeService(mode="json_object", answer_references=True)
    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-scope-model",
        api_key="synthetic-install-secret", structured_output="json_object",
        max_attempts=1, transport=httpx.MockTransport(service.handle))
    result = extract(acceptance.SCOPE_SOURCE, acceptance.SCOPE_TASK, backend=backend,
        output_dir=tmp_path / "failed", progress="quiet", max_chunk_chars=6000,
        execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1),
        table_subject="one individual specimen", max_analysis_calls=5)
    package = result.analysis
    assert len(result.candidates) == len(package["observations"]) == 18
    assert package["status"] == "failed" and package["budget"]["reserved"] == 3
    assert package["objects"] == [] and package["relations"] == []
    assert [job["status"] for job in package["jobs"]] == ["failed", "pending", "pending"]
    assert service.calls == ["extraction", "search", "read", "decide"]
    step = package["jobs"][0]["steps"][-1]
    assert step["error_code"] == "binding_scope_mismatch"
    summary = step["validation_summary"]
    assert summary["expected_count"] == 6 and summary["returned_count"] == 18
    assert summary["missing_count"] == summary["duplicate_count"] == 0
    assert summary["unexpected_count"] == summary["reference_count"] == 12
    diagnostics = tmp_path / "failed" / "diagnostics"
    details = json.loads((diagnostics / step["validation_details"]).read_text(encoding="utf-8"))
    assert set(details["reference_ids"]) == set(service.reference_ids)
    assert set(details["unexpected_ids"]) == set(service.reference_ids)
    assert details["foreign_ids"] == []
    raw = json.loads((diagnostics / "analysis/responses/verify-0001.step-03.txt").read_text(encoding="utf-8"))
    assert len(raw["bindings"]) == 18 and len(raw["objects"]) == 5
    assert all(item["subject_id"] is None for item in package["observations"])

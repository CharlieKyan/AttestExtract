"""Synthetic public-entry acceptance for review-only partial analysis."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.analysis_export import save_analysis
from attestextract.backends import OpenAICompatibleBackend


_SPEC = importlib.util.spec_from_file_location(
    "ambiguity_acceptance", Path(__file__).resolve().parents[1] / "scripts/analysis_smoke.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
@pytest.mark.parametrize("entry", ["python", "cli"])
def test_group_ambiguity_isolated_other_group_processed_and_resume_offline(tmp_path, mode, entry):
    summary = acceptance.accept_ambiguity(tmp_path / "entry", mode, entry)
    assert summary["review_observations"] == summary["supported_observations"] == 6
    assert summary["analysis_calls"] == 2 and summary["resume_new_calls"] == 0


@pytest.mark.parametrize("case, max_calls, expected_jobs, expected_calls", [
    ("hard", 2, ["failed", "pending"], ["extraction", "decide"]),
    ("pending", 1, ["review_required", "pending"], ["extraction", "decide"]),
    ("limited", 8, ["limited", "limited"], ["extraction"] + ["search"] * 8),
])
def test_cli_review_exit_does_not_hide_hard_pending_or_limited_work(tmp_path, case, max_calls, expected_jobs, expected_calls):
    service = acceptance.AmbiguityService(mode="json_object", invalid_after_ambiguity=case == "hard",
                                        lookup_only=case == "limited")
    _, invoke = acceptance.ambiguity_entry(tmp_path / case, "json_object", "cli",
                                         service=service, max_calls=max_calls)
    output = tmp_path / case / "run"
    assert invoke(output) == 2
    analysis = acceptance.read(output / "analysis/analysis.json")
    assert [job["status"] for job in analysis["jobs"]] == expected_jobs
    assert service.calls == expected_calls
    assert analysis["objects"] == analysis["relations"] == []
    assert all(item["subject_id"] is None for item in analysis["observations"])
    if case == "hard":
        assert analysis["status"] == "failed"
        assert analysis["jobs"][0]["steps"][-1]["error_code"] != "quote_ambiguous"


def test_python_without_output_retains_review_material_for_later_manual_export(tmp_path):
    service = acceptance.AmbiguityService(mode="json_object")
    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-ambiguity-model",
        api_key="synthetic-install-secret", structured_output="json_object",
        max_attempts=1, transport=httpx.MockTransport(service.handle))
    result = extract(acceptance.AMBIGUITY_SOURCE, acceptance.SCOPE_TASK, backend=backend,
        execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1), max_chunk_chars=6000,
        table_subject="one individual specimen", max_analysis_calls=2, progress="quiet")
    analysis = result.analysis
    assert analysis["status"] == "partial" and len(analysis["review_groups"]) == 1
    group = analysis["review_groups"][0]
    assert group["response_details"] is None and group["validation_details"] is None
    response = json.loads(group["response_text"])
    assert len(response["objects"]) == len(response["bindings"]) == 6
    assert response["objects"][0]["mentions"][0] == {
        "passage_id": "passage-000001", "quote": "Specimen A01", "occurrence": None}
    assert group["diagnostic"]["ambiguity_count"] == 1
    assert len(group["diagnostic"]["ambiguities"][0]["matches"]) == 2
    output = tmp_path / "manual-analysis"
    save_analysis(analysis, output)
    saved = acceptance.read(output / "review_groups.json")[0]
    assert saved["response_text"] == group["response_text"] and saved["diagnostic"] == group["diagnostic"]
    assert len(acceptance.read(output / "objects.json")["objects"]) == 6
    assert service.calls == ["extraction", "decide", "decide"]

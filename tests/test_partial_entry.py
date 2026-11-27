"""Public-entry checks for per-proposal review and retained independent bindings."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from niceextract import ExecutionOptions, extract
from niceextract.analysis_export import save_analysis
from niceextract.backends import OpenAICompatibleBackend


_SPEC = importlib.util.spec_from_file_location(
    "partial_acceptance", Path(__file__).resolve().parents[1] / "scripts/analysis_smoke.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
@pytest.mark.parametrize("entry", ["python", "cli"])
def test_three_supported_one_unknown_two_invalid_do_not_discard_independent_answers(tmp_path, mode, entry):
    summary = acceptance.accept_partial(tmp_path / "entry", mode, entry)
    assert summary["first_group_applied"] == 4 and summary["first_group_held"] == 2
    assert summary["supported_observations"] == 9 and summary["unknown_observations"] == 1
    assert summary["resume_new_calls"] == 0 and summary["analysis_calls"] == 2


@pytest.mark.parametrize("entry", ["python", "cli"])
def test_invalid_object_blocks_only_referencing_bindings_and_relation(tmp_path, entry):
    summary = acceptance.accept_partial(tmp_path / "entry", "json_object", entry, kind="object")
    assert summary["review_observations"] == 2 and summary["supported_observations"] == 10
    assert summary["rejected_dependencies_excluded"]


def test_portable_partial_result_keeps_two_original_contradictions(tmp_path):
    service = acceptance.AmbiguityService(mode="json_object", partial_kind="binding")
    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-ambiguity-model",
        api_key="synthetic-install-secret", structured_output="json_object",
        max_attempts=1, transport=httpx.MockTransport(service.handle))
    result = extract(acceptance.AMBIGUITY_SOURCE, acceptance.SCOPE_TASK, backend=backend,
        execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1), max_chunk_chars=6000,
        table_subject="one individual specimen", max_analysis_calls=2, progress="quiet")
    analysis = result.analysis
    group = analysis["review_groups"][0]
    assert group["response_details"] is None and group["validation_details"] is None
    original = json.loads(group["response_text"])
    assert len(original["bindings"]) == 6
    assert all(item["subject_id"] is None and item["status"] == "supported" for item in original["bindings"][4:])
    assert len(group["diagnostic"]["reviews"]) == 2
    assert len(group["applied_observation_ids"]) == 4 and len(group["observation_ids"]) == 2
    save_analysis(analysis, tmp_path / "portable")
    saved = acceptance.read(tmp_path / "portable/review_groups.json")[0]
    assert saved["response_text"] == group["response_text"]
    assert saved["diagnostic"] == group["diagnostic"]


def test_modified_partial_diagnostic_is_rejected_before_any_generation(tmp_path):
    service = acceptance.AmbiguityService(mode="json_object", partial_kind="binding")
    _, invoke = acceptance.ambiguity_entry(tmp_path / "entry", "json_object", "cli", service=service)
    first = tmp_path / "entry/first"
    assert invoke(first) == 3
    diagnostic = first / "diagnostics/analysis/validation/verify-0001.step-01.errors.json"
    data = acceptance.read(diagnostic)
    data["reviews"].pop()
    diagnostic.write_text(json.dumps(data), encoding="utf-8")
    service.forbidden = True
    assert invoke(tmp_path / "entry/replay", first) == 1
    assert service.calls == ["extraction", "decide", "decide"]

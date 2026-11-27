import hashlib
import json

import pytest

from niceextract import ContextOptions, ExecutionOptions, extract
from niceextract.backends import ReplayBackend
from niceextract.errors import ConfigurationError, InvalidResponseError
from niceextract.task_input import compile_task


SOURCE = "Cart M carries canisters A and B. Canister A holds 7 L. Canister B holds 9 L."


def task():
    return compile_task(prompt_description="List volumes by container.",
                        field_list={"volume": "Documented container volume."}).model_copy(update={"row_subject": "Carts"})


def item(label, reading):
    return {"record_type": "volume", "name_handling": "related",
            "values": {"entity": label, "value": reading, "unit": "L"},
            "quotes": {"entity": label, "value": f"{label} holds {reading} L.", "unit": f"{reading} L"},
            "row_relation": {"target": "Cart M", "predicate": "carried in", "field_applicability": "included",
                             "evidence": [{"quote": "Cart M carries canisters A and B.", "ref": None}]}}


def run(directory, answers, *, protocol, **options):
    return extract(SOURCE, task(), observation_protocol=protocol, backend=ReplayBackend(answers),
                   backend_identity="fixed simulated model", output_dir=directory, progress="quiet",
                   execution=ExecutionOptions(max_model_calls=options.pop("budget", 2)),
                   format_retries=1, context=ContextOptions(), table_mode="text", **options)


@pytest.fixture
def previous(tmp_path):
    old = tmp_path / "previous"
    a, b = item("Canister A", "7"), item("Canister B", "9")
    with pytest.raises(InvalidResponseError):
        run(old, [{"records": [a]}, {"records": [a, b]}], protocol="source-v1")
    return old


def fingerprints(directory):
    return {p.relative_to(directory): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def test_both_actual_attempts_revalidated_with_record_origins_and_reserved_budget(previous, tmp_path):
    expected = fingerprints(previous)
    recovered = tmp_path / "recovered"
    result = run(recovered, [], protocol="source-v2", recover_format_from=previous)
    assert {c.fields["value"].value for c in result.candidates} == {"7", "9"}
    assert result.run.response_count == result.run.request_attempts == 2
    ledger = json.loads((recovered / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    assert ledger["calls_used"] == ledger["max_model_calls"] == 2
    key, = ledger["roots"]
    bundle = json.loads((recovered / f"diagnostics/execution/format-recovery/{key}.json").read_bytes())
    assert bundle["record_origins"] == [[{"attempt": 1, "record_index": 0}, {"attempt": 2, "record_index": 0}],
                                        [{"attempt": 2, "record_index": 1}]]
    for attempt in bundle["attempts"]:
        data = (previous / "diagnostics" / attempt["response_file"]).read_bytes()
        assert attempt["response_sha256"] == hashlib.sha256(data).hexdigest()
        assert attempt["response"]["text"] == data.decode()
    assert not (recovered / "diagnostics/requests").exists()
    again = run(tmp_path / "again", [], protocol="source-v2", resume_from=recovered)
    assert again.candidates == result.candidates
    assert fingerprints(previous) == expected
    cache = recovered / f"diagnostics/execution/format-recovery/{key}.json"
    bundle["attempts"].pop()
    cache.write_text(json.dumps(bundle), encoding="utf8")
    with pytest.raises(ConfigurationError, match="damaged"):
        run(tmp_path / "badresume", [], protocol="source-v2", resume_from=recovered)


@pytest.mark.parametrize("bad", ["missing_retry", "changed_request", "low_budget", "wrong_diagnostic"])
def test_inconsistent_original_rejected_before_generation(previous, tmp_path, bad):
    files = previous / "diagnostics"
    if bad == "missing_retry":
        (files / "responses/chunk-0001.format-2.txt").unlink()
    if bad == "changed_request":
        (files / "requests/chunk-0001.request.json").write_text("{}", encoding="utf8")
    if bad == "wrong_diagnostic":
        (files / "validation/chunk-0001.errors.json").write_text("{}", encoding="utf8")
    with pytest.raises(ConfigurationError, match="could not verify"):
        run(tmp_path / "rejected", [], protocol="source-v2", recover_format_from=previous,
            budget=1 if bad == "low_budget" else 2)
    assert not (tmp_path / "rejected/diagnostics/execution/checkpoint.json").exists()

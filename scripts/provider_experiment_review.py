"""Validate source-bound, offline semantic reviews without rewriting strict scores.

An attached review is an accountable judgment, not an expert label or a global
name normalizer. Checksums bind it to artifacts; they do not authenticate a
reviewer's identity. Deterministic response/evidence checks remain mandatory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from attestextract.errors import ConfigurationError
from attestextract.evidence import ground_quote
from attestextract.models import BackendResponse, DocumentChunk, ExtractionRequest, TaskSpec
from attestextract.pipeline import _parse_response

from provider_experiment_bundle import _read
from provider_experiment_reporting import PATHS, _contains_value, score_response


REFERENCES = ("sources.json", "tasks.json", "gold.json", "rows.json", "requests.json")
PASS = "pass_for_this_synthetic_case"
VERDICTS = {PASS, "reject", "review"}


def _require(condition, message):
    if not condition:
        raise ConfigurationError("Semantic review: " + message)


def _reason(value):
    return isinstance(value, str) and bool(value.strip())


def _artifact(root: Path, relative: str) -> Path:
    path = root / relative
    _require(path.resolve().is_relative_to(root.resolve()), "artifact is outside the bundle.")
    for item in (path, *path.parents):
        _require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(),
                 "linked artifact paths are not supported.")
        if item == root:
            break
    _require(path.is_file(), "required response or result artifact is missing.")
    return path


def _same_value(actual, expected, field):
    if field.type == "number" and type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def _name_judgments(finding, records, task):
    judgments = {}
    for item in finding.get("name_boundary_findings", []):
        index, name = item["record_index"], item["field"]
        _require(type(index) is int and 0 <= index < len(records), "name judgment has an invalid record.")
        record = records[index]
        spec = task.record_types[record.record_type]
        _require(isinstance(name, str) and name in spec.identity_fields and name in record.fields,
                 "name judgment must refer to an identity field.")
        _require((index, name) not in judgments, "duplicate name judgment.")
        _require(spec.fields[name].type == "string" and isinstance(item["gold_value"], str)
                 and item["model_value"] == record.fields[name].value
                 and item["model_value"] != item["gold_value"]
                 and item.get("judgment") == "equivalent_in_this_source" and _reason(item.get("reason")),
                 "name judgment is missing its exact values or explanation.")
        judgments[index, name] = item
    return judgments


def _record_supported(index, record, expected, judgments, task, source, chunk):
    if record.record_type != expected["record_type"] or set(record.fields) != set(expected["fields"]):
        return False
    spec = task.record_types[record.record_type]
    for name, field in record.fields.items():
        anchor = expected["fields"][name]
        same = _same_value(field.value, anchor["value"], spec.fields[name])
        judgment = judgments.get((index, name))
        if judgment is not None:
            if same or judgment["gold_value"] != anchor["value"]:
                return False
        elif not same:
            return False
        if spec.fields[name].required and field.value is None:
            return False
        if field.value is None:
            continue
        located = ground_quote(source, chunk, field.quote)
        start, end = anchor.get("start"), anchor.get("end")
        if (located.status != "matched" or located.start is None or located.end is None
                or source[located.start:located.end] != field.quote
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(source) or source[start:end] != anchor["quote"]):
            return False
        # Keep the independent annotated condition passage, even when a name
        # differs. This rejects a valid quote taken from the other observation.
        related = (start <= located.start < located.end <= end
                   or located.start <= start < end <= located.end)
        if not related or not _contains_value(field.quote, field.value):
            return False
    return True


def _pass_guards(finding, text, request, case, scores):
    _require(scores["exact_source_spans"] == scores["evidence_fields"],
             "a pass cannot override ambiguous or missing source evidence.")
    raw = _parse_response(text, output_protocol=request.output_protocol)
    task, chunk = TaskSpec.model_validate(case["task"]), DocumentChunk.model_validate(case["chunk"])
    expected = case.get("gold")
    _require(isinstance(expected, list) and len(raw.records) == len(expected),
             "a pass cannot override missing or extra records.")
    judgments = _name_judgments(finding, raw.records, task)
    used = set()
    for index, record in enumerate(raw.records):
        matches = [i for i, gold in enumerate(expected) if i not in used and
                   _record_supported(index, record, gold, judgments, task, case["source"], chunk)]
        _require(len(matches) == 1, "a pass lacks a unique value-and-condition-aligned evidence record.")
        used.add(matches[0])


def _validate_finding(finding, row, bundle, root, results):
    from provider_experiment_runner import read_record

    identifier = row["id"]
    _require(type(finding.get("round")) is int and all(finding.get(key) == row[source] for key, source in
                 (("path_id", "path_id"), ("case_id", "case_id"), ("round", "round"))),
             "finding refers to a different task, path or round.")
    verdict = finding.get("assistant_semantic_review")
    _require(verdict in VERDICTS and _reason(finding.get("reason")), "finding has no valid verdict or reason.")
    result_name = f"results/trials/{identifier}/result.json"
    response_name = f"results/trials/{identifier}/diagnostics/unified-response.json"
    _require(finding.get("original_result_file") == result_name and finding.get("response_file") == response_name,
             "finding contains an unexpected artifact path.")
    result_path, response_path = _artifact(root, result_name), _artifact(root, response_name)
    _require(hashlib.sha256(result_path.read_bytes()).hexdigest() == finding.get("original_result_sha256")
             and hashlib.sha256(response_path.read_bytes()).hexdigest() == finding.get("response_sha256"),
             "reviewed artifact checksum changed.")
    result = read_record(result_path)
    _require(result == results.get(identifier), "result differs from the validated call ledger.")
    _require(result.get("status") == "completed" and result.get("sent") is True
             and result.get("http_complete") is True and result.get("status_code") == 200
             and result.get("transport_unknown") is False
             and result.get("request_outcome") == "response_received",
             "review requires a complete successful response.")
    response = BackendResponse.model_validate(_read(response_path))
    _require(response.finish_reason in (None, "stop"), "review cannot override incomplete generation.")
    request = ExtractionRequest.model_validate(row["request"])
    raw_path = _artifact(root, f"results/trials/{identifier}/diagnostics/responses/{request.artifact_stem}.txt")
    _require(raw_path.read_bytes().decode("utf-8") == response.text, "unified response differs from saved model text.")
    scores = score_response(response.text, request, bundle["cases"][row["case_id"]])
    _require(scores == result.get("scores"), "saved strict scores do not match the saved response.")
    _require(scores.get("format_passed") is True, "review cannot override format or task-schema failures.")
    _require(finding.get("original_strict_content_passed") is scores["content_passed"]
             and finding.get("original_strict_evidence_passed") is scores["evidence_passed"],
             "review misstates the original strict scores.")
    _require(type(finding.get("candidate_count")) is int and finding["candidate_count"] == scores["candidate_count"]
             and type(finding.get("quote_fields_exact_and_condition_aligned")) is int
             and finding["quote_fields_exact_and_condition_aligned"] == scores["exact_source_spans"],
             "review counts do not match the saved response.")
    if verdict == PASS:
        _pass_guards(finding, response.text, request, bundle["cases"][row["case_id"]], scores)
    return verdict


def validate_review(review: dict, bundle: dict, results_root: Path, results: dict) -> dict:
    """Validate every C finding and derive D eligibility; never mutate inputs."""
    try:
        _require(review.get("review_version") == "0.1" and review.get("origin") == "assistant_semantic_review"
                 and review.get("expert_gold") is False and _reason(review.get("rubric_basis")),
                 "unsupported review format or missing AI-review provenance.")
        _require(review.get("bundle_id") == bundle["manifest"]["bundle_id"], "review belongs to another frozen bundle.")
        references = {name: bundle["manifest"]["files"][name] for name in REFERENCES}
        _require(review.get("reference_checksums") == references, "review references do not match frozen materials.")
        rows = [row for row in bundle["rows"] if row["stage"] == "C"]
        findings = review["findings"]
        _require(isinstance(findings, list) and len(findings) == len(rows) == 24,
                 "review must cover all 24 frozen C trials.")
        indexed = {item["trial_id"]: item for item in findings}
        _require(len(indexed) == len(findings) and set(indexed) == {row["id"] for row in rows},
                 "review contains missing, duplicate or foreign trials.")
        root = Path(results_root).absolute().parent
        _require(Path(results_root).name == "results", "invalid result directory.")
        verdicts = {row["id"]: _validate_finding(indexed[row["id"]], row, bundle, root, results) for row in rows}
        selection = {}
        for provider, paths in PATHS.items():
            eligible = []
            for path in paths:
                group = [row for row in rows if row["path_id"] == path]
                if (len(group) == 6 and all(verdicts[row["id"]] == PASS for row in group)
                        and sum(indexed[row["id"]]["candidate_count"] for row in group) == 8):
                    eligible.append(path)
            selection[provider] = {"selected": next(iter(eligible), None), "eligible": eligible,
                                   "reason": "reviewed_predeclared_priority" if eligible else "no_six_of_six_reviewed_path"}
        return {"protocol_selection": selection, "review_origin": review["origin"],
                "review_version": review["review_version"], "reviewed_trials": len(rows),
                "expert_gold": False, "strict_scores_preserved": True}
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ConfigurationError("Semantic review is missing, damaged or inconsistent; no request was sent.") from None

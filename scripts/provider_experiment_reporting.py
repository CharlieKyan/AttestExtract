"""Offline, descriptive scores for the frozen provider experiment.

Gold stays local. These scores are fixture checks, never general semantic
verification or permission to accept a candidate or make another model call.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from statistics import median

from attestextract.errors import InvalidResponseError
from attestextract.evidence import ground_quote
from attestextract.models import DocumentChunk, ExtractionRequest, TaskSpec
from attestextract.pipeline import _check_task_records, _parse_response


PROVIDERS = ("qwen", "deepseek")
TLS_MODES = ("auto", "1.2", "auto_fallback")
DEFAULT_TLS = {"qwen": "auto_fallback", "deepseek": "1.2"}
PATHS = {
    "qwen": ("qwen-chat-object",),
    "deepseek": ("deepseek-chat-object", "deepseek-responses-object", "deepseek-responses-schema"),
}


def _record_key(record_type, values, task):
    # Number fields accept 7 and 7.0; boolean/string coercion is never allowed.
    spec = task.record_types.get(record_type)
    typed = []
    for name, value in sorted(values.items()):
        kind = type(value).__name__
        if spec is not None and name in spec.fields and spec.fields[name].type == "number" and type(value) in (int, float):
            kind = "number"
        typed.append((name, kind, value))
    return record_type, tuple(typed)


def _contains_value(quote, value):
    """Literal support for the small frozen fixtures, not arbitrary semantics."""
    if value is None:
        return True
    if isinstance(value, str):
        return bool(value) and value in quote
    if type(value) in (int, float):
        tokens = re.findall(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])", quote, flags=re.ASCII)
        return any(float(token) == value for token in tokens)
    return False  # Boolean verbalizations need a separately annotated fixture.


def _evidence_scores(raw, task, source, chunk, gold):
    expected = defaultdict(list)
    if gold is not None:
        for record in gold:
            values = {name: field["value"] for name, field in record["fields"].items()}
            expected[_record_key(record["record_type"], values, task)].append(record)
    checks = []
    for index, record in enumerate(raw.records):
        values = {name: field.value for name, field in record.fields.items()}
        key = _record_key(record.record_type, values, task)
        annotated = expected[key].pop(0) if expected[key] else None
        for name, field in record.fields.items():
            if field.value is None:
                continue
            located = ground_quote(source, chunk, field.quote)
            exact = (located.status == "matched" and located.start is not None
                     and located.end is not None and source[located.start:located.end] == field.quote)
            supported = None
            if gold is not None:
                anchor = annotated["fields"].get(name) if annotated else None
                supported = False
                if exact and anchor is not None:
                    start, end = anchor.get("start"), anchor.get("end")
                    annotated_span = (type(start) is int and type(end) is int
                                      and 0 <= start < end <= len(source)
                                      and source[start:end] == anchor.get("quote"))
                    # Accept shorter field quotes in their hand-annotated passage,
                    # or a longer quote containing that full passage. A quote from
                    # the other temperature's passage is not supporting evidence.
                    related = annotated_span and (
                        start <= located.start < located.end <= end
                        or located.start <= start < end <= located.end)
                    supported = bool(related and _contains_value(field.quote, field.value))
            checks.append({"record_index": index, "field": name, "location_status": located.status,
                           "exact_span": exact, "fixture_support": supported})
    exact_count = sum(row["exact_span"] for row in checks)
    support_count = sum(row["fixture_support"] is True for row in checks) if gold is not None else None
    passed = bool(checks) and exact_count == len(checks)
    if gold is not None:
        passed = passed and support_count == len(checks)
        if not gold and not raw.records:
            passed = True
    elif not checks:
        passed = None
    return {"evidence_fields": len(checks), "exact_source_spans": exact_count,
            "fixture_supported_fields": support_count, "evidence_passed": passed,
            "evidence_checks": checks,
            "evidence_scope": "hand_annotated_fixture_passages" if gold is not None else "location_only"}


def score_response(response_text: str, request: ExtractionRequest, case: dict) -> dict:
    """Score available model text; do not call this for absent/truncated text."""
    scores = dict.fromkeys(("json_passed", "contract_passed", "task_passed", "format_passed",
                           "content_passed", "evidence_passed"))
    task = TaskSpec.model_validate(case["task"])
    chunk = DocumentChunk.model_validate(case["chunk"])
    try:
        raw = _parse_response(response_text, output_protocol=request.output_protocol)
    except InvalidResponseError as error:
        phase = (error.validation_details or {}).get("phase", "unknown")
        scores.update(json_passed=phase != "json_syntax", contract_passed=False if phase != "json_syntax" else None,
                      format_passed=False, validation_phase=phase)
        return scores
    scores.update(json_passed=True, contract_passed=True)
    try:
        _check_task_records(raw, task, trace=None, chunk_id=request.chunk_id,
                            output_protocol=request.output_protocol)
    except InvalidResponseError:
        scores.update(task_passed=False, format_passed=False, validation_phase="task_schema")
        return scores
    scores.update(task_passed=True, format_passed=True, candidate_count=len(raw.records))
    gold = case.get("gold")
    if gold is not None:
        expected = Counter(_record_key(record["record_type"],
                           {name: field["value"] for name, field in record["fields"].items()}, task)
                           for record in gold)
        actual = Counter(_record_key(record.record_type,
                         {name: field.value for name, field in record.fields.items()}, task)
                         for record in raw.records)
        matched = sum((actual & expected).values())
        missing, extra = sum((expected - actual).values()), sum((actual - expected).values())
        scores.update(expected_records=sum(expected.values()), matching_records=matched,
                      missing_records=missing, unexpected_records=extra,
                      duplicate_records=sum(max(0, count - 1) for count in actual.values()),
                      content_passed=missing == 0 and extra == 0)
    scores.update(_evidence_scores(raw, task, case["source"], chunk, gold))
    scores["manual_semantic_review"] = "not_performed"
    return scores


def _indexed(rows, results):
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate planned trial IDs")
    if isinstance(results, list):
        if len({item["id"] for item in results}) != len(results):
            raise ValueError("Duplicate result trial IDs")
        results = {item["id"]: item for item in results}
    if set(results) - set(ids) or any(value.get("id", key) != key for key, value in results.items()):
        raise ValueError("Result refers to an unknown or inconsistent trial ID")
    return results


def _http_passed(result):
    status = result.get("status_code")
    return result.get("sent") is True and result.get("http_complete") is True and type(status) is int and 200 <= status < 300


def _mode(row):
    return row.get("tls_mode") or row.get("config_key", "").partition("--tls-")[2]


def select_connections(rows, results):
    results = _indexed(rows, results)
    selected = {}
    for provider in PROVIDERS:
        groups = {mode: [row for row in rows if row["stage"] == "B" and row["provider"] == provider
                         and _mode(row) == mode] for mode in TLS_MODES}
        eligible = [mode for mode, group in groups.items()
                    if len(group) == 6 and all(_http_passed(results.get(row["id"], {})) for row in group)]
        order = list(dict.fromkeys((DEFAULT_TLS[provider], *TLS_MODES)))
        winner = next((mode for mode in order if mode in eligible), None)
        selected[provider] = {"selected": winner, "eligible": eligible,
                              "reason": "retained_preset" if winner == DEFAULT_TLS[provider] else
                                        "fixed_predeclared_order" if winner else "no_six_of_six_complete_policy"}
    return selected


def select_protocols(rows, results):
    results = _indexed(rows, results)
    selected = {}
    for provider, paths in PATHS.items():
        eligible = []
        for path in paths:
            group = [row for row in rows if row["stage"] == "C" and row["provider"] == provider and row["path_id"] == path]
            entries = [results.get(row["id"], {}) for row in group]
            if (len(group) == 6 and all(entry.get("status") == "completed" and _http_passed(entry) and all((entry.get("scores") or {}).get(key) is True
                    for key in ("format_passed", "content_passed", "evidence_passed")) for entry in entries)
                    and sum(entry["scores"].get("matching_records", 0) for entry in entries) == 8
                    and sum(entry["scores"].get("unexpected_records", 0) for entry in entries) == 0):
                eligible.append(path)
        selected[provider] = {"selected": next((path for path in paths if path in eligible), None),
                              "eligible": eligible,
                              "reason": "fixed_predeclared_order" if eligible else "no_six_of_six_qualified_path"}
    return selected


def _counts(rows, results):
    entries = [results.get(row["id"], {}) for row in rows]
    attempted = [entry for entry in entries if entry.get("model_post_attempts", 0) > 0 or entry.get("sent") is True]
    known_sent = [entry for entry in entries if entry.get("sent") is True]
    durations = [entry["elapsed_seconds"] for entry in attempted
                 if type(entry.get("elapsed_seconds")) in (int, float) and entry["elapsed_seconds"] >= 0]
    reported_usage = [entry["usage"] for entry in attempted if isinstance(entry.get("usage"), dict) and entry["usage"]]
    tokens = defaultdict(int)
    token_counts = Counter()
    for usage in reported_usage:
        for key, value in usage.items():
            if type(value) is int and value >= 0:
                tokens[key] += value
                token_counts[key] += 1
    evaluation = {}
    for key in ("json_passed", "contract_passed", "task_passed", "format_passed", "content_passed", "evidence_passed"):
        evaluation[key] = {"passed": sum((entry.get("scores") or {}).get(key) is True for entry in entries),
                           "failed": sum((entry.get("scores") or {}).get(key) is False for entry in entries),
                           "not_evaluated": sum((entry.get("scores") or {}).get(key) is None for entry in entries)}
    # Observations refer to the formal POST, never its anonymous GET probe.
    observations = [(entry.get("connection_observation") or {}) for entry in attempted]
    observations = [item if item.get("request_kind") == "model_post" else {} for item in observations]
    tls_counts = Counter(item.get("actual_tls_version", "unknown") for item in observations)
    http_counts = Counter(item.get("actual_http_version", "unknown") for item in observations)
    return {"planned": len(rows), "reserved": sum(entry.get("status") not in (None, "not_run") for entry in entries),
            "sent": len(known_sent),
            "model_post_attempts": sum(entry.get("model_post_attempts", int(entry.get("sent") is True)) for entry in entries),
            "possibly_sent": sum(entry.get("request_outcome") == "unknown" or entry.get("status") == "reserved_unknown" for entry in entries),
            "known_not_sent": sum(entry.get("request_outcome") == "not_sent" for entry in entries),
            "http_complete": sum(entry.get("http_complete") is True for entry in entries),
            "http_success": sum(_http_passed(entry) for entry in entries),
            "completed": sum(entry.get("status") == "completed" for entry in entries),
            "failed": sum(entry.get("status") == "failed" for entry in entries),
            "reserved_unknown": sum(entry.get("status") == "reserved_unknown" for entry in entries),
            "not_run": sum(entry.get("status") in (None, "not_run") for entry in entries),
            "transport_unknown": sum(entry.get("transport_unknown") is True for entry in entries),
            "not_run_reasons": dict(Counter(entry.get("reason", "pending") for entry in entries
                                             if entry.get("status") in (None, "not_run"))),
            "evaluation": evaluation, "known_usage": dict(tokens) or None,
            "usage_reporting_counts": dict(token_counts), "usage_unknown_attempts": len(attempted) - len(reported_usage),
            "usage_unknown_sent": sum(not entry.get("usage") for entry in known_sent),
            "billing_unknown": sum(entry.get("billing_unknown") is True for entry in entries),
            "probe_requests": sum(entry.get("probe_requests", 0) for entry in entries),
            "actual_tls_versions": dict(tls_counts), "actual_http_versions": dict(http_counts),
            "elapsed_seconds": {"count": len(durations), "median": median(durations) if durations else None,
                                "min": min(durations) if durations else None, "max": max(durations) if durations else None}}


def summarize(rows, results):
    results = _indexed(rows, results)
    grouped = defaultdict(list)
    for row in rows:
        case = "long" if row["case_id"].startswith("paper-") else "short"
        key = (row["stage"], row["provider"], row["path_id"], _mode(row) if row["stage"] == "B" else "selected", case,
               row["case_id"] if row["stage"] == "C" else None)
        grouped[key].append(row)
    groups = [{"stage": key[0], "provider": key[1], "path_id": key[2], "tls_mode": key[3],
               "load": key[4], "case_id": key[5], **_counts(group, results)} for key, group in grouped.items()]
    full_runs = []
    for stage in ("A", "D"):
        for provider in PROVIDERS:
            group = [row for row in rows if row["stage"] == stage and row["provider"] == provider]
            if not group:
                continue
            complete = sum(results.get(row["id"], {}).get("status") == "completed"
                           and (results.get(row["id"], {}).get("scores") or {}).get("format_passed") is True for row in group)
            full_runs.append({"stage": stage, "provider": provider, "planned_chunks": len(group),
                              "completed_chunks": complete, "trials_passed": len(group) == 16 and complete == 16,
                              "passed": None, "claim": "Pipeline outcome required; not semantic or expert acceptance"})
    return {"summary_version": "0.1", "counts": _counts(rows, results), "groups": groups,
            "connection_selection": select_connections(rows, results),
            "protocol_selection": select_protocols(rows, results), "full_runs": full_runs,
            "scope": "Descriptive pilot on the frozen models, endpoints, network and cases; not a universal ranking",
            "currency_cost": "Use supplier billing; unknown requests are not zero cost",
            "manual_semantic_review": "pending"}


def render_report(summary):
    counts = summary["counts"]
    lines = ["# Provider experiment report", "", summary["scope"], "",
             f"Planned: {counts['planned']}; sent: {counts['sent']} (confirmed); HTTP success: {counts['http_success']}; "
             f"completed: {counts['completed']}; not run: {counts['not_run']}; reserved unknown: {counts['reserved_unknown']}.", "",
             f"POST attempts: {counts['model_post_attempts']}; possibly sent: {counts['possibly_sent']}; "
             f"known not sent: {counts['known_not_sent']}. An attempt is not proof of server processing.", "",
             "Missing responses have no JSON/content score. Review counts are not error rates. "
             "A passing source-span check does not establish semantic correctness.", "",
             "| Stage | Provider | Path | TLS policy | Case/load | Planned | Sent | HTTP success | Format passed | Content passed |",
             "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for group in summary["groups"]:
        lines.append(f"| {group['stage']} | {group['provider']} | {group['path_id']} | {group['tls_mode']} | {group['case_id'] or group['load']} | "
                     f"{group['planned']} | {group['sent']} | {group['http_success']} | "
                     f"{group['evaluation']['format_passed']['passed']} | {group['evaluation']['content_passed']['passed']} |")
    lines += ["", "## Candidate settings", "", "Selection follows the predeclared gate and order, not small latency differences.", ""]
    for provider in PROVIDERS:
        connection = summary["connection_selection"][provider]
        protocol = summary["protocol_selection"][provider]
        lines.append(f"- {provider}: TLS `{connection['selected']}` ({connection['reason']}); "
                     f"path `{protocol['selected']}` ({protocol['reason']}).")
    lines += ["", "## Observed formal-request connections", "",
              "Configured auto is not proof of TLS 1.3. Probe handshakes are excluded from these counts.", "",
              "| Stage | Provider | Path | Policy | Case/load | Actual TLS versions | Probe requests |",
              "|---|---|---|---|---|---|---:|"]
    for group in summary["groups"]:
        observed = ", ".join(f"{mode}: {count}" for mode, count in sorted(group["actual_tls_versions"].items())) or "no POST observed"
        lines.append(f"| {group['stage']} | {group['provider']} | {group['path_id']} | {group['tls_mode']} | "
                     f"{group['case_id'] or group['load']} | {observed} | {group['probe_requests']} |")
    lines += ["", "## Fresh full-paper runs", ""]
    for run in summary["full_runs"]:
        lines.append(f"- {run['stage']} / {run['provider']}: {run['completed_chunks']}/{run['planned_chunks']} chunks; passed={run['passed']}.")
    lines += ["", "## Cost and review limits", "", summary["currency_cost"], "",
              "Known usage is a partial sum; see summary.json for per-metric reporting counts, unknown usage, "
              "failed trials, timing ranges and unexecuted reasons. Full-paper content has no exhaustive expert gold. "
              "Inspect every empty chunk and unflagged candidate as well as the frozen source checklist.", ""]
    return "\n".join(lines)

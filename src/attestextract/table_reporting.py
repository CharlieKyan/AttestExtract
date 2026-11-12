"""Source-linked table reports and shared check labels.

Model text is always escaped. The table shown here is reconstructed from indexed
physical cells; untrusted source HTML is never executed in the report.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
from urllib.parse import quote


_STEPS = (
    ("request.json", "1. 实际模型输入"),
    ("response.json", "2. 模型原始输出"),
    ("parsed.json", "3. 解析后的结构"),
    ("parse-rejections.json", "3a. 被隔离记录与格式错误"),
    ("evaluation.json", "4. 字段、来源与范围检查"),
    ("results.json", "5. 抽取结果（附检查状态）"),
    ("reconciliation.json", "6. 历史关联与冲突记录"),
    ("error.json", "错误位置与原因"),
    ("reservation.json", "调用预留记录"),
    ("outcome.json", "阶段执行状态"),
)

_CHECK_CATEGORIES = {
    "mechanical_errors": "格式与原文字面检查",
    "relationship_warnings": "关联未自动确认",
    "risk_diagnostics": "需要优先检查的具体风险",
}


def table_check_label(status, *, check: str | None = None) -> str:
    """Plain-text labels shared by table reports and the document entry point.

    Callers must escape the returned text for their output format. In particular,
    local position checks do not establish scientific truth or member evidence.
    """
    if check == "member_evidence_check":
        if status == "not_run":
            return "未逐项核验成员及其出处"
        if status == "not_applicable":
            return "按模型范围声明不适用（未验证该声明）"
    labels = {"located": "已定位", "missing": "缺失或无法定位", "ambiguous": "位置不唯一",
              "not_applicable": "不适用", "passed": "字面检查通过", "failed": "字面检查失败",
              "locally_supported": "局部位置规则符合（非科学核验）", "not_confirmed": "未自动确认",
              "not_run": "未进行独立核验", "individual": "单一对象（模型声明）",
              "group": "群体或累计（模型声明）", "each": "各成员分别适用（模型声明）"}
    return labels.get(status, str(status)) if status is not None else "未评估"


def _read(path: Path, default=None, *, root: Path):
    path.resolve().relative_to(root)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _anchor(case_id: str, reference: str) -> str:
    identity = json.dumps([case_id, reference], ensure_ascii=False).encode("utf-8")
    return "source-" + hashlib.sha256(identity).hexdigest()[:20]


def _link(root: Path, path: Path, label: str) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return f'<a href="{quote(relative, safe="/")}">{escape(label)}</a>'


def _references(value) -> set[str]:
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get("ref"), str):
            found.add(value["ref"])
        for child in value.values():
            found.update(_references(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_references(child))
    return found


def _source_links(value, case_id: str, known: set[str]) -> str:
    links = []
    for reference in sorted(_references(value)):
        if reference in known:
            links.append(f'<a href="#{_anchor(case_id, reference)}">{escape(reference)}</a>')
        else:
            links.append(f'<span class="bad">未知来源：{escape(reference)}</span>')
    return '<p class="source-links">定位原文：' + " · ".join(links) + "</p>" if links else ""


def _entries(table_input: dict) -> list[dict]:
    """The input contract contains one entry per original physical source."""
    entries = table_input.get("entries", table_input.get("cells", []))
    if isinstance(entries, dict):
        entries = list(entries.values())
    return [item for item in entries if isinstance(item, dict)]


def _source_table(case_id: str, table_input: dict) -> tuple[str, set[str]]:
    entries = _entries(table_input)
    known = {str(item["ref"]) for item in entries if "ref" in item}
    cells = [item for item in entries if type(item.get("row")) is int
             and type(item.get("column")) is int]
    output = ['<div class="table-scroll"><table class="source-table"><tbody>']
    for row in sorted({cell["row"] for cell in cells}):
        output.append("<tr>")
        for cell in sorted((item for item in cells if item["row"] == row), key=lambda item: item["column"]):
            reference = str(cell.get("ref", ""))
            # Source inputs come from the validated index. Never interpolate raw attributes.
            rowspan = cell.get("rowspan", 1)
            colspan = cell.get("colspan", 1)
            rowspan = rowspan if type(rowspan) is int and 1 <= rowspan <= 10000 else 1
            colspan = colspan if type(colspan) is int and 1 <= colspan <= 1000 else 1
            output.append(f'<td id="{_anchor(case_id, reference)}" rowspan="{rowspan}" colspan="{colspan}">'
                          f'<div>{escape(str(cell.get("quote", cell.get("text", ""))))}</div>'
                          f'<small>{escape(reference)} · 行{row} 列{cell["column"]} · '
                          f'原文[{escape(str(cell.get("start", "?")))}, '
                          f'{escape(str(cell.get("end", "?")))}）</small></td>')
        output.append("</tr>")
    output.append("</tbody></table></div>")
    for entry in entries:
        if entry in cells:
            continue
        reference = str(entry.get("ref", ""))
        output.append(f'<blockquote id="{_anchor(case_id, reference)}"><small>'
                      f'{escape(reference)} · {escape(str(entry.get("kind", "context")))}</small><br>'
                      f'{escape(str(entry.get("quote", entry.get("text", ""))))}</blockquote>')
    if not cells:
        output.append('<p class="bad">没有可显示的有效单元格索引；不能据此宣称已定位证据。</p>')
    return "".join(output), known


def _record_values(record: dict) -> dict:
    if isinstance(record.get("values"), dict):
        return record["values"]
    fields = record.get("fields", {})
    if not isinstance(fields, dict):
        return {}
    return {name: field.get("value") if isinstance(field, dict) else field
            for name, field in fields.items()}


def _format_literal(value):
    """Comparison only: exterior whitespace and one complete single-dollar pair."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if (len(text) > 2 and text.startswith("$") and text.endswith("$")
            and text.count("$") == 2 and text[-2] != "\\"):
        text = text[1:-1].strip()
    return text


def _format_fields(task: dict | None) -> dict[str, set[str]]:
    result = {}
    for name, spec in (task or {}).get("record_types", {}).items():
        fields = set()
        for group in spec.get("measurement_groups", {}).values():
            fields.update((group["value_field"], group["unit_field"]))
        result[name] = fields
    return result


def _matches(actual: dict, expected: dict, *, format_fields: set[str] | None = None) -> bool:
    if actual.get("record_type") != expected.get("record_type"):
        return False
    values, target = _record_values(actual), _record_values(expected)
    aliases = expected.get("aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
    if any(value is not None for key, value in values.items() if key not in target):
        return False
    for key, value in target.items():
        allowed = [value, *aliases.get(key, [])]
        normalize = _format_literal if key in (format_fields or ()) else lambda item: item
        if key not in values or not any(type(values[key]) is type(option)
                                        and normalize(values[key]) == normalize(option) for option in allowed):
            return False
    return True


def _selection_matches(actual: dict, expected: dict) -> bool:
    quotes = [expected.get("quote"), *expected.get("quote_aliases", [])]
    return (actual.get("ref") == expected.get("ref") and isinstance(actual.get("ref"), str)
            and actual.get("offset") == expected.get("offset")
            and actual.get("quote") in quotes)


def _sources_match(actual, expected: list[dict]) -> bool:
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    if any(not isinstance(item, dict) for item in [*actual, *expected]):
        return False
    # A reference may occur more than once. Match occurrences one-to-one, and
    # allow only aliases frozen on that particular expected reference.
    owners = {}
    def assign(index: int, visited: set[int]) -> bool:
        for candidate, source in enumerate(actual):
            if candidate in visited or not _selection_matches(source, expected[index]):
                continue
            visited.add(candidate)
            if candidate not in owners or assign(owners[candidate], visited):
                owners[candidate] = index
                return True
        return False
    return all(assign(index, set()) for index in range(len(expected)))


def _source_differences(record: dict, expected: dict) -> list[dict]:
    fields = record.get("fields", {})
    differences = []
    for name, selections in expected["expected_sources"].items():
        binding = fields.get(name) if isinstance(fields, dict) else None
        actual = binding.get("sources") if isinstance(binding, dict) else None
        if not _sources_match(actual, selections):
            differences.append({"field": name, "expected_sources": selections,
                                "actual_sources": actual, "code": "reference_sources_differ_or_missing"})
    return differences


def _source_score(records: list[dict] | None, gold: list[dict], check_sources: bool) -> dict:
    annotated = [item for item in gold if isinstance(item.get("expected_sources"), dict)
                 and item["expected_sources"]]
    checked = records is not None and bool(annotated) and check_sources
    score = {"source_checked": checked, "matched_sources": None, "expected_source_records": len(annotated),
             "unmatched_sources": None, "unannotated_source_records": len(gold) - len(annotated),
             "source_rows": [], "source_score_reason": None}
    if not checked:
        score["source_score_reason"] = ("no_output" if records is None else
                                         "source_check_disabled" if not check_sources else "missing_gold_sources")
        return score
    # Source accuracy is independent of value accuracy: 999 with an original
    # reference to 100 can have correct lineage and an incorrect value.
    remaining = list(enumerate(records))
    matched = 0
    missing = []
    for expected in annotated:
        match = next(((index, record) for index, record in remaining
                      if record.get("record_type") == expected.get("record_type")
                      and not _source_differences(record, expected)), None)
        if match is None:
            missing.append(expected)
        else:
            remaining.remove(match)
            matched += 1
            score["source_rows"].append({"status": "source_matched", "expected": expected,
                                         "actual": match[1], "differences": []})
    for expected in missing:
        # When values match but provenance does not, show that exact failure.
        # Do not guess correspondence for records whose values and sources both differ.
        value_match = next(((index, record) for index, record in remaining if _matches(record, expected)), None)
        if value_match:
            remaining.remove(value_match)
            score["source_rows"].append({"status": "value_matches_but_source_differs", "expected": expected,
                                         "actual": value_match[1],
                                         "differences": _source_differences(value_match[1], expected)})
        else:
            score["source_rows"].append({"status": "no_matching_source_record", "expected": expected,
                                         "actual": None, "differences": []})
    score.update(matched_sources=matched, unmatched_sources=len(annotated) - matched)
    return score


def score_table_records(records: list[dict] | None, gold: list[dict], *, check_sources: bool = True,
                        task: dict | None = None) -> dict:
    """One-to-one frozen-reference matching, without claiming semantic review.

    None means no evaluable output. An actual empty list is an evaluated empty
    result, which misses every nonempty reference target. Duplicate outputs are
    counted as extras instead of matching one target repeatedly.
    """
    source_score = _source_score(records, gold, check_sources)
    source_score.update(_cell_reference_score(records, gold, check_sources))
    if records is None:
        return {"evaluated": False, "matched": None, "expected": len(gold),
                "matched_without_aliases": None, "matched_format_equivalent": None,
                "missing": None, "extra": None, "rows": [], **source_score}
    remaining = list(enumerate(records))
    rows = []
    matches = 0
    for expected in gold:
        match = next(((index, item) for index, item in remaining if _matches(item, expected)), None)
        if match is None:
            rows.append({"status": "missing_or_different", "expected": expected, "actual": None})
        else:
            matches += 1
            remaining.remove(match)
            rows.append({"status": "matched", "expected": expected, "actual": match[1]})
    rows.extend({"status": "extra_or_different", "expected": None, "actual": item}
                for _, item in remaining)
    exact_remaining = list(records)
    exact_matches = 0
    for expected in gold:
        exact_expected = {key: value for key, value in expected.items() if key != "aliases"}
        index = next((i for i, item in enumerate(exact_remaining) if _matches(item, exact_expected)), None)
        if index is not None:
            exact_remaining.pop(index)
            exact_matches += 1
    format_matches = None
    declared = _format_fields(task)
    if any(declared.values()):
        format_remaining = list(records)
        format_matches = 0
        for expected in gold:
            index = next((i for i, item in enumerate(format_remaining)
                          if _matches(item, expected, format_fields=declared.get(expected.get("record_type"), set()))), None)
            if index is not None:
                format_remaining.pop(index)
                format_matches += 1
    return {"evaluated": True, "matched": matches, "expected": len(gold),
            "matched_without_aliases": exact_matches,
            "matched_format_equivalent": format_matches,
            "missing": len(gold) - matches, "extra": len(remaining), "rows": rows, **source_score}


def _cell_reference_score(records: list[dict] | None, gold: list[dict], check_sources: bool) -> dict:
    """A separate coarse metric: same cells do not prove correct quote or meaning."""
    annotated = [item for item in gold if isinstance(item.get("expected_sources"), dict)
                 and item["expected_sources"]]
    checked = records is not None and bool(annotated) and check_sources
    result = {"cell_references_checked": checked, "matched_cell_references": None,
              "expected_cell_reference_records": len(annotated)}
    if not checked:
        return result
    remaining = list(enumerate(records))
    matches = 0
    for expected in annotated:
        match = None
        for index, record in remaining:
            if record.get("record_type") != expected.get("record_type"):
                continue
            fields = record.get("fields", {})
            if not isinstance(fields, dict):
                continue
            same = True
            for name, selections in expected["expected_sources"].items():
                binding = fields.get(name)
                actual = binding.get("sources") if isinstance(binding, dict) else None
                if (not isinstance(actual, list) or
                        any(not isinstance(item, dict) or not isinstance(item.get("ref"), str) for item in actual)
                        or Counter(item["ref"] for item in actual) != Counter(item["ref"] for item in selections)):
                    same = False
                    break
            if same:
                match = (index, record)
                break
        if match is not None:
            matches += 1
            remaining.remove(match)
    result["matched_cell_references"] = matches
    return result


def _source_score_html(score: dict, case_id: str, known: set[str]) -> str:
    if not score["source_checked"]:
        reasons = {"source_check_disabled": "本次未进行来源评分",
                   "no_output": "没有可评分的输出", "missing_gold_sources": "参考答案未提供原格标注"}
        return ('<p>原格来源匹配：未评估（' + reasons[score["source_score_reason"]]
                + '）。不自动当作来源正确。</p>')
    output = [f'<p>原格来源匹配 {score["matched_sources"]}/{score["expected_source_records"]}（原格及引文严格比较）；'
              f'来源不同或未匹配 {score["unmatched_sources"]}；'
              f'没有来源标注的目标 {score["unannotated_source_records"]}。'
              '来源仅检查参考答案已标注的字段，与字段值独立评分；引用匹配也不等于专家语义审核。</p>',
              f'<p>仅单元格编号一致：{score["matched_cell_references"]}/{score["expected_cell_reference_records"]}。'
              '这一项忽略引文长短和显示值，不能证明格内位置或语义正确。严格字符串未匹配不等于科学内容错误。</p>',
              '<details><summary>逐条核对原格来源，包括“值对但来源错”</summary>'
              '<table><thead><tr><th>来源结果</th><th>固定原格标注</th><th>实际记录与差异</th></tr></thead><tbody>']
    for row in score["source_rows"]:
        output.append('<tr><td>' + escape(row["status"]) + '</td><td><pre>'
                      + escape(_text(row["expected"]["expected_sources"])) + '</pre>'
                      + _source_links(row["expected"]["expected_sources"], case_id, known)
                      + '</td><td><pre>' + escape(_text({"record": row["actual"], "differences": row["differences"]}))
                      + '</pre>' + _source_links(row["actual"], case_id, known) + '</td></tr>')
    return "".join(output) + '</tbody></table></details>'


def _score_html(title: str, score: dict, case_id: str, known: set[str]) -> str:
    if not score["evaluated"]:
        return (f'<h4>{escape(title)}</h4><p>未评估：没有可评分的解析结果。不是0条错误，也不是通过。</p>'
                + _source_score_html(score, case_id, known))
    output = [f'<h4>{escape(title)}</h4><p>参考值匹配 {score["matched"]}/{score["expected"]}；'
              f'缺失或不同 {score["missing"]}；额外或不同 {score["extra"]}。</p>',
              f'<p>不使用名称别名的严格写法匹配：{score["matched_without_aliases"]}/{score["expected"]}。'
              '参考值匹配仅使用调用前冻结的别名，不表示科学关系已核验。</p>',
              _source_score_html(score, case_id, known),
              '<table><thead><tr><th>状态</th><th>固定参考答案</th><th>实际记录</th></tr></thead><tbody>']
    if score.get("matched_format_equivalent") is not None:
        output.insert(2, f'<p>格式等价匹配：{score["matched_format_equivalent"]}/{score["expected"]}。'
                      '仅测量值和单位比较忽略外围空白与一层单美元符；不改原值、参考或证据，不代表科学核验。</p>')
    for row in score["rows"]:
        output.append(f'<tr><td>{escape(row["status"])}</td><td><pre>{escape(_text(row["expected"]))}</pre></td>'
                      f'<td><pre>{escape(_text(row["actual"]))}</pre>'
                      f'{_source_links(row["actual"], case_id, known)}</td></tr>')
    output.append("</tbody></table>")
    return "".join(output)


def _records(value, key: str) -> list[dict] | None:
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return None
    result = value[key]
    return result if all(isinstance(item, dict) for item in result) else None


def _issues(evaluation, reconciliation) -> list[dict]:
    """The same diagnostic copied into reconciliation counts only once."""
    issues = []
    for value, key in ((evaluation, "issues"), (reconciliation, "unresolved")):
        for issue in value.get(key, []) if isinstance(value, dict) else []:
            if isinstance(issue, dict) and issue not in issues:
                issues.append(issue)
    return issues


def _candidates(evaluation, reconciliation) -> list[dict] | None:
    if any((value or {}).get("schema_version") == "table-route-v3" for value in (evaluation, reconciliation)):
        for value in (reconciliation, evaluation):
            records = _records(value, "candidates")
            if records is not None:
                return records
        return None
    for value, key in ((reconciliation, "candidates"), (evaluation, "candidates"),
                       (evaluation, "records"), (reconciliation, "current_results"),
                       (evaluation, "current_results")):
        records = _records(value, key)
        if records is not None:
            return records
    return None


def delivery_fidelity(raw: list[dict] | None, candidates: list[dict] | None) -> dict:
    """Check original fields, types and order, independently of reference answers."""
    if raw is None or candidates is None:
        return {"assessed": False, "expected_records": len(raw or []), "unchanged_records": None,
                "changed_indices": [], "extra_records": None}
    changed = []
    for index, original in enumerate(raw):
        candidate = candidates[index] if index < len(candidates) else {}
        actual = {key: candidate[key] for key in original if key in candidate}
        same = json.dumps(actual, sort_keys=True, ensure_ascii=False) == json.dumps(original, sort_keys=True, ensure_ascii=False)
        if "values" in candidate:
            same = same and json.dumps(candidate["values"], sort_keys=True, ensure_ascii=False) == json.dumps(
                _record_values(original), sort_keys=True, ensure_ascii=False)
        if not same:
            changed.append(index)
    return {"assessed": True, "expected_records": len(raw), "unchanged_records": len(raw) - len(changed),
            "changed_indices": changed, "extra_records": max(0, len(candidates) - len(raw))}


def _category_issues(evaluation, reconciliation, category: str) -> list[dict]:
    issues = []
    for value in (evaluation, reconciliation):
        if not isinstance(value, dict):
            continue
        candidates = value.get("candidates", [])
        groups = [value.get(category, [])]
        groups.extend(record.get(category, []) for record in candidates if isinstance(record, dict))
        for group in groups:
            for issue in group:
                if isinstance(issue, dict) and issue not in issues:
                    issues.append(issue)
    return issues


def _diagnostic_counts(issues: list[dict]) -> dict:
    records = {issue[key] for issue in issues for key in ("record_id", "candidate_record_id")
               if issue.get(key) is not None}
    fields = {(issue.get("record_id"), issue["field"]) for issue in issues
              if issue.get("record_id") is not None and issue.get("field") is not None}
    return {"affected_records": len(records), "affected_fields": len(fields), "diagnostics": len(issues)}


def _require_a_slots(slots: list[dict]) -> None:
    if any(slot.get("stage") != "A" for slot in slots):
        raise ValueError("Only A reports are supported; open the saved historical REPORT.html "
                         "or import A responses with fork-replay into a new directory")


def summarize_table_bundle(bundle: Path) -> dict:
    """Summarize saved evidence without calls or rewriting original artifacts.

    Runner entry points verify the frozen bundle and trial hashes first. Counts
    describe saved artifacts, not token accounting or expert semantic acceptance.
    """
    root = Path(bundle).resolve()
    def read(relative: str, default=None):
        return _read(root / relative, default, root=root)
    cases = {case["id"]: case for case in read("cases.json", [])}
    observe_only = read("manifest.json", {}).get("schema_version") == "table-a-0.3"
    slots = read("slots.json", [])
    _require_a_slots(slots)
    statuses, issue_codes = Counter(), Counter()
    affected_records, affected_fields = set(), set()
    diagnostics = 0
    saved_responses = saved_live_responses = evaluated_stages = problem_stages = 0
    confirmed_live_http = live_reserved_stages = 0
    classified = {category: [] for category in _CHECK_CATEGORIES}
    classified_stages = rejected_records = parse_diagnostics = parsed_records = 0
    routes = {}
    def metrics():
        return {"planned_trials": 0, "evaluated_trials": 0, "trials_without_gold": 0,
                "expected_records": 0, "evaluated_expected_records": 0, "matched": 0,
                "matched_without_aliases": 0, "matched_format_equivalent": 0, "format_evaluated_trials": 0,
                "missing_in_evaluated_trials": 0, "extra_in_evaluated_trials": 0,
                "source_checked_trials": 0, "expected_source_records": 0,
                "matched_sources": 0, "unmatched_sources_in_evaluated_trials": 0,
                "unassessed_source_records": 0,
                "matched_cell_references": 0}
    phases = ("raw", "candidates") if observe_only else ("raw", "candidates", "current")
    routes["A"] = {phase: metrics() for phase in phases}
    fidelity = {"assessed_trials": 0, "expected_records": 0, "unchanged_records": 0,
                "changed_records": 0, "extra_records": 0, "unassessed_trials": 0}
    for slot in slots:
        relative = f"trials/{slot['slot_id']}"
        outcome = read(relative + "/outcome.json", {})
        reservation = read(relative + "/reservation.json")
        mode = outcome.get("mode") or (reservation or {}).get("mode")
        status = outcome.get("status")
        if status is None:
            status = "reserved_unknown" if (root / relative / "reservation.json").exists() else "not_run"
        statuses[status] += 1
        if read(relative + "/response.json") is not None:
            saved_responses += 1
            saved_live_responses += mode == "live"
        if mode == "live" and reservation is not None:
            live_reserved_stages += 1
            for path in (root / relative / "transport/http").glob("*.connection.json"):
                observation = _read(path, {}, root=root)
                if (observation.get("request_kind") == "model_post"
                        and observation.get("observation_status") == "observed"
                        and observation.get("custom_transport") is False
                        and observation.get("status_code") == 200
                        and observation.get("body_complete") is True):
                    confirmed_live_http += 1
                    break  # Count stages, not trace files or TLS probes.
        evaluation = read(relative + "/evaluation.json")
        reconciliation = read(relative + "/reconciliation.json")
        parsed = read(relative + "/parsed.json")
        rejections = (parsed or {}).get("rejections", [])
        rejected_records += len(rejections)
        parse_diagnostics += sum(len(item.get("errors", [])) for item in rejections)
        if isinstance(evaluation, dict) and "candidates" in evaluation:
            classified_stages += 1
        for category in _CHECK_CATEGORIES:
            for issue in _category_issues(evaluation, reconciliation, category):
                # Prefix IDs only in the aggregate so equal local IDs in different tables stay distinct.
                prefixed = dict(issue)
                for key in ("record_id", "candidate_record_id"):
                    if prefixed.get(key) is not None:
                        prefixed[key] = (slot["slot_id"], prefixed[key])
                classified[category].append(prefixed)
        issues = _issues(evaluation, reconciliation)
        if isinstance(evaluation, dict):
            evaluated_stages += 1
        diagnostics += len(issues)
        for issue in issues:
            record_ids = {issue[key] for key in ("record_id", "candidate_record_id")
                          if issue.get(key) is not None}
            for record_id in record_ids:
                affected_records.add((slot["slot_id"], record_id))
                if issue.get("field") is not None:
                    affected_fields.add((slot["slot_id"], record_id, issue["field"]))
        if issues:
            problem_stages += 1
            issue_codes.update(str(issue.get("code", "unknown")) for issue in issues)
        stage = slot["stage"]
        case = cases.get(slot["case_id"], {})
        gold = case.get("gold")
        gold = gold if isinstance(gold, list) else []
        raw = _records(parsed, "records")
        parsed_records += len(raw or [])
        candidates = _candidates(evaluation, reconciliation)
        if observe_only:
            preserved = delivery_fidelity(raw, candidates)
            fidelity["expected_records"] += preserved["expected_records"]
            fidelity["assessed_trials"] += preserved["assessed"]
            fidelity["unassessed_trials"] += not preserved["assessed"]
            if preserved["assessed"]:
                fidelity["unchanged_records"] += preserved["unchanged_records"]
                fidelity["changed_records"] += len(preserved["changed_indices"])
                fidelity["extra_records"] += preserved["extra_records"]
        current = _records(reconciliation, "current_results")
        if current is None:
            current = _records(evaluation, "current_results")
        for phase, records in (("raw", raw), ("candidates", candidates), ("current", current)):
            if phase not in phases:
                continue
            summary = routes[stage][phase]
            summary["planned_trials"] += 1
            summary["trials_without_gold"] += not bool(gold)
            summary["expected_records"] += len(gold)
            score = score_table_records(records, gold, task=case.get("task") if observe_only else None)
            summary["expected_source_records"] += score["expected_source_records"]
            if score["evaluated"] and gold:
                summary["evaluated_trials"] += 1
                summary["evaluated_expected_records"] += len(gold)
                summary["matched"] += score["matched"]
                summary["matched_without_aliases"] += score["matched_without_aliases"]
                if score["matched_format_equivalent"] is not None:
                    summary["matched_format_equivalent"] += score["matched_format_equivalent"]
                    summary["format_evaluated_trials"] += 1
                summary["missing_in_evaluated_trials"] += score["missing"]
                summary["extra_in_evaluated_trials"] += score["extra"]
            if score["source_checked"]:
                summary["source_checked_trials"] += 1
                summary["matched_sources"] += score["matched_sources"]
                summary["unmatched_sources_in_evaluated_trials"] += score["unmatched_sources"]
                summary["matched_cell_references"] += score["matched_cell_references"]
                summary["unassessed_source_records"] += score["unannotated_source_records"]
            else:
                summary["unassessed_source_records"] += len(gold)
    for route in routes.values():
        for summary in route.values():
            if not summary["format_evaluated_trials"]:
                summary["matched_format_equivalent"] = None
            if not summary["evaluated_trials"]:
                summary["matched"] = None
                summary["matched_without_aliases"] = None
            if not summary["source_checked_trials"]:
                summary["matched_sources"] = None
                summary["matched_cell_references"] = None
    quality_status = "not_evaluated" if not evaluated_stages else "checks_completed_not_semantically_accepted"
    if problem_stages:
        quality_status = "issues_found"
    current_scores = [route["candidates" if observe_only else "current"] for route in routes.values()]
    if not problem_stages and evaluated_stages and any(
            score["planned_trials"] > score["evaluated_trials"] or score["trials_without_gold"]
            or score["unassessed_source_records"] for score in current_scores):
        quality_status = "reference_unassessed"
    if not problem_stages and any(score["missing_in_evaluated_trials"] or score["extra_in_evaluated_trials"]
                                  or score["unmatched_sources_in_evaluated_trials"]
                                  for score in current_scores):
        quality_status = "reference_mismatches"
    if rejected_records:
        quality_status = "partial_format_error" if parsed_records else "format_error"
    if observe_only and (fidelity["changed_records"] or fidelity["extra_records"]):
        quality_status = "delivery_changed"
    return {"execution": {"planned_slots": len(slots), "statuses": dict(statuses),
                           "completed_stages": statuses["success"], "saved_responses": saved_responses,
                           "partial_format_error_stages": statuses["partial_format_error"],
                           "isolated_records": rejected_records, "parse_diagnostics": parse_diagnostics,
                           "parsed_records": parsed_records,
                           "saved_live_responses": saved_live_responses,
                           "live_reserved_stages": live_reserved_stages,
                           "confirmed_complete_live_http_stages": confirmed_live_http,
                           "live_stages_without_complete_http_evidence": live_reserved_stages - confirmed_live_http},
            "quality_summary": {"status": quality_status, "evaluated_stages": evaluated_stages,
                                "processing_policy": "observe_only" if observe_only else "historical",
                                "delivery_fidelity": fidelity if observe_only else None,
                                "stages_with_issues": problem_stages,
                                "affected_records": len(affected_records),
                                "affected_fields": len(affected_fields), "diagnostics": diagnostics,
                                "issue_counts": dict(sorted(issue_codes.items())), "routes": routes,
                                "classified_stages": classified_stages,
                                "check_categories": {key: _diagnostic_counts(items) for key, items in classified.items()},
                                "semantic_verification": "not_run",
                                "expert_review_status": "not_assessed",
                                "notice": "success means the stage completed, not that its content is correct. "
                                          "Missing gold, missing source annotations and missing responses are unassessed. "
                                          "Review raw responses and evidence checks; do not rerun paid calls to inspect results."}}


def _summary_html(summary: dict) -> str:
    execution, quality = summary["execution"], summary["quality_summary"]
    output = ['<section><h2>执行完成与内容质量分开看</h2>',
              f'<p>步骤完成 {execution["completed_stages"]}/{execution["planned_slots"]}；'
              f'已保存响应 {execution["saved_responses"]}，其中真实运行响应 {execution["saved_live_responses"]}。'
              f'程序发现问题的步骤 {quality["stages_with_issues"]}/{quality["evaluated_stages"]}。</p>',
              f'<p>部分格式错误步骤 {execution["partial_format_error_stages"]}；'
              f'隔离记录 {execution["isolated_records"]} 条，格式诊断 {execution["parse_diagnostics"]} 条。'
              '其余可解析记录继续处理，部分格式错误不能计为完全成功。</p>',
              f'<p>有完整HTTP 200响应证据的真实步骤：{execution["confirmed_complete_live_http_stages"]}；'
              f'已预留但缺少此证据：{execution["live_stages_without_complete_http_evidence"]}。'
              '离线回放不计为HTTP成功，响应完整也不证明科学内容正确。</p>',
              '<p><strong>success 不是抽取正确率。</strong>下表只汇总冻结参考答案；'
              '未评估部分不能算通过，来源匹配也不等于专家审核。评分只针对事先冻结的任务范围。'
              '严格字符串未匹配不等于科学内容错误；例如完整表头引文和预设短引文可能指向同一原格。</p>',
              f'<p>受影响记录 {quality["affected_records"]} 条；受影响字段 {quality["affected_fields"]} 个；'
              f'诊断 {quality["diagnostics"]} 条。同一字段可触发多条诊断，不能把它们当作独立科学错误。</p>',
              '<table><thead><tr><th>路线 / 阶段</th><th>值匹配 / 已评估目标</th>'
              '<th>不使用别名的严格写法匹配</th>'
              '<th>格式等价匹配</th>'
              '<th>全部计划目标 / 已评估试次</th><th>原格及引文严格匹配 / 已标注目标</th>'
              '<th>仅单元格编号一致 / 已标注目标</th>'
              '<th>未评估来源目标 / 缺参考答案试次</th></tr></thead><tbody>']
    for stage, route in quality["routes"].items():
        for phase, score in route.items():
            matched = "未评估" if score["matched"] is None else str(score["matched"])
            sources = "未评估" if score["matched_sources"] is None else str(score["matched_sources"])
            cells = "未评估" if score["matched_cell_references"] is None else str(score["matched_cell_references"])
            label = {"raw": "模型原始输出（成功解析部分）", "candidates": "抽取结果（附检查状态）",
                     "current": "旧严格规则通过结果"}[phase]
            exact = "未评估" if score["matched_without_aliases"] is None else str(score["matched_without_aliases"])
            formatted = "未评估" if score.get("matched_format_equivalent") is None else str(score["matched_format_equivalent"])
            output.append(f'<tr><td>{stage} / {label}</td><td>{matched} / {score["evaluated_expected_records"]}</td>'
                          f'<td>{exact} / {score["evaluated_expected_records"]}</td>'
                          f'<td>{formatted}</td>'
                          f'<td>{score["expected_records"]} / {score["evaluated_trials"]}次</td>'
                          f'<td>{sources} / {score["expected_source_records"]}</td>'
                          f'<td>{cells} / {score["expected_source_records"]}</td>'
                          f'<td>{score["unassessed_source_records"]} / {score["trials_without_gold"]}次</td></tr>')
    output.append('</tbody></table><p>抽取结果完整交付，不等于全部科学关系已独立核验。'
                  '格式等价只比较声明的测量值和单位的外围空白、单层美元符；不进行单位换算或修改证据。</p>')
    if quality.get("delivery_fidelity") is not None:
        fidelity = quality["delivery_fidelity"]
        output.append(f'<p>交付保真：{fidelity["unchanged_records"]}/{fidelity["expected_records"]} 条原样保留；'
                      f'改动或遗漏 {fidelity["changed_records"]} 条，额外记录 {fidelity["extra_records"]} 条；'
                      f'尚未评估 {fidelity["unassessed_trials"]} 次。保真不等于内容正确。</p>')
    else:
        output.append('<p>历史“检查后的可用结果”仅作为旧严格规则对照展示。</p>')
    output.append(f'<p>具有分项检查状态的步骤：{quality["classified_stages"]}；旧产物未分类不视为无问题。</p>')
    output.append('<p>“格式与原文字面检查”包含检查失败和无法确认的情况；请查看字段状态区分。'
                  '未确认不等于数值错误，不会自动调用 Jev。</p>')
    for category, label in _CHECK_CATEGORIES.items():
        counts = quality["check_categories"][category]
        output.append(f'<p>{label}：{counts["affected_records"]} 条记录 / '
                      f'{counts["affected_fields"]} 个字段 / {counts["diagnostics"]} 条诊断。</p>')
    output.append('<details><summary>问题类型与完整分层摘要</summary><pre>'
                  + escape(_text(summary)) + '</pre></details></section>')
    return "".join(output)


def _issues_html(evaluation, reconciliation, case_id: str, known: set[str]) -> str:
    issues = _issues(evaluation, reconciliation)
    if not issues:
        return ""
    groups = {}
    for issue in issues:
        groups.setdefault((issue.get("record_id"), issue.get("field")), []).append(issue)
    records = {issue[key] for issue in issues for key in ("record_id", "candidate_record_id")
               if issue.get(key) is not None}
    fields = {key for key in groups if key[0] is not None and key[1] is not None}
    is_v2 = isinstance(evaluation, dict) and "candidates" in evaluation
    is_v3 = (evaluation or {}).get("schema_version") == "table-route-v3"
    heading = ('<h3>原文与关系检查（不改写模型字段）</h3>' if is_v3 else
               '<h3>旧严格规则诊断（含关联未确认）</h3>' if is_v2 else
               '<h3 class="bad">需要定位的问题</h3>')
    cell_class = "" if is_v2 else ' class="bad"'
    output = [heading,
              f'<p>受影响记录 {len(records)} 条；受影响字段 {len(fields)} 个；诊断 {len(issues)} 条。</p>',
              '<table><thead><tr><th>记录 / 字段</th><th>诊断代码（同一字段归组）</th>'
              '<th>详细位置与依据</th></tr></thead><tbody>']
    for (record_id, field), group in groups.items():
        output.append('<tr><td>' + escape(str(record_id or "阶段")) + ' / '
                      + escape(str(field or "整条记录")) + '</td><td' + cell_class + '>'
                      + escape(" · ".join(str(item.get("code", "unknown")) for item in group))
                      + '</td><td><details><summary>展开此字段的全部诊断</summary><pre>'
                      + escape(_text(group)) + '</pre>' + _source_links(group, case_id, known)
                      + '</details></td></tr>')
    return "".join(output) + '</tbody></table>'


def _pending_html(evaluation, reconciliation, case_id: str, known: set[str]) -> str:
    if isinstance(evaluation, dict) and "candidates" in evaluation:
        output = ['<h3>分项检查与优先检查入口</h3><p>关联未自动确认不会自动调用 Jev。'
                  '字面检查可能失败或无法确认，未确认不等于数值错误。'
                  '请按字段状态区分；内容仍在抽取结果中保留。</p>']
        for category, label in _CHECK_CATEGORIES.items():
            issues = _category_issues(evaluation, reconciliation, category)
            counts = _diagnostic_counts(issues)
            output.append('<details><summary>' + label + '：'
                          + str(counts["affected_records"]) + ' 条记录 / '
                          + str(counts["affected_fields"]) + ' 个字段 / '
                          + str(counts["diagnostics"]) + ' 条诊断</summary><pre>'
                          + escape(_text(issues)) + '</pre>' + _source_links(issues, case_id, known) + '</details>')
        return "".join(output)
    issues = _issues(evaluation, reconciliation)
    affected = {issue[key] for issue in issues for key in ("record_id", "candidate_record_id")
                if issue.get(key) is not None}
    pending = []
    for item in _records(evaluation, "records") or []:
        if item.get("status") != "source_checked" or item.get("record_id") in affected:
            pending.append(item)
    for item in _records(reconciliation, "history") or []:
        if item.get("record_id") in affected and item not in pending:
            pending.append(item)
    changes = []
    for record in _records(evaluation, "records") or []:
        changes.extend(record.get("changes", []))
    for change in (reconciliation or {}).get("changes", []):
        if change not in changes:
            changes.append(change)
    return ('<h3>待复核候选</h3><p>' + str(len(pending)) + ' 条；未进入可用结果不代表原值变成 null。'
            '原始回答与解析结果始终保留。</p><details><summary>查看候选及逐项修改记录</summary><pre>'
            + escape(_text({"pending_candidates": pending, "changes": changes})) + '</pre>'
            + _source_links(pending, case_id, known) + '</details>')


def _candidate_html(evaluation, reconciliation, case_id: str, known: set[str]) -> str:
    records = _candidates(evaluation, reconciliation)
    if records is None:
        return '<h3>抽取结果（附检查状态）</h3><p>尚无可交付的解析结果；原始响应及格式错误仍可查看。</p>'
    output = ['<h3>抽取结果（附检查状态）</h3>',
              f'<p>共 {len(records)} 条。完整交付不等于全部科学关系已核验。'
              '局部位置检查仅核对同行、同列、跨度或引用的上下文类型，不证明对象归属或共享单位适用。'
              '范围引文可定位，也不证明累计包含哪些成员。'
              '关系未确认时保留内容；明确错误和具体风险仍需检查。</p>']
    for record in records:
        output.append('<details open><summary>记录 ' + escape(str(record.get("record_id", "未提供编号")))
                      + '</summary><pre>' + escape(_text(_record_values(record))) + '</pre>'
                      + _source_links(record, case_id, known))
        checks = record.get("field_checks")
        if isinstance(checks, dict):
            output.append('<table><thead><tr><th>字段</th><th>原文定位</th><th>数值／单位字面检查</th>'
                          '<th>局部位置关系</th><th>科学关系独立核验</th></tr></thead><tbody>')
            for field, check in checks.items():
                output.append('<tr><td>' + escape(field) + '</td>')
                for key in ("quote_location", "literal_check", "relation_check", "semantic_verification"):
                    output.append('<td>' + escape(table_check_label(check.get(key), check=key)) + '</td>')
                output.append('</tr>')
            output.append('</tbody></table>')
        else:
            output.append('<p>旧产物没有分项检查状态，不视为这些检查均已通过。</p>')
        scope = record.get("scope_check")
        if isinstance(scope, dict):
            output.append('<p>范围声明：' + escape(table_check_label(scope.get("declared_scope")))
                          + '；范围引文定位：' + escape(table_check_label(scope.get("scope_quote_location")))
                          + '；成员逐项出处核验：'
                          + escape(table_check_label(scope.get("member_evidence_check"), check="member_evidence_check"))
                          + '。</p>')
        else:
            output.append('<p>此产物未记录成员逐项出处核验状态，不能视为成员依据完整。</p>')
        output.append('<details><summary>完整记录、原始出处与检查信息</summary><pre>'
                      + escape(_text(record)) + '</pre></details></details>')
    evidence = (reconciliation or {}).get("evidence", (evaluation or {}).get("evidence"))
    if evidence is not None:
        output.append('<details><summary>实际输入的共享表格证据包</summary>'
                      '<p>完整表格、表题和脚注可查，不等于每个对象或条件已被程序证明。'
                      '未加入模型当次看不到的其他原文。</p><pre>' + escape(_text(evidence)) + '</pre></details>')
    if (evaluation or {}).get("schema_version") != "table-route-v3":
        changes = (reconciliation or {}).get("all_changes", (evaluation or {}).get("all_changes", []))
        output.append('<details><summary>全部工作副本修正（历史产物）</summary><pre>'
                      + escape(_text(changes)) + '</pre>' + _source_links(changes, case_id, known) + '</details>')
    else:
        associations = {key: (reconciliation or {}).get(key, []) for key in ("associations", "conflicts")}
        output.append('<details><summary>重复、历史关联与冲突注记（不替换本轮记录）</summary><pre>'
                      + escape(_text(associations)) + '</pre></details>')
    return "".join(output)


def _rejections_html(parsed) -> str:
    rejections = (parsed or {}).get("rejections", [])
    if not rejections:
        return ""
    output = ['<h3 class="bad">被隔离的格式错误记录</h3><p>数组位置从0开始。'
              '这些记录未冒充成功结果；其余合法记录继续处理。</p>']
    for rejection in rejections:
        output.append('<details open><summary>原始数组位置 ' + escape(str(rejection.get("index")))
                      + '</summary><pre>' + escape(_text(rejection)) + '</pre></details>')
    return "".join(output)


def render_table_report(bundle: Path) -> str:
    """Render frozen inputs and every available stage, including partial runs."""
    root = Path(bundle).resolve()
    def read(path: Path, default=None):
        return _read(path, default, root=root)
    manifest = read(root / "manifest.json", {})
    observe_only = manifest.get("schema_version") == "table-a-0.3"
    cases = read(root / "cases.json", [])
    slots = read(root / "slots.json", [])
    _require_a_slots(slots)
    tables = read(root / "table-inputs.json", {})
    frozen_requests = read(root / "requests.json", {})
    by_case = {case["id"]: case for case in cases}
    rows = []
    counts = Counter()
    for slot in slots:
        trial = root / "trials" / slot["slot_id"]
        trial.resolve().relative_to(root)
        outcome = read(trial / "outcome.json")
        if outcome is None:
            status = "reserved_unknown" if (trial / "reservation.json").exists() else "not_run"
            outcome = {"status": status}
        counts[outcome.get("status", "unknown")] += 1
        rows.append((slot, trial, outcome))
    style = """
    :root{font-family:system-ui,'Microsoft YaHei',sans-serif;color:#173047;background:#f4f7fa}
    body{max-width:1500px;margin:auto;padding:24px}h1,h2,h3{color:#123c55}a{color:#075fa6}
    .notice,article,section{background:white;padding:20px;border:1px solid #dbe3e9;border-radius:8px;margin:18px 0}
    table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #cbd5df;padding:9px;text-align:left;vertical-align:top}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:540px;overflow:auto;margin:8px 0;font-size:13px}
    .table-scroll{overflow:auto}.source-table{width:max-content;min-width:100%}.source-table td{max-width:320px;min-width:90px}
    small{display:block;color:#556876;overflow-wrap:anywhere;font-size:11px}details{border-top:1px solid #dde5eb;padding:12px 0}
    summary{cursor:pointer;font-weight:650}.bad{color:#a42323}.muted{color:#556876}
    :target{outline:4px solid #d88500;outline-offset:-3px;background:#fff3ce;scroll-margin-top:20px}
    .source-links{line-height:1.8}blockquote{border-left:4px solid #b8cddd;padding:10px;white-space:pre-wrap}
    """
    output = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>表格路线实验：输入、输出与错误定位</title>', f"<style>{style}</style></head><body>",
              '<h1>A 路线表格实验：每一步都能查看</h1>',
              '<div class="notice"><p>A：模型标出字段与原格关系，程序检查原文与引用。'
              '用户不需要指定列号或表格布局。</p>'
              '<p>这里展示实际请求、模型返回和程序检查，不表示模型未公开的内部思维过程。'
              'success只表示该阶段完成，不代表内容正确。参考值匹配、来源检查和专家审核是不同结论。</p>'
              '<p>没有模型响应的槽位记为未评估；离线replay使用合成或历史保存的回答，'
              '验证程序处理，不是新一轮真实模型调用。</p></div>',
              f'<p>计划槽位：{len(slots)}；状态：{escape(_text(dict(counts)))}。</p>',
              _summary_html(summarize_table_bundle(root)),
              '<details><summary>冻结清单</summary><pre>' + escape(_text(manifest)) + '</pre></details>',
              '<h2>快速定位</h2><ul>']
    for slot, _, outcome in rows:
        output.append(f'<li><a href="#trial-{escape(slot["slot_id"], quote=True)}">'
                      f'{escape(slot["case_id"])} · 第{escape(str(slot["round"]))}轮 · {escape(slot["stage"])}</a>：'
                      f'{escape(str(outcome.get("status", "unknown")))}</li>')
    output.append("</ul>")
    if manifest.get("replay_only"):
        output.append('<p class="notice">本目录是导入回答的离线回放副本，不允许真实调用。原实验记录未修改；是否覆盖回答见冻结清单。</p>')
    known_by_case = {}
    for case_id, case in by_case.items():
        table_html, known = _source_table(case_id, tables.get(case_id, {}))
        known_by_case[case_id] = known
        output.append(f'<section><h2>原文与任务：{escape(case_id)}</h2><p>点击结果中的来源编号，'
                      '可跳到下列原格并高亮。行列位置为索引值；原文范围是Unicode字符的左闭右开位置。</p>'
                      + table_html + '<details><summary>原始全文（未经改写）</summary><pre>'
                      + escape(case.get("source", "")) + '</pre></details>'
                      + '<details><summary>任务要求</summary><pre>' + escape(_text(case.get("task", {})))
                      + '</pre></details><details><summary>冻结参考答案（不发送给模型）</summary><pre>'
                      + escape(_text(case.get("gold", []))) + '</pre></details></section>')
    for slot, trial, outcome in rows:
        case_id, stage = slot["case_id"], slot["stage"]
        case, known = by_case.get(case_id, {}), known_by_case.get(case_id, set())
        parsed = read(trial / "parsed.json")
        evaluation = read(trial / "evaluation.json")
        reconciliation = read(trial / "reconciliation.json")
        output.append(f'<article id="trial-{escape(slot["slot_id"], quote=True)}"><h2>'
                      f'{escape(case_id)} · 第{escape(str(slot["round"]))}轮 · {escape(stage)}</h2>'
                      f'<p>执行模式：{escape(str(outcome.get("mode", "尚未执行")))}；状态：'
                      f'{escape(str(outcome.get("status", "unknown")))}。耗时：'
                      f'{escape(str(outcome.get("elapsed_seconds", "未报告")))}秒。</p>')
        if outcome.get("status") == "partial_format_error":
            output.append('<p class="bad">部分格式错误：部分记录已隔离，其余记录继续处理；不是完全成功。</p>')
        elif outcome.get("status") == "format_error":
            output.append('<p class="bad">格式错误：不能将无法解析的内容视为空成功。</p>')
        output.append(_candidate_html(evaluation, reconciliation, case_id, known))
        output.append(_rejections_html(parsed))
        output.append(_pending_html(evaluation, reconciliation, case_id, known))
        output.append('<details><summary>原文与关系检查</summary>'
                      + _issues_html(evaluation, reconciliation, case_id, known) + '</details>')
        expected_codes = case.get("expected_diagnostics", {}).get(stage)
        if isinstance(expected_codes, list):
            observed_codes = sorted({issue.get("code", "unknown") for issue in
                                     evaluation.get("issues", []) if isinstance(issue, dict)}) if evaluation else None
            output.append('<p>故障样例预设应出现的问题代码：<code>' + escape(_text(expected_codes))
                          + '</code>；实际检查代码：<code>' + escape(_text(observed_codes))
                          + '</code>。这是人为注入的测试条件，不发送给模型；额外问题仍需检查。</p>')
        output.append(_score_html("抽取结果：与冻结参考值比较",
                                  score_table_records(_candidates(evaluation, reconciliation), case.get("gold", []),
                                                      task=case.get("task") if observe_only else None),
                                  case_id, known))
        output.append('<details><summary>模型原始输出与交付保真</summary>' if observe_only else
                      '<details><summary>模型原始输出与旧严格结果对照</summary>')
        output.append(_score_html("模型原始输出（成功解析部分）：与参考值比较",
                                  score_table_records(_records(parsed, "records"), case.get("gold", []),
                                                      task=case.get("task") if observe_only else None),
                                  case_id, known))
        if observe_only:
            output.append('<p>交付字段、引用、关系及顺序与解析后的原始记录比较；不代表模型抽取正确。</p><pre>'
                          + escape(_text(delivery_fidelity(_records(parsed, "records"),
                                                           _candidates(evaluation, reconciliation)))) + '</pre>')
        else:
            current = _records(reconciliation, "current_results")
            if current is None:
                current = _records(evaluation, "current_results")
            output.append(_score_html("旧严格规则通过结果：与参考值比较",
                                      score_table_records(current, case.get("gold", [])), case_id, known))
        output.append('</details>')
        provenance = root / "replay-provenance" / slot["slot_id"]
        origin = read(provenance / "origin.json")
        if origin is not None:
            output.append('<h3>被回放回答的历史来源</h3><p>以下为保存的原请求和响应；'
                          '本轮回放未发送新请求。是否替换过回答见来源记录的 overridden 字段。</p>')
            if origin.get("overridden"):
                output.append('<p class="bad">本轮使用人为替换的回答，以下历史响应不是本轮被检查的回答；'
                              '被检查文本见本轮 response.json。</p>')
            request_label = ("产生原回答的历史实际请求" if origin.get("mode") == "live" else
                             "历史回放请求（未实际发送）" if origin.get("mode") == "replay" else
                             "历史保存请求（发送状态见来源记录）")
            for filename, label in (("origin.json", "来源包、模式与回答替换记录"),
                                    ("request.json", request_label),
                                    ("response.json", "历史原始响应")):
                path = provenance / filename
                value = read(path)
                if value is not None:
                    output.append('<details><summary>' + label + '</summary><p>'
                                  + _link(root, path, "打开历史来源JSON") + '</p><pre>'
                                  + escape(_text(value)) + '</pre></details>')
        for filename, label in _STEPS:
            path = trial / filename
            exists = path.exists()
            value = read(path) if exists else None
            if filename == "request.json" and not exists:
                value = frozen_requests.get(slot["slot_id"])
                if value is not None:
                    label += "（冻结草案，尚未发送）"
            elif filename == "request.json" and outcome.get("mode") == "replay":
                label = "1. 本轮回放请求草案（未发送，不冒充历史实际请求）"
            if value is None:
                if filename in {"request.json", "response.json", "parsed.json", "evaluation.json", "reconciliation.json"}:
                    output.append(f'<details><summary>{escape(label)}</summary><p>尚无该步骤产物</p></details>')
                continue
            opened = " open" if filename == "error.json" else ""
            output.append(f'<details{opened}><summary>{escape(label)}</summary>')
            if exists:
                output.append("<p>" + _link(root, path, "打开原始JSON文件") + "</p>")
            if filename == "response.json" and isinstance(value, dict) and isinstance(value.get("text"), str):
                output.append('<h4>原始模型文本</h4><pre>' + escape(value["text"]) + '</pre>'
                              '<h4>响应元数据与完整保存对象</h4>')
            output.append('<pre>' + escape(_text(value)) + '</pre>' + _source_links(value, case_id, known) + '</details>')
        output.append("</article>")
    output.append("</body></html>")
    return "".join(output)

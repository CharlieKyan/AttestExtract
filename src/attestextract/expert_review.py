"""Compact, loss-preserving review views; label grouping never accepts facts."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
import json
from pathlib import Path

from .diagnostics import RunTrace
from .evidence import is_missing
from .models import ExtractionResult


_NOTICE = (
    "审阅行只按完全相同的对象和条件文字整理，不证明是同一实体或同一情景，"
    "也不是正式数据库行。缺少条件不等于条件相同；同一字段的多条观测全部保留。"
    "原始数值、范围和引文不改写，不生成实体ID，不进行专家或模型裁决。"
)


def _merge_evidence(entries: list[dict]) -> list[dict]:
    """Store an identical quotation and location once within one observation."""
    merged = {}
    for entry in entries:
        item = deepcopy(entry)
        field = item.pop("field")
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in merged:
            merged[key] = {"fields": [], **item}
        merged[key]["fields"].append(field)
    return list(merged.values())


def _origin(result: ExtractionResult, chunks: list[str]) -> dict:
    origin = {"chunks": chunks}
    if result.run.routes:
        origin["models"] = []
        for chunk in chunks:
            provenance = result.run.chunk_provenance.get(chunk)
            if provenance is not None:
                origin["models"].append({"chunk": chunk, **provenance.model_dump(mode="json")})
    else:
        origin["model"] = result.run.model
    return origin


def _prose_item(result: ExtractionResult, index: int, simple: bool) -> dict:
    candidate = result.candidates[index]
    evidence = []
    for name, field in candidate.fields.items():
        value = field.evidence
        if not value.quote and not value.matches and not value.source_matches:
            continue
        entry = {"field": name, "quote": value.quote, "location_status": value.status}
        if value.start is not None and value.end is not None:
            entry["span"] = {"start": value.start, "end": value.end}
        if value.matches:
            entry["matches"] = [span.model_dump(mode="json") for span in value.matches]
        if value.source_matches:
            entry["source_matches"] = [match.model_dump(mode="json") for match in value.source_matches]
        if value.table_refs:
            entry["table_refs"] = [ref.model_dump(mode="json") for ref in value.table_refs]
        evidence.append(entry)
    values = {name: field.value for name, field in candidate.fields.items()}
    item = {
        "candidate_id": candidate.candidate_id, "record_type": candidate.record_type,
        "status": "needs_review" if candidate.needs_review or any(not check.passed for check in candidate.checks) else "program_checked",
        "semantic_verification": "not_run", "source_kind": "prose",
        "source_record": f"/candidates/{index}",
        **_origin(result, candidate.chunk_ids),
        "evidence": _merge_evidence(evidence),
        "field_status": dict(candidate.field_status),
        "failed_checks": [check.model_dump(mode="json") for check in candidate.checks if not check.passed],
        "issues": [review.model_dump(mode="json", exclude={"candidate_id"})
                   for review in result.review if review.candidate_id == candidate.candidate_id],
    }
    if candidate.source_context:
        item["context"] = [passage.model_dump(mode="json") for passage in candidate.source_context]
    if simple:
        return {**{name: values.get(name) for name in ("value", "unit", "entity", "conditions")}, **item}
    return {"fields": values, **item}


def _table_item(result: ExtractionResult, table_index: int, index: int, simple: bool,
                *, rejected: bool = False) -> dict:
    table = result.table_results[table_index]
    if rejected:
        rejection = table.rejections[index]
        raw = rejection.get("raw")
        record = raw if isinstance(raw, dict) else {}
        pointer = f"/table_results/{table_index}/rejections/{index}/raw"
        issues = deepcopy(rejection.get("errors", []))
    else:
        record = table.candidates[index]
        pointer = f"/table_results/{table_index}/candidates/{index}"
        issues = deepcopy(record.get("issues", []))
    fields = record.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    values, evidence, relations = {}, [], {}
    resolved = record.get("resolved_fields", {}) if not rejected else {}
    for name, binding in fields.items():
        if not isinstance(binding, dict):
            continue  # Malformed fields remain reachable through source_record.
        values[name] = deepcopy(binding.get("value"))
        relations[name] = binding.get("relation")
        sources = binding.get("sources")
        if not isinstance(sources, list):
            continue
        for selection in sources:
            if not isinstance(selection, dict):
                continue
            entry = {"field": name, "quote": deepcopy(selection.get("quote")),
                     "ref": deepcopy(selection.get("ref")), "location_status": "not_checked"}
            if "offset" in selection:
                entry["declared_offset"] = selection["offset"]
            # Use stored resolved sources only; do not locate or repair a rejection.
            matches = [value for value in resolved.get(name, [])
                       if value.get("ref") == selection.get("ref")
                       and value.get("selected_quote") == selection.get("quote")]
            if len(matches) == 1:
                entry["location_status"] = "located"
                entry["span"] = {"start": matches[0]["selected_start"], "end": matches[0]["selected_end"]}
            evidence.append(entry)
    item = {
        "candidate_id": f"{table.table_id}:{record.get('record_id', 'unknown')}",
        "record_type": record.get("record_type"),
        "status": "quarantined" if rejected else record.get("check_status", "not_checked"),
        "semantic_verification": "not_run", "source_kind": "table",
        "source_record": pointer, "table_id": table.table_id, "table_status": table.status,
        **_origin(result, [table.chunk_id]),
        "scope": record.get("scope"), "relations": relations,
        "anchors": deepcopy(record.get("anchors", [])),
        "scope_sources": deepcopy(record.get("scope_sources", [])),
        "evidence": _merge_evidence(evidence), "issues": issues,
    }
    if not rejected:
        item["field_checks"] = {name: {key: value for key, value in checks.items()
                                      if key not in {"mechanical_errors", "relationship_warnings", "risk_diagnostics"}}
                                for name, checks in record.get("field_checks", {}).items()}
    else:
        item["response_index"] = rejection.get("index")
        item["reason"] = "format_validation_failed; values below are unvalidated model declarations"
        if not isinstance(raw, dict) or not fields:
            item["unparsed_record"] = deepcopy(raw)
    if simple:
        extra = {name: value for name, value in values.items() if name not in {"value", "unit", "entity", "conditions"}}
        if extra:
            item["extra_fields"] = extra
        return {**{name: values.get(name) for name in ("value", "unit", "entity", "conditions")}, **item}
    return {"fields": values, **item}


def build_expert_review(result: ExtractionResult) -> dict:
    """Group exact labels for reading while preserving every candidate separately."""
    if result.run.routes:
        from .automatic_provenance import validate_automatic_provenance
        validate_automatic_provenance(result)
    simple = result.task.input_materials is not None
    items = [_prose_item(result, index, simple) for index in range(len(result.candidates))]
    quarantined = []
    for table_index, table in enumerate(result.table_results):
        items.extend(_table_item(result, table_index, index, simple) for index in range(len(table.candidates)))
        quarantined.extend(_table_item(result, table_index, index, simple, rejected=True)
                           for index in range(len(table.rejections)))
    rows, groups = [], {}
    for item in items:
        entity, conditions = item.get("entity"), item.get("conditions")
        # Collective table declarations remain distinct from individual observations.
        scope = item.get("scope")
        can_group = simple and isinstance(entity, str) and not is_missing(entity)
        key = json.dumps([entity, conditions, scope], ensure_ascii=False) if can_group else None
        if key is None or key not in groups:
            row = {"row_id": f"review-row-{len(rows) + 1:04d}",
                   "grouping": "exact_labels_only" if can_group else "individual_candidate",
                   "identity_verified": False, "entity": entity, "conditions": conditions,
                   "conditions_reported": not is_missing(conditions), "scope": scope, "fields": {}}
            rows.append(row)
            if key is not None:
                groups[key] = row
        else:
            row = groups[key]
        row["fields"].setdefault(item["record_type"], []).append(item)
    represented = {item["record_type"] for item in items if not simple or not is_missing(item.get("value"))}
    return {
        "review_version": "0.1", "view_only": True, "notice": _NOTICE,
        "source": result.source.model_dump(mode="json"), "source_result_schema": result.schema_version,
        "format_status": result.format_status, "task": result.task.name,
        "target_fields": list(result.task.record_types),
        "targets_without_retained_value": [name for name in result.task.record_types if name not in represented],
        "summary": {"review_rows": len(rows), "prose_candidates": len(result.candidates),
                    "table_candidates": sum(len(table.candidates) for table in result.table_results),
                    "quarantined_records": len(quarantined)},
        "rows": rows, "quarantined": quarantined,
        "associations": [link.model_dump(mode="json") for link in result.links],
    }


def _literal(value: object, secrets: Iterable[str] = ()) -> str:
    """Keep arbitrary source text inert in Markdown, including math and HTML."""
    # Reporting imports the index renderer, so share its escaping at render time.
    from .reporting import _cell

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
    return _cell(text, secrets)


def _list_text(values: list) -> str:
    return "、".join(str(value) for value in values)


def _location(evidence: dict) -> str:
    status = evidence.get("location_status", "原文位置")
    span = evidence.get("span")
    if span is not None:
        position = f"[{span['start']}, {span['end']})"
    elif evidence.get("source_matches"):
        position = f"{len(evidence['source_matches'])} 个规范化匹配位置，详见 JSON"
    elif evidence.get("matches"):
        position = f"{len(evidence['matches'])} 个匹配位置，详见 JSON"
    else:
        position = "未提供唯一定位"
    reference = f"；单元格 {evidence['ref']}" if evidence.get("ref") is not None else ""
    return f"{status}；{position}{reference}"


def _issue_text(issue: dict) -> str:
    location = issue.get("field") or ".".join(str(part) for part in issue.get("loc", []))
    code = issue.get("code", issue.get("type", ""))
    message = issue.get("message", issue.get("msg", ""))
    return "；".join(str(part) for part in (location, code, message) if part)


def _scope_text(scope: object) -> str:
    if scope is None:
        return "未声明"
    labels = {"individual": "单个观测", "group": "整体／合计组（未核实成员）",
              "each": "逐个成员（未核实逐个适用）"}
    if isinstance(scope, str) and scope in labels:
        return labels[scope]
    return "无效声明（未校验）：" + json.dumps(scope, ensure_ascii=False, allow_nan=False)


def _scope_evidence_lines(sources: object, secrets: Iterable[str], *, unvalidated: bool = False) -> list[str]:
    """Rejected declarations need not satisfy the Selection list contract."""
    label = "模型范围依据（未校验）" if unvalidated else "范围依据"
    if not isinstance(sources, list):
        return [f"- {_literal(label, secrets)}：{_literal(sources, secrets)}"]
    lines = []
    for selection in sources:
        if (isinstance(selection, dict) and isinstance(selection.get("quote"), str)
                and isinstance(selection.get("ref"), str)):
            lines.append(f"- {_literal(label, secrets)}：{_literal(selection['quote'], secrets)}；"
                         f"引用 {_literal(selection['ref'], secrets)}")
        else:
            lines.append(f"- 模型范围依据声明（未校验）：{_literal(selection, secrets)}")
    return lines


def _evidence_lines(evidence: dict, secrets: Iterable[str], *, unvalidated: bool = False) -> list[str]:
    label = _list_text(evidence["fields"])
    prefix = "模型引用（未校验）" if unvalidated else label
    return [f"- {_literal(prefix, secrets)}：{_literal(evidence['quote'], secrets)}",
            f"  {_literal(_location(evidence), secrets)}"]


def _index_lines(review: dict, secrets: Iterable[str] = ()) -> list[str]:
    def literal(value):
        return _literal(value, secrets)

    lines = ["## 按对象与条件整理的审阅行", "", _NOTICE, "",
             "| 审阅行 | 对象 | 条件 | 共同展示的字段（观测条数） |", "| --- | --- | --- | --- |"]
    for row in review["rows"]:
        fields = "；".join(f"{name}（{len(items)}）" for name, items in row["fields"].items())
        lines.append("| " + " | ".join(literal(value) for value in (
            row["row_id"], row["entity"] if row["entity"] is not None else "未报告／逐候选展示",
            row["conditions"] if row["conditions_reported"] else "未报告，不能视为同一情景", fields)) + " |")
    if not review["rows"]:
        lines.append("| 无 | 没有保留的候选 | — | 不能据此判定原文没有信息 |")
    lines += ["", f"隔离记录：{len(review['quarantined'])}，不进入以上审阅行。完整模型回答仍保存在原结果和诊断中。", ""]
    return lines


def render_review_index(result: ExtractionResult, *, secrets: Iterable[str] = ()) -> str:
    return "\n".join(_index_lines(build_expert_review(result), tuple(secrets)))


def render_expert_review(review: dict, *, secrets: Iterable[str] = ()) -> str:
    """Render complete quotations without repeating task schemas or raw responses."""
    secrets = tuple(secrets)
    def literal(value):
        return _literal(value, secrets)
    info_count = sum(issue.get("severity") == "info" for row in review["rows"]
                     for observations in row["fields"].values() for item in observations for issue in item["issues"])
    lines = ["# 专家审阅简表", "", f"文献：{literal(review['source']['name'])}",
             f"完整结果文件：{literal(review.get('source_result', 'result.json'))}",
             f"运行格式状态：{literal(review['format_status'])}；本文件仅供审阅，未作专家确认。", "",
             "字段值保持原文表达；范围不拆成推算的 min/max，未报告项不补齐。", "",
             *_index_lines(review, secrets)]
    if info_count:
        lines += [f"另有 {info_count} 项信息提示（例如可选字段未报告），完整记录见 review.json；不计作实质复核问题。", ""]
    for row in review["rows"]:
        lines += [f"## {literal(row['row_id'])}", "", f"对象：{literal(row['entity'])}；条件：{literal(row['conditions'])}",
                  f"范围声明：{literal(_scope_text(row['scope']))}。", "",
                  "| 字段 | 原值 | 单位 | 状态 | 候选引用 |", "| --- | --- | --- | --- | --- |"]
        for field, observations in row["fields"].items():
            for item in observations:
                lines.append("| " + " | ".join(literal(value) for value in (
                    field, item.get("value", item.get("fields")), item.get("unit"), item["status"], item["candidate_id"])) + " |")
        lines.append("")
        for field, observations in row["fields"].items():
            for item in observations:
                lines += [f"### {literal(field)} · {literal(item['candidate_id'])}", "",
                          f"来源：{literal(item['source_record'])}；分块：{literal(_list_text(item['chunks']))}", ""]
                for evidence in item["evidence"]:
                    lines.extend(_evidence_lines(evidence, secrets))
                for issue in item["issues"]:
                    if issue.get("severity") != "info":
                        lines.append(f"- 需复核：{literal(_issue_text(issue))}")
                for check in item.get("failed_checks", []):
                    lines.append(f"- 未通过检查：{literal(_issue_text(check))}")
                if item.get("relations"):
                    relations = "；".join(f"{name}={relation}" for name, relation in item["relations"].items())
                    lines.append(f"- 模型声明的表格关系：{literal(relations)}；语义未核验。")
                lines.extend(_scope_evidence_lines(item.get("scope_sources", []), secrets))
                for passage in item.get("context", []):
                    lines.append(f"- 原上下文（{literal(passage['role'])}）：{literal(passage['quote'])}；{literal(_location(passage))}")
                lines.append("")
    if review.get("associations"):
        labels = {"possible_repeat": "可能重复", "possible_conflict": "可能冲突", "insufficient_context": "关联依据不足"}
        lines += ["## 候选关联线索", "", "以下是原结果已有线索，不合并、删除或选定候选。", ""]
        for link in review["associations"]:
            lines.append(f"- {literal(labels.get(link['kind'], link['kind']))}：{literal(_list_text(link['candidate_ids']))}；"
                         f"比较字段：{literal(_list_text(link['compared_fields']))}；"
                         f"不同字段：{literal(_list_text(link['differing_fields']))}；"
                         f"尚未明确：{literal(_list_text(link['unresolved_fields']))}")
        lines.append("")
    lines += ["## 格式隔离记录", "", "以下只展示未通过格式检查的模型声明，不能作为已通过的观测。", ""]
    for item in review["quarantined"]:
        lines += [f"### {literal(item['table_id'])} · 原响应下标 {literal(item['response_index'])}", "",
                  f"字段：{literal(item['record_type'])}；对象：{literal(item.get('entity'))}；"
                  f"原值：{literal(item.get('value', item.get('fields')))}；单位：{literal(item.get('unit'))}",
                  f"条件：{literal(item.get('conditions'))}；范围声明：{literal(_scope_text(item.get('scope')))}。",
                  f"原记录引用：{literal(item['source_record'])}"]
        for issue in item["issues"]:
            lines.append(f"- 隔离原因：{literal(_issue_text(issue))}")
        for evidence in item["evidence"]:
            lines.extend(_evidence_lines(evidence, secrets, unvalidated=True))
        lines.extend(_scope_evidence_lines(item.get("scope_sources", []), secrets, unvalidated=True))
        if "unparsed_record" in item:
            lines.append(f"- 未解析声明：{literal(item['unparsed_record'])}")
        lines.append("")
    if not review["quarantined"]:
        lines += ["无格式隔离记录。", ""]
    lines += ["## 没有保留有值候选的目标", "", literal(_list_text(review["targets_without_retained_value"])), "",
              "这不是原文未报告的结论；请同时查看隔离记录，并回到原文检查遗漏。", ""]
    return "\n".join(lines)


def save_expert_review(result: ExtractionResult, directory: Path, *, secrets: Iterable[str] = (),
                       source_result: str = "result.json") -> None:
    """Write additional view files exclusively; the caller validates the directory."""
    secrets = tuple(secrets)
    review = build_expert_review(result)
    review["source_result"] = source_result
    payload = RunTrace(None, secrets=secrets)._mask(review)
    markdown = render_expert_review(review, secrets=secrets)
    with (directory / "review.json").open("x", encoding="utf-8", newline="") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with (directory / "review.md").open("x", encoding="utf-8", newline="") as stream:
        stream.write(markdown)
    from .annotation_view import save_annotation
    save_annotation(result, directory, secrets=secrets, source_result=source_result)

"""Human-facing values and evidence, with technical provenance kept separately."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
import json
from pathlib import Path

from .diagnostics import RunTrace
from .evidence import is_missing
from .expert_review import build_expert_review, _literal, _scope_text
from .models import ExtractionResult


_VERIFICATION = "未人工核实；程序检查不能证明抽取正确"
_ISSUE_LABELS = {
    "required_value_missing": "必填值缺失", "missing_value": "字段值缺失",
    "missing_field": "缺少字段", "unknown_field": "出现未要求的字段",
    "field_source_missing": "缺少字段原文依据", "field_type_mismatch": "字段类型不符",
    "relation_geometry_mismatch": "声明的表格关系与行列位置不符",
    "literal_source_not_unique": "原文依据不能唯一确定",
    "mixed_measurement_conditions": "同一观测混入不同条件",
    "scope_evidence_missing": "缺少适用范围依据",
    "group_identity_narrowed": "整体结果可能被归给局部对象",
}
_LINK_LABELS = {"possible_repeat": "可能重复", "possible_conflict": "可能冲突",
                "insufficient_context": "关联依据不足"}


def _issue_label(issue: dict) -> str:
    code = issue.get("code", issue.get("type", ""))
    detail = issue.get("message") or issue.get("msg") or _ISSUE_LABELS.get(code) or str(code)
    field = issue.get("field")
    return f"{field}：{detail}" if field else str(detail)


def _issues(item: dict, associations: list[dict]) -> list[str]:
    warnings = [_issue_label(issue) for issue in item.get("issues", [])
                if issue.get("severity") != "info"]
    warnings.extend(_issue_label(check) for check in item.get("failed_checks", []))
    for link in associations:
        if item["candidate_id"] not in link["candidate_ids"]:
            continue
        warning = _LINK_LABELS.get(link["kind"], link["kind"]) + "：与其他观测的关联尚待核对"
        if link.get("unresolved_fields"):
            warning += "；尚未明确 " + "、".join(link["unresolved_fields"])
        warnings.append(warning)
    return list(dict.fromkeys(warnings))


def _observation(item: dict, associations: list[dict]) -> dict:
    quotes = []
    for entry in item["evidence"]:
        quote = entry.get("quote")
        if isinstance(quote, str) and quote not in quotes:
            quotes.append(quote)
    scope_sources = item.get("scope_sources")
    if isinstance(scope_sources, list):
        for entry in scope_sources:
            quote = entry.get("quote") if isinstance(entry, dict) else None
            if isinstance(quote, str) and quote not in quotes:
                quotes.append(quote)
    observation = {
        "value": deepcopy(item.get("value", item.get("fields"))),
        "unit": deepcopy(item.get("unit")), "conditions": deepcopy(item.get("conditions")),
        "evidence": quotes,
        "source": "表格 " + item["table_id"] if item["source_kind"] == "table" else "正文",
    }
    if item.get("scope") is not None:
        observation["scope"] = deepcopy(item["scope"])
    warnings = _issues(item, associations)
    if warnings:
        observation["issues"] = warnings
    return observation


def _pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _provenance(item: dict, pointer: str, review_row: str | None) -> dict:
    return {"annotation_pointer": pointer, "source_record": item["source_record"],
            "candidate_id": item["candidate_id"], "source_kind": item["source_kind"],
            "review_row": review_row, "scope": deepcopy(item.get("scope")),
            "evidence": deepcopy(item["evidence"])}


def _build(result: ExtractionResult) -> tuple[dict, list[dict]]:
    review = build_expert_review(result)
    simple = result.task.input_materials is not None
    records, groups, sources = [], {}, []
    for row in review["rows"]:
        for field, observations in row["fields"].items():
            for item in observations:
                entity = item.get("entity")
                can_group = simple and isinstance(entity, str) and not is_missing(entity)
                record_index = groups.get(entity) if can_group else None
                if record_index is None:
                    record_index = len(records)
                    records.append({"entity": deepcopy(entity), "fields": {}})
                    if can_group:
                        groups[entity] = record_index
                collection = records[record_index]["fields"].setdefault(field, [])
                pointer = f"/records/{record_index}/fields/{_pointer_part(field)}/{len(collection)}"
                collection.append(_observation(item, review["associations"]))
                sources.append(_provenance(item, pointer, row["row_id"]))
    quarantined = []
    for item in review["quarantined"]:
        pointer = f"/quarantined/{len(quarantined)}"
        declaration = {"field": deepcopy(item["record_type"]), "entity": deepcopy(item.get("entity")),
                       **_observation(item, [])}
        if "unparsed_record" in item:
            declaration["unparsed_declaration"] = deepcopy(item["unparsed_record"])
        quarantined.append(declaration)
        sources.append(_provenance(item, pointer, None))
    return {"document": result.source.name, "verification": _VERIFICATION,
            "records": records, "missing_fields": review["targets_without_retained_value"],
            "quarantined": quarantined}, sources


def build_annotation(result: ExtractionResult) -> dict:
    """Show exact object labels together without merging observations or accepting facts."""
    return _build(result)[0]


def _table_row(field: object, item: dict, secrets: Iterable[str], *, rejected: bool = False) -> str:
    def literal(value):
        return _literal(value, secrets) if value is not None else "未报告"

    conditions = literal(item["conditions"])
    if "scope" in item:
        conditions += "<br>范围：" + literal(_scope_text(item["scope"]))
    evidence = "<br>".join(literal(quote) for quote in item["evidence"]) or "无可展示的引文"
    checks = "<br>".join(literal(issue) for issue in item.get("issues", []))
    if rejected:
        checks = "格式未通过；不能采用" + ("<br>" + checks if checks else "")
    elif not checks:
        checks = "未人工核实"
    else:
        checks = "待核对：<br>" + checks
    return "| " + " | ".join((literal(field), literal(item["value"]), literal(item["unit"]),
                                conditions, literal(item["source"]), evidence, checks)) + " |"


def render_annotation(data: dict, *, secrets: Iterable[str] = ()) -> str:
    """Keep value, unit, conditions and complete evidence beside each other."""
    secrets = tuple(secrets)
    lines = ["# 抽取结果", "", "文献：" + _literal(data["document"], secrets),
             "核实状态：" + _literal(data["verification"], secrets), "",
             "每个对象下共同展示各字段；同名对象不等于已经证明是同一个实体。不同条件和多个值各占一行，未做归并。",
             "引文各项是独立原文片段，尤其表格片段不代表行列或对象关联已经核实。完整诊断见 result.json，追踪映射见 annotation.sources.json。", ""]
    header = ["| 字段 | 原值 | 单位 | 条件 | 出处 | 原文依据 | 检查 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for index, record in enumerate(data["records"], 1):
        entity = record["entity"] if not is_missing(record["entity"]) else "对象未报告（独立记录）"
        lines += [f"## {index}. {_literal(entity, secrets)}", "", *header]
        for field, items in record["fields"].items():
            lines.extend(_table_row(field, item, secrets) for item in items)
        lines.append("")
    if not data["records"]:
        lines += ["没有保留的观测；不能据此认定原文没有可抽取信息。", ""]
    lines += ["## 没有保留有值结果的字段", "",
              _literal("、".join(data["missing_fields"]), secrets) if data["missing_fields"] else "无。", "",
              "这不是原文未报告的结论，请结合原文检查遗漏。", ""]
    if data["quarantined"]:
        lines += ["## 格式隔离：以下声明不能作为有效结果", ""]
        for index, item in enumerate(data["quarantined"], 1):
            lines += [f"### 隔离声明 {index} · 对象：{_literal(item['entity'], secrets)}", "", *header,
                      _table_row(item["field"], item, secrets, rejected=True), ""]
            if "unparsed_declaration" in item:
                lines += ["无法解析的原声明：" + _literal(item["unparsed_declaration"], secrets), ""]
    return "\n".join(lines)


def save_annotation(result: ExtractionResult, directory: Path, *, secrets: Iterable[str] = (),
                    source_result: str = "result.json") -> None:
    """Add three view files to a validated directory without altering the original result."""
    secrets = tuple(secrets)
    data, entries = _build(result)
    provenance = {"source_result": source_result, "entries": entries}
    trace = RunTrace(None, secrets=secrets)
    rendered = render_annotation(data, secrets=secrets)
    for name, payload in (("annotation.json", data), ("annotation.sources.json", provenance)):
        with (directory / name).open("x", encoding="utf-8", newline="") as stream:
            json.dump(trace._mask(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    with (directory / "annotation.md").open("x", encoding="utf-8", newline="") as stream:
        stream.write(rendered)

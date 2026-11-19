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


def admission_annotation(dataset: dict) -> tuple[dict, list[dict]]:
    """Keep unconfirmed field mappings out of the named fields of confirmed rows."""
    groups, records, held, sources = {}, [], [], []
    observation_pointers = {}
    for index, p in enumerate(dataset["proposals"]):
        field = p["suggested_field"]
        evidence = [s["quote"] for s in p["evidence"]]
        item = {"value": p["value"], "unit": p["unit"], "conditions": p["conditions"],
                "statistic": p["statistic"], "source_property": p["source_property"],
                "evidence": list(dict.fromkeys(evidence)), "proposal_id": p["proposal_id"],
                "source": "表格 " + (p["table_id"] or p["block_id"]) if p["source_kind"] == "table" else "正文",
                "direct_subject": p["direct_subject"], "target_subject": p["target"].get("label"),
                "fit": p["fit"]["status"], "disposition": p["disposition"], "reason": p["reason"]}
        if p["row_ids"] and p["observation_id"] in observation_pointers:
            pointer = observation_pointers[p["observation_id"]]
        elif p["row_ids"]:
            group = p["target"]["id"]
            if group not in groups:
                groups[group] = len(records)
                records.append({"entity": p["target"]["label"], "subject_id": group, "fields": {}})
            i = groups[group]
            values = records[i]["fields"].setdefault(field, [])
            pointer = f"/records/{i}/fields/{_pointer_part(field)}/{len(values)}"
            observation_pointers[p["observation_id"]] = pointer
            values.append(item)
        else:
            pointer = f"/review_proposals/{len(held)}"
            if "source_subject" in p:
                item.update(source_subject=p["source_subject"], candidate_subjects=[
                    {k: candidate.get(k) for k in ("id", "label", "qualifier")} for candidate in p["candidate_subjects"]])
            held.append({**item, "suggested_field": field, "candidate_subject": p["candidate_subject"],
                         "candidate_basis": p["candidate_basis"], "direct_status": p["direct"]["status"],
                         "target_status": p["target"]["status"]})
        sources.append({"annotation_pointer": pointer, "source_record": f"/proposals/{index}",
                        "proposal_id": p["proposal_id"], "observation_id": p["observation_id"],
                        "source_kind": p["source_kind"], "evidence": p["evidence"]})
    fallback = [{"observation_id": o["observation_id"], "source_record": o["source_record"],
                 "field": o["field"], "value": o["value"], "row_ids": o["row_ids"]}
                for o in dataset["observations"] if not o["proposal_ids"]]
    return {"document": dataset.get("source_name") or dataset["document_id"], "verification": _VERIFICATION, "records": records,
            "review_proposals": held, "quarantined": [],
            "frozen_without_proposal_receipt": fallback,
            "missing_fields": [f for f in dataset["schema"] if not any(not is_missing(r[f]) for r in dataset["rows"])],
            "call_gaps": [c for c in dataset["admission"]["coverage"] if c["status"] == "gap"]}, sources


def _build(result: ExtractionResult) -> tuple[dict, list[dict]]:
    if result.dataset is not None and "admission" in result.dataset:
        return admission_annotation(result.dataset)
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
    if item.get("statistic"):
        conditions += "<br>统计口径：" + literal(item["statistic"])
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
        lines += ["本视图中没有正式字段记录。原文可能仍有相关观测。", ""]
    if "review_proposals" in data:
        lines += ["## 待核与字段不适配的提议", "",
                  "| 原文直接对象 | 模型提名 | 篇内主体 | 原文属性 | 原值 | 单位 | 条件 | 统计口径 | 建议字段 | 字段判断 | 原因 | 出处 | 原文依据 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for p in data["review_proposals"]:
            fit = {"supported": "口径有据", "contradicted": "不适配", "unknown": "待核"}.get(p["fit"], p["fit"])
            direct = p["direct_subject"] or "定位待核"
            if "source_subject" in p and p["direct_status"] != "supported":
                direct = (p["direct_subject"] or p["source_subject"] or "") + "（归属待核）"
            identity = p["target_subject"] if p["target_status"] == "supported" else "身份待核"
            if p.get("candidate_subjects") and p["target_status"] != "supported":
                alternatives = [s["label"] + (f"（{s['qualifier']}）" if s.get("qualifier") else "")
                                for s in p["candidate_subjects"]]
                identity += " · " + " / ".join(alternatives)
            cells = (direct, p["candidate_subject"] or "模型未提名", identity,
                     p["source_property"] or "未定位", p["value"], p["unit"] or "", p["conditions"] or "",
                     p["statistic"] or "", p["suggested_field"], fit, p["reason"], p["source"])
            evidence = "<br>".join(_literal(q, secrets) for q in p["evidence"]) or "未定位"
            lines.append("| " + " | ".join([*(_literal(v, secrets) for v in cells), evidence]) + " |")
        lines += ["", f"缺少可审计完整回答的调用数为 {len(data['call_gaps'])}。详见 dataset.json。", ""]
        fallback = data["frozen_without_proposal_receipt"]
        if fallback:
            lines += [f"另有 {len(fallback)} 条冻结观测缺少可对照的提议。其原值和主表去向已列入 "
                      "annotation.json 的 frozen_without_proposal_receipt。", ""]
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
    provenance = {"source_result": str(Path(source_result).parent / "dataset" / "dataset.json")
                  if "review_proposals" in data else source_result,
                  "entries": entries}
    trace = RunTrace(None, secrets=secrets)
    rendered = render_annotation(data, secrets=secrets)
    for name, payload in (("annotation.json", data), ("annotation.sources.json", provenance)):
        with (directory / name).open("x", encoding="utf-8", newline="") as stream:
            json.dump(trace._mask(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    with (directory / "annotation.md").open("x", encoding="utf-8", newline="") as stream:
        stream.write(rendered)

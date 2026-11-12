"""Render extraction candidates for local human review without changing results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from html import escape
import json
import re
import string

from .diagnostics import redact_text
from .models import EvidenceRef, ExtractionResult
from .evidence import is_missing


def _cell(value: str, secrets: Iterable[str]) -> str:
    """Keep source-derived text inside one Markdown cell or paragraph."""
    text = redact_text(value, secrets).replace("\r\n", "\n").replace("\r", "\n")
    if "$" in text or any(delimiter in text for delimiter in (r"\(", r"\[", r"\begin{")):
        # Math-aware Markdown viewers skip code elements. Encoding punctuation
        # also prevents Markdown, table separators, or source HTML inside them.
        literal = "".join(f"&#{ord(char)};" if char in string.punctuation else char for char in text)
        return "<code>" + literal.replace("\n", "<br>") + "</code>"
    text = escape(text, quote=False)
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)
    return text.replace("\n", "<br>")


def _location(evidence: EvidenceRef) -> str:
    if evidence.status in {"matched", "normalized"} and evidence.start is not None and evidence.end is not None:
        return f"[{evidence.start}, {evidence.end})"
    if evidence.source_matches:
        locations = "; ".join(f"[{match.span.start}, {match.span.end})" for match in evidence.source_matches[:5])
        if len(evidence.source_matches) > 5:
            locations += f"；共 {len(evidence.source_matches)} 处（完整位置见 JSON）"
        return "多个规范化检索位置：" + locations
    if evidence.matches:
        locations = "; ".join(f"[{span.start}, {span.end})" for span in evidence.matches[:5])
        if len(evidence.matches) > 5:
            locations += f"；共 {len(evidence.matches)} 处（完整位置见 JSON）"
        return "多个匹配：" + locations
    return "未定位"


def render_markdown_report(result: ExtractionResult, *, secrets: Iterable[str] = ()) -> str:
    """Return a readable view; exact values and evidence remain in the JSON result.

    Locations are half-open Unicode character offsets in the input document.
    A matched quote establishes location only; this report accepts no facts.
    """
    secrets = tuple(secrets)
    if result.run.routes:
        from .automatic_provenance import validate_automatic_provenance
        validate_automatic_provenance(result)

    def cell(value: str) -> str:
        return _cell(value, secrets)

    def evidence_details(label: str, evidence: EvidenceRef) -> list[str]:
        details = []
        if evidence.source_matches:
            details += [f"- {cell(label)}：规范化检索仅提供待复核位置；模型引文未改写。"]
            for match in evidence.source_matches[:5]:
                details.append(f"- 原文精确片段：{cell(match.source_quote)}；位置 "
                               f"[{match.span.start}, {match.span.end})；规则：{cell(', '.join(match.rules))}")
        for reference in evidence.table_refs:
            details.append(f"- {cell(label)}单元格：{cell(reference.table_id)} / {cell(reference.cell_id)}；"
                           f"行 {reference.row + 1}，列 {reference.column + 1}；"
                           f"跨 {reference.rowspan} 行、{reference.colspan} 列；角色：{cell(reference.role)}")
        return details

    pending = sum(candidate.needs_review for candidate in result.candidates)
    blocking = sum(item.severity == "review" for item in result.review)
    lines = [
        "# 抽取候选审阅", "",
        "本报告展示抽取候选。结构或引文定位检查通过不代表事实已核验，也不代表已接受为正式结果。", "",
        f"- 文献：{cell(result.source.name or '未命名输入')}",
        f"- 任务：{cell(result.task.name)}",
        f"- 程序版本：{cell(result.package_version)}；结果格式：{cell(result.schema_version)}",
        f"- 模型：{cell('按分块记录，见实际调用来源' if result.run.routes else result.run.model or '未报告')}；模式：{cell(result.run.mode)}",
        f"- 正文模型回答协议：{cell(result.run.output_protocol)}" if result.table_results else f"- 模型回答协议：{cell(result.run.output_protocol)}",
        f"- 分块：{result.run.chunk_count}；模型响应：{result.run.response_count}；HTTP/后端尝试：{result.run.request_attempts}",
        f"- 候选：{len(result.candidates)}；待复核候选：{pending}；复核项：{len(result.review)}",
        f"- 需复核问题：{blocking}；信息提示：{len(result.review) - blocking}（信息提示本身不阻止候选通过程序检查）",
        f"- 输入字符数：{result.source.character_count}；SHA-256：{cell(result.source.sha256)}", "",
        "位置使用原始输入的 Unicode 字符偏移，采用左闭右开区间 `[start, end)`；片段输入的位置相对于片段。",
        "字段表中的位置覆盖整条引文；测量证据表另列数值和单位片段的位置。matched 仅代表原文定位成功，不代表字段归属或计算情景已经核验。", "",
        "normalized 表示通过保守规范化找到了原文位置，仍须复核；不代表引文逐字匹配或事实已核验。", "",
        "字段状态：reported＝已报告；not_reported＝未报告；not_applicable＝任务声明不适用；required_missing＝必要信息缺失。", "",
        "关联检查仅覆盖任务配置的相同比较值，尚未实现完整的不同值矛盾检测；没有关联不表示没有冲突。", "",
    ]
    if result.table_results:
        lines += [f"- 表格：{len(result.table_results)}；表格记录：{sum(len(t.candidates) for t in result.table_results)}；"
                  f"隔离记录：{sum(len(t.rejections) for t in result.table_results)}；格式状态：{cell(result.format_status)}", "",
                  "正文候选与表格记录分别保存。表格交付格式 table-route-v3；字段、引文和顺序原样交付，不经过正文去重。", ""]
    if result.task.input_materials is not None:
        observations = [(item.record_type, item.fields.get("value").value if "value" in item.fields else None)
                        for item in result.candidates]
        observations.extend((item.get("record_type"), item.get("fields", {}).get("value", {}).get("value"))
                            for table in result.table_results for item in table.candidates)
        present = Counter(name for name, value in observations if not is_missing(value))
        missing = Counter(name for name, value in observations if is_missing(value))
        lines += ["## 本次抽取范围", "",
                  "完整清单决定目标，示例不会缩小范围。有值候选仅表示 value 非空，不代表事实、对象或条件已核验。",
                  "缺值记录保留供复核，不计入有值候选；零条候选也不能证明原文没有报告该信息。", "",
                  "| 目标字段 | 定义 | 有值候选 | 缺值候选 |",
                  "| --- | --- | ---: | ---: |"]
        for name, spec in result.task.record_types.items():
            lines.append(f"| {cell(name)} | {cell(spec.description)} | {present[name]} | {missing[name]} |")
        lines.append("")
    if result.run.routes:
        lines += ["## 实际调用来源", "",
                  "以下记录实际使用的路径，不代表模型返回的事实已核验。失败或结果未知的请求也占用调用额度。", "",
                  "| 分块 | 路径 | 请求模型 | 服务报告模型 | 接口 | 输出约束 | 成功尝试 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for chunk_id, origin in result.run.chunk_provenance.items():
            route = result.run.routes[origin.route_id]
            config = route["config"]
            cells = (chunk_id, origin.route_id, config["model"], origin.reported_model or "未报告",
                     config["adapter"], config["structured_output"], origin.attempt_id)
            lines.append("| " + " | ".join(cell(value) for value in cells) + " |")
        unknown = sum(item.outcome_unknown for item in result.run.generation_attempts)
        lines += ["", f"- 已预留生成尝试：{len(result.run.generation_attempts)}；结果未知：{unknown}。未知用量不能视为零费用。", "",
                  "| 尝试 | 分块 | 路径 | 状态 | 错误代码 | HTTP 尝试 |",
                  "| --- | --- | --- | --- | --- | ---: |"]
        for attempt in result.run.generation_attempts:
            cells = (attempt.attempt_id, attempt.chunk_id, attempt.route_id, attempt.status,
                     attempt.error_code or "无", str(attempt.http_attempts))
            lines.append("| " + " | ".join(cell(value) for value in cells) + " |")
        lines.append("")
    if result.run.usage:
        lines += ["## 模型用量", "", "| 指标 | 数量 |", "| --- | ---: |"]
        lines += [f"| {cell(name)} | {count} |" for name, count in sorted(result.run.usage.items())]
        lines.append("")
    counts = Counter((item.severity, item.code) for item in result.review)
    if counts:
        lines += ["## 复核问题汇总", "", "| 级别 | 问题代码 | 次数 |", "| --- | --- | ---: |"]
        lines += [f"| {'需复核' if severity == 'review' else '信息'} | {cell(code)} | {count} |"
                  for (severity, code), count in sorted(counts.items())]
        lines.append("")
    if result.links:
        ordinals = {candidate.candidate_id: index for index, candidate in enumerate(result.candidates, 1)}
        labels = {"possible_repeat": "可能重复表述", "possible_conflict": "可能冲突", "insufficient_context": "关联依据不足"}
        lines += ["## 候选之间的待确认关联", "",
                  "以下仅是程序发现的待确认线索，没有合并、删除或选定任何候选。数值相同不能单独证明属于同一观测。", "",
                  "| 关联类型 | 候选序号 | 比较字段 | 不同字段 | 尚未明确的字段 |",
                  "| --- | --- | --- | --- | --- |"]
        for link in result.links:
            cells = (labels[link.kind], ", ".join(str(ordinals.get(identifier, identifier)) for identifier in link.candidate_ids),
                     ", ".join(link.compared_fields), ", ".join(link.differing_fields), ", ".join(link.unresolved_fields))
            lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
        lines.append("")
    if not result.candidates and not result.table_results:
        lines += ["没有生成候选；这不能证明原文没有可抽取信息。", ""]
    for index, candidate in enumerate(result.candidates, 1):
        state = "待复核" if candidate.needs_review else "结构与证据定位检查通过（仍为候选）"
        lines += [
            f"## 候选 {index}：{cell(candidate.record_type)}", "",
            f"- ID：{cell(candidate.candidate_id)}",
            f"- 状态：{state}",
            f"- 来源分块：{cell(', '.join(candidate.chunk_ids))}", "",
            "| 字段 | 值（JSON 标量） | 原文证据 | 证据状态 | 引文位置 | 字段状态 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for name, field in candidate.fields.items():
            value = json.dumps(field.value, ensure_ascii=False, allow_nan=False)
            cells = (name, value, field.evidence.quote, field.evidence.status, _location(field.evidence),
                     candidate.field_status.get(name, "未记录（旧结果）"))
            lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
        lines.append("")
        for name, field in candidate.fields.items():
            lines.extend(evidence_details(name, field.evidence))
        if candidate.source_context:
            roles = {"heading": "章节标题", "table_header": "列标题行", "table_row_header": "行标题",
                     "table_caption": "表题", "table_footnote": "表注",
                     "neighbor_before": "前邻段", "neighbor_after": "后邻段"}
            lines += ["", "### 实际提供的上下文", "",
                      "以下片段曾提供给模型；它们用于人工核对，不代表对象、条件或单位归属已核验。", "",
                      "| 角色 | 原文片段 | 原文位置 |", "| --- | --- | --- |"]
            for passage in candidate.source_context:
                cells = (roles[passage.role], passage.quote, f"[{passage.span.start}, {passage.span.end})")
                lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
            lines.append("")
        for name, status in candidate.field_status.items():
            if name not in candidate.fields:
                lines.append(f"- 未提供字段：{cell(name)}；状态：{cell(status)}")
        for name, measurement in candidate.measurements.items():
            lines += [f"### 测量证据组：{cell(name)}", "",
                      "语义状态：未核验。以下检查不证明参数、数值、单位及条件在语义上属于同一观测。",
                      f"- 关联条件字段：{cell(', '.join(measurement.condition_fields) or '无')}",
                      f"- 单位来源：{'表头' if measurement.unit_source == 'table_header' else '测量上下文'}", "",
                      "| 片段角色 | 原文 | 定位状态 | 字符位置 |", "| --- | --- | --- | --- |"]
            fragments = [("测量上下文（整条引文）", measurement.context),
                         ("数值片段", measurement.value), ("单位片段", measurement.unit)]
            if measurement.unit_context is not None:
                fragments.append(("表头上下文（不代表列关联通过）", measurement.unit_context))
            for role, evidence in fragments:
                cells = (role, evidence.quote, evidence.status, _location(evidence))
                lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
            lines.append("")
            for role, evidence in fragments:
                lines.extend(evidence_details(role, evidence))
            if measurement.range_binding is not None:
                binding = measurement.range_binding
                layouts = {"shared": "范围共享单位", "per_endpoint": "两个端点分别绑定单位",
                           "table_header": "范围使用同列表头单位"}
                lines += [f"- 范围关系：{layouts[binding.unit_layout]}（程序语法检查，不是事实确认）", "",
                          "| 范围组成 | 原文 | 字符位置 |", "| --- | --- | --- |"]
                components = [("端点 1", binding.endpoints[0]), ("连接符", binding.separator),
                              ("端点 2", binding.endpoints[1])]
                components.extend((f"单位 {index}", unit) for index, unit in enumerate(binding.units, 1))
                for role, evidence in components:
                    lines.append("| " + " | ".join(cell(item) for item in
                                                  (role, evidence.quote, _location(evidence))) + " |")
                if measurement.unit.status == "ambiguous":
                    lines.append("")
                    lines.append("单位字符串存在多个原文位置；上述绑定说明它们与范围端点的关系，未合并或猜选位置。")
                lines.append("")
            for code in measurement.issues:
                lines.append(f"- 测量检查问题：{cell(code)}")
        for check in candidate.checks:
            if not check.passed:
                field = f"（字段：{cell(check.field)}）" if check.field is not None else ""
                lines.append(f"- 未通过检查：{cell(check.code)}{field}")
        for item in result.review:
            if item.candidate_id == candidate.candidate_id:
                field = f"（字段：{cell(item.field)}）" if item.field is not None else ""
                label = "复核" if item.severity == "review" else "信息"
                lines.append(f"- {label}：{cell(item.code)}{field}：{cell(item.message)}")
        lines.append("")
    if result.table_results:
        lines.extend(_table_result_lines(result, cell))
    return "\n".join(lines)


def _table_result_lines(result: ExtractionResult, cell) -> list[str]:
    from .table_reporting import table_check_label
    lines = ["## 表格抽取结果", "",
             "定位成功说明引用在原文中；字面检查核对数字或单位是否完整。局部位置规则只检查同行、同列等关系，"
             "不证明对象、共享单位或累计成员的科学归属。科学核验未运行。", ""]
    for table in result.table_results:
        lines += [f"### {cell(table.table_id)}", "", f"- 运行分块：{cell(table.chunk_id)}；检查状态：{cell(table.status)}",
                  f"- 合法记录：{len(table.candidates)}；隔离记录：{len(table.rejections)}。", ""]
        for record in table.candidates:
            scope = record.get("scope_check", {})
            lines += [f"#### 记录 {cell(record['record_id'])}", "",
                      f"- 模型范围声明：{cell(record['scope'])}；范围引文：{cell(table_check_label(scope.get('scope_quote_location')))}",
                      "- 成员逐项出处：" + cell(table_check_label(scope.get("member_evidence_check"), check="member_evidence_check")),
                      "- 科学核验：未运行。", "",
                      "| 字段 | 模型原值 | 模型原引文 | 引文定位 | 数字/单位字面检查 | 局部位置规则 |",
                      "| --- | --- | --- | --- | --- | --- |"]
            for name, binding in record["fields"].items():
                checks = record["field_checks"][name]
                quotes = "; ".join(s["ref"] + ": " + s["quote"] for s in binding["sources"])
                values = (name, json.dumps(binding["value"], ensure_ascii=False), quotes,
                          table_check_label(checks["quote_location"]), table_check_label(checks["literal_check"]),
                          table_check_label(checks["relation_check"]))
                lines.append("| " + " | ".join(cell(value) for value in values) + " |")
            lines.append("")
            for issue in record["issues"]:
                lines.append(f"- 检查意见：{cell(issue.get('field') or '记录')}：{cell(issue['code'])}")
            lines.append("")
        if table.rejections:
            lines += ["隔离记录（原数组下标从0开始；原记录保存在JSON中）：", ""]
            for rejection in table.rejections:
                lines.append(f"- 下标 {rejection['index']}：{cell(json.dumps(rejection['errors'], ensure_ascii=False))}")
            lines.append("")
        lines += ["#### 表格原文索引", "", "以下是模型实际收到的原格、标题、表题和脚注；位置相对完整输入论文。", "",
                  "| 编号 | 类型 | 原文位置 | 原文 |", "| --- | --- | --- | --- |"]
        for entry in table.evidence["table_input"]["entries"]:
            values = (entry["ref"], entry["kind"], f"[{entry['start']}, {entry['end']})", entry["quote"])
            lines.append("| " + " | ".join(cell(value) for value in values) + " |")
        lines.append("")
    return lines

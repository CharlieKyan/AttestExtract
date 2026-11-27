"""Readable local inventories; these views never apply decision suggestions."""

from __future__ import annotations

from .decision_bundle import DecisionBundle, decision_bundle_summary
from .decision_ledger import DecisionLedger, decision_ledger_summary
from .reporting import _cell


def _text(value: object) -> str:
    return _cell(str(value), ())


def render_decision_bundle(bundle: DecisionBundle) -> str:
    """Reference shared source and candidate snapshots instead of duplicating them."""
    summary = decision_bundle_summary(bundle)
    ordinals = {candidate.candidate_id: index for index, candidate in enumerate(bundle.extraction.candidates, 1)}
    lines = [
        "# 离线决策材料清单", "",
        "这是待判断材料，不是 Jev 输出、专家标签或正式接受结果。原文与候选各保存一份，位于同目录 decision-input.json。", "",
        f"- 材料包：{_text(bundle.bundle_id)}；版本：0.1；分析程序：{_text(bundle.analysis_package_version)}",
        f"- 分块材料组：{summary['batch_count']}；候选：{summary['candidate_count']}；合法空块：{summary['empty_chunk_count']}",
        f"- 原有待复核候选：{summary['review_candidate_count']}；未触发实质复核：{summary['unflagged_candidate_count']}",
        f"- 候选判断任务：{summary['candidate_task_count']}；总任务：{summary['task_count']}；本轮模型调用：0", "",
        "document 保存原文及校验值；extraction 保存原始任务、候选、检查、引文全部匹配位置及旧关联。",
        "每组按 candidate_ids 引用候选；按候选 ID 可查 extraction.review、extraction.links 和 analysis_links。",
        "segments 使用 Unicode 左闭右开位置。空块只表示该块成功返回空列表，不能证明没有可抽取信息。",
        "材料组数不等于未来模型调用数；同组多条候选可分别建议保留。", "",
        "## 分块覆盖", "",
        "| 分块 | 原文位置 | 候选序号 | 需复核问题数 | 状态 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for batch in bundle.batches:
        positions = "; ".join(f"[{span.start}, {span.end})" for span in batch.segments)
        numbers = ", ".join(str(ordinals[identifier]) for identifier in batch.candidate_ids)
        reviews = sum(item.candidate_id in batch.candidate_ids and item.severity == "review" for item in bundle.extraction.review)
        cells = (batch.chunk_id, positions, numbers or "无", reviews, "有候选" if numbers else "合法空块，需覆盖核查")
        lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
    if bundle.extraction.schema_version == "0.5":
        run = bundle.extraction.run
        lines += ["", "## 实际模型来源", "",
                  "每组对应实际完成抽取的请求；候选通过 chunk_ids 关联此处来源。配置与原始网络日志未复制到材料包。", "",
                  "| 分块 | 请求模型 | 服务报告模型 | 接口 | 输出约束 | 成功尝试 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for batch in bundle.batches:
            origin = run.chunk_provenance[batch.chunk_id]
            config = run.routes[origin.route_id]["config"]
            cells = (batch.chunk_id, config["model"], origin.reported_model or "未报告",
                     config["adapter"], config["structured_output"], origin.attempt_id)
            lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
    lines += ["", "## 逻辑判断任务", "", "| 任务 ID | 材料组 | 候选序号 | 允许动作 |", "| --- | --- | --- | --- |"]
    for task in bundle.tasks:
        cells = (task.task_id, task.batch_id, ", ".join(str(ordinals[x]) for x in task.candidate_ids) or "空块", ", ".join(task.allowed_actions))
        lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
    labels = {"possible_repeat": "可能重复", "possible_conflict": "可能冲突", "insufficient_context": "关联依据不足"}
    original = {tuple(sorted(link.candidate_ids)): link for link in bundle.extraction.links}
    current = {tuple(sorted(link.candidate_ids)): link for link in bundle.analysis_links}
    lines += ["", "## 关联分级对照", "",
              "旧结果保留在 extraction.links，本轮分析单列 analysis_links。没有自动合并、删除或选定候选。",
              "比较仅覆盖任务配置的相同比较值；没有关联不表示没有冲突。", "",
              "| 候选序号 | 历史分级 | 本轮分析 | 不同字段 | 尚未明确的字段 |", "| --- | --- | --- | --- | --- |"]
    for key in sorted(original.keys() | current.keys()):
        old, new = original.get(key), current.get(key)
        link = new or old
        cells = (", ".join(str(ordinals[x]) for x in key), labels[old.kind] if old else "无",
                 labels[new.kind] if new else "无", ", ".join(link.differing_fields) or "无",
                 ", ".join(link.unresolved_fields) or "无")
        lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
    if not original and not current:
        lines += ["", "未产生关联线索。"]
    return "\n".join(lines) + "\n"


def render_decision_ledger(ledger: DecisionLedger) -> str:
    summary = decision_ledger_summary(ledger)
    labels = {"suggest_keep": "建议保留", "suggest_reject": "建议不采用", "needs_review": "待核查"}
    lines = [
        "# 模拟回传校验记录", "",
        "这里只验证接口与引用合法性，不验证判断准确性，也不应用判断。原候选、复核项和正式记录不改变。", "",
        f"- 材料包：{_text(ledger.bundle_id)}；状态：{ledger.status}",
        f"- 合法回传：{summary['valid_count']}；非法回传：{summary['invalid_count']}；未完成任务：{summary['missing_count']}",
        f"- 仍有阻断的选择建议：{summary['blocked_selection_count']}；新建正式记录：0；本轮模型调用：0", "",
        "select 只表示建议保留；必填缺失、证据与现有复核问题不会被这一动作消除。",
        "缺少合法回传的任务保持未完成，不自动补造 review 或其他判断。", "",
        "| 回传序号 | 已知任务 | 校验 | 建议 | 错误代码 | 保留的阻断 |", "| ---: | --- | --- | --- | --- | --- |",
    ]
    for entry in ledger.entries:
        blockers = "; ".join(item.code + (":" + item.field if item.field else "") for item in entry.blocking_reasons)
        if not blockers:
            blockers = "未评估（回传非法）" if entry.status == "invalid" else "无已知机械阻断（非事实核验）"
        cells = (entry.response_index + 1, entry.task_id or "未知", entry.status,
                 labels.get(entry.recommendation, "无"), entry.code or "无", blockers)
        lines.append("| " + " | ".join(_text(cell) for cell in cells) + " |")
    if ledger.missing_task_ids:
        lines += ["", "## 未完成任务", ""]
        lines += ["- " + _text(identifier) for identifier in ledger.missing_task_ids]
    return "\n".join(lines) + "\n"

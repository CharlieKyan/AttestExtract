# 离线决策材料与模拟回传（dev18）

dev19 兼容说明：决策材料继续使用 bundle 0.1，同时支持结果 schema 0.3/0.4。
0.4 批次另存主体与辅助上下文范围，检查引文位于实际展示的独立片段，保留候选上下文出处。
历史 0.3 材料按原序列化计算身份，不追加默认字段改变已有 bundle ID。

本轮实现的是 D5 关联分级局部修复和 D6 决策接口准备。没有 Jev 推理、训练、额外模型调用或自动裁决。抽取仍使用 `flat-v1`、结果 schema `0.3`，原有抽取命令与必填参数不变。

## 1. 准备材料

PowerShell 示例使用自选的成功运行目录；`--output` 可以省略，默认创建新的同级目录。无需服务地址、密钥或 `.env`。

```powershell
python -m attestextract prepare-decisions --run "C:\Work\research\outputs\successful-run"
```

终端返回 `output_directory` 和覆盖统计，目录内保存：

| 文件 | 内容 |
| --- | --- |
| `decision-input.json` | `decision_bundle_version: "0.1"`；文档原文与身份、原始 extraction 快照、最终叶块、任务及本轮关联分析 |
| `decision-report.md` | 全部分块和任务清单、空块、旧关联与新分析对照 |

JSON 中的原文位于 `document.text`，原文校验值在文档及 `extraction.source`；任务要求位于 `extraction.task`。所有候选、完整引文、已有匹配位置、测量证据、检查和旧关联保留在 `extraction`，不改写历史结果。

`batches` 按块保存 `segments`、`candidate_ids`、`task_ids`，不再复制原文或候选。按候选 ID 从 `extraction.review`、`extraction.links` 及 `analysis_links` 查相关问题和关系。原文位置均为原始 Unicode 字符左闭右开区间；Markdown 表头复用可能使不同块的区间重叠。

每条候选只有一个 `candidate_review` 任务，允许 `select/reject/review`；同一块多条候选可以分别选择。重复引用多个块的候选由第一个块拥有逻辑任务，其他块继续引用该候选。合法空块有无候选的 `empty_chunk_review` 任务，只允许 `review`。空块不代表没有可抽取信息，未报警候选也不代表正确。材料组数、任务数均不等于未来真实推理调用数。

首版只支持成功且完整的诊断 schema 0.3、结果 schema 0.3、检查点版本 2。接受运行目录或其 `diagnostics` 目录；原有旧档案读取能力不变，但不是所有旧运行均可用于这个导出入口。

导出检查成功状态、文档校验值、任务/结果一致性、检查点 checksum 与树结构、最终叶块完成状态、块文字/原文位置、候选和证据引用。恢复与细分运行只用当前目录中自包含的最终叶块，不读取历史路径，不重复导出细分父块。缺文件、失败运行、不支持的版本、位置或引用损坏会明确失败。

只读取约定的源文件与定位信息，不复制 `.env`、backend 配置、请求头、网络日志或原始模型响应。材料包仍包含私有论文和候选，应存放在私有目录；白名单并不意味着研究内容已匿名化。哈希用于发现内容不一致，不是来源身份认证或防恶意签名。

## 2. 校验模拟回传

先从自己的 `decision-input.json` 取真实 `bundle_id`、`task_id` 和 `candidate_id`。响应文件结构如下，示例 ID 仅是占位符：

```json
{
  "bundle_id": "bundle-from-your-file",
  "results": [
    {"task_id": "candidate-task-one", "action": "select", "candidate_id": "candidate-for-that-task"},
    {"task_id": "candidate-task-two", "action": "reject"},
    {"task_id": "empty-chunk-task", "action": "review"}
  ]
}
```

`reject/review` 不得指定非空 `candidate_id`。空块不能 select 或 reject。每个任务至多回传一次；重复回传的所有条目均拒绝，不采用“最后一个覆盖前面”的规则。

```powershell
python -m attestextract check-decisions `
  --bundle "C:\Work\research\outputs\materials\decision-input.json" `
  --responses "C:\Work\research\responses.json"
```

默认在材料目录的同级创建新目录，保存 `decision-ledger.json` 和 `decision-report.md`。可指定 `--output` 为新目录，不能覆盖已有目录或写进输入材料/运行中。

| 结果 | 行为与退出码 |
| --- | --- |
| 所有任务均有合法回传 | `status: valid`，退出 0；只说明接口合法 |
| 有缺少的合法回传，没有非法条目 | `status: incomplete`，退出 2；列出未完成任务，不补造答案 |
| 存在未知任务、跨任务候选、重复回传、非法动作或不合规条目 | `status: invalid`，退出 2；记录安全错误代码和未完成任务 |
| JSON 语法、包 ID、顶层结构或输入材料损坏 | 退出 1；明确错误，不生成伪成功记录 |

合法 select 仅记“建议保留”，reject 仅记“建议不采用”，review 记“待核查”。记录中固定 `mode: simulation_replay`、`applied: false`、`created_final_records: 0`。不会自动生成 `CandidateReview(accepted)`，不会删除原候选或清空复核项。

选择建议仍列出已有阻断，包括必填缺失、无法定位的已报告字段、测量证据问题和原有实质复核项。没有机械阻断也不代表语义正确；未来真实 Jev 接入需另定解决这些问题的允许动作，单次选择不能覆盖所有问题。

## 3. 本轮关联规则变化

合成例子：两条候选均为厚度 `100 m`，一条主体为“区域甲”，另一条为“区域甲储层”，后一条条件为空。旧规则优先显示 `possible_conflict`。现在显示 `insufficient_context`，同时保存主体差异与缺失条件；需要确认对象范围和条件，不据此合并。

只有任务声明的身份、条件及比较字段明确一致且相关引文唯一定位，才保留 `possible_repeat` 线索。已明确不同条件的观测继续不因同值建立重复关联。本轮仍只覆盖相同比较值；没有关联不表示没有冲突，未实现完整不同值矛盾检测。

新运行采用新规则。导出旧运行时，`extraction.links` 仍保留历史内容，新分析另存 `analysis_links`，`analysis_package_version` 标明分析程序版本；清单展示差异，不覆写旧文件。

## 4. 软件验收

公开验收只用合成原文和固定 ReplayBackend，不访问真实服务。以下脚本会验证同块两条选择、空块覆盖、重复和缺少回传、选择仍有阻断、源文件不变及正式记录不增加。

```powershell
python scripts/decision_bundle_smoke.py --output-dir outputs/decision-materials-check
```

使用新目录。相关测试还覆盖未知任务、跨任务候选、非法动作、源文/块/检查点损坏、恢复和细分、缺文件与不支持的版本。软件验收不证明决策准确性、召回改善或成本优势；这些需要未来真实 Jev 与人工标签对照。

Python 调用入口：`attestextract.decision_bundle` 中的 `prepare_decision_bundle`、`save_decision_bundle`、`load_decision_bundle`；`attestextract.decision_ledger` 中的 `load_decision_responses`、`check_decisions`、`save_decision_ledger`。它们不会触发模型调用。

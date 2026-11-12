# 正文与表格统一抽取

dev28 起，向普通 `extract` 入口提供论文原文和任务即可使用表格 A 路线。
程序识别完整 Markdown 表格及 Markdown 中的 HTML 表格，正文继续使用正文抽取协议，
每张已识别表格使用 A 协议；最后在同一结果文件和报告中交付。用户无需指定列号、
表格布局或准备实验包。PDF 仍需先在外部转换为 UTF-8 Markdown／文本。

这里的统一是统一运行、预算、保存和展示。表格记录保持模型解析后的字段、类型、
引用、位置、关系、范围和顺序；不会送入正文的候选合并逻辑，也不会用历史记录替换。
正文原有处理行为保持。表格模型提示和响应协议沿用已验证的 A 路线。

## 先查看不联网的运行计划

安装后，在仓库根目录执行以下合成示例。占位地址和模型名仅供本地检查，
`--check` 不调用接口、不验证密钥、端点可用性或模型效果，也不产生抽取结果。

```powershell
attestextract extract `
  --input src/attestextract/examples/procurement.md `
  --task src/attestextract/examples/procurement.task.json `
  --base-url https://example.invalid/v1 --model offline-plan `
  --check
```

终端计划中的 `table_routing.table_count` 表示识别出的表数，`table_chunks` 列出对应任务，
`chunk_count` 包含正文和表格任务；`execution` 显示预算与恢复信息，`network_calls` 为0。
表格按整张处理，`max-chunk-chars` 不会把一张表切成多个 A 请求。
检查也会验证原文覆盖及已有缓存。若识别出的表格没有可用单元格网格，会在模型调用前失败，
需要先检查输入转换质量，程序不会猜一个网格继续抽取。

把 `--input` 和 `--task` 换成自己的文件，即可先查看真实任务的本地计划。
任务说明要写希望抽取的对象、字段和条件；`identity_fields`、`condition_fields`、
`measurement_groups` 表达字段含义，不要求用户描述它们位于哪一列。
`--mineru-content-list` 可选传入与原文对应的 MinerU 信息；原文位置保持不变。

## 正式运行使用同一个命令

以下命令会调用已配置的模型，可能产生费用。本次开发未执行新的真实调用。
先配置好私有 `.env`，并按前面的计划选择请求总预算；此处的8只是示例上限。

```powershell
attestextract extract `
  --input paper.md --task task.json `
  --model-id deepseek:deepseek-flash --env-file .env `
  --max-model-calls 8
```

省略输出路径时，CLI 创建新的运行目录，保存 JSON、Markdown 报告和诊断。
若输入位于 `inputs/`，默认在其同级 `outputs/` 下建立新运行目录。
显式指定 `--output` 时，可同时指定 `--report`、`--log-dir` 保存可读报告和恢复材料。

默认 `--tables auto` 自动分流表格。`--tables text` 是显式兼容选项：让表格继续走旧正文协议，
适用于复放使用旧协议生成的响应；它不是恢复自动改值的表格模式。
`--context` 仍控制正文上下文。A 的表格输入保留结构中可定位的当前章节标题、表题和脚注，
不会因正文上下文选项为 `none` 就丢掉这些表格依据。

Python 同样使用已有入口。下面示例也会调用模型：

```python
from pathlib import Path
from attestextract import ExecutionOptions, RunTrace, extract, load_task

run_dir = Path("private-output/run-01")  # 使用新目录，避免覆盖历史运行
trace = RunTrace(run_dir / "diagnostics")
result = extract(
    Path("paper.md").read_text(encoding="utf-8"),
    load_task("task.json"),
    model="deepseek:deepseek-flash",
    env_file=".env",
    table_mode="auto",                   # 默认值；也接受带结构信息的 Document
    execution=ExecutionOptions(max_model_calls=8),
    trace=trace,
)
(run_dir / "results.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
trace.finish(result.model_dump(mode="json"))
print(result.format_status)
for table in result.table_results:
    print(table.table_id, table.status, len(table.candidates), len(table.rejections))
```

已有 `backend=` 注入及 `model=`／地址配置方式继续使用，不需要再注册一套表格后端。
Python 调用方应检查 `format_status` 和每表 `status`，不能把函数返回当成所有记录格式完全正确。

## 结果保存在哪里，怎样读取

包含表格的结果使用 `schema_version: "0.6"`。没有表格的运行保留原结果版本。

| 结果字段 | 内容 |
| --- | --- |
| `candidates` | 正文候选；不包含表格候选 |
| `table_results` | 按表保存 A 的输出，每表含 `table_id`、`chunk_id`、`status` |
| `table_results[].candidates` | 每条合法表格记录及分项检查，原样保留模型字段 |
| `table_results[].response` / `parsed` | 原始模型响应、逐条解析结果 |
| `table_results[].evidence` | 实际输入的表格、单元格、标题与脚注证据包 |
| `table_results[].issues` / `rejections` | 检查诊断、被隔离记录及其原数组位置和格式错误 |
| `run.candidate_count` | 正文候选数与全部表格合法候选数之和 |
| `format_status` | `complete`、`partial_format_error` 或 `format_error`；不是科学准确率 |

调用方不能再只读取顶层 `candidates` 来代表整篇全部结果。表格的引用、scope及检查信息
与正文协议不同，两个集合分别保留，避免在转换成旧格式时丢失证据。

诊断目录的 `table-routing.json` 记录分流；`tables/<chunk_id>/input.json` 是表格实际输入，
`parsed.json` 是解析结果，`results.json` 保存这一表的候选、证据和检查。
完整请求与原始返回仍在通用 `requests/`、`responses/` 中，恢复状态位于 `execution/checkpoint.json`。
恢复复用的回答保存在 `execution/cache/`，每表结果中的 `response`、`parsed` 也可直接查看；
复用过程不会重新生成一次网络请求，逐次请求/响应文件可追溯到原运行。
这些文件包含原文与模型内容，应保存于私有目录。实验用 HTML 对照报告及冻结参考仍由
[独立实验工具](TABLE_ROUTE_EXPERIMENTS.md)生成，普通入口不需要参考答案。

## 检查通过分别意味着什么

| 报告项目 | 能说明什么 | 不能据此声称什么 |
| --- | --- | --- |
| 原文定位：已定位 | 模型引用能回到原格或已引用上下文中的文字 | 文字已经证明对象与数值的科学关系 |
| 数值／单位字面检查通过 | 在当前规则范围内，原文支持该数值或单位写法及完整性 | 跨列块单位必然适用于此观测 |
| 局部位置规则符合 | 行、列、跨度或上下文类型符合局部规则 | 对象、条件或共享关系已经独立核验 |
| 范围引文定位：已定位 | 群体／累计引用的文字可找到 | 累计成员已列全且每个都有正确出处 |
| 成员逐项出处：未核验 | 尚未逐个验证成员及其出处 | 不能改称“成员依据完整” |
| 科学关系独立核验：未进行 | 本轮没有调用独立核验器或专家 | 不能改称“已接受的科学事实” |

JSON 的 `field_checks.*.relation_check` 保留兼容值 `locally_supported`，
新增 `relation_check_basis: local_position_rules` 明确含义。`scope_check` 分开记录
`declared_scope`、`scope_quote_location` 和 `member_evidence_check`。
当前 `group`／`each` 的成员核验为 `not_run`；`individual` 为按模型声明不适用，
不等于程序已验证这个声明。科学核验始终为 `semantic_verification: not_run`。

例如模型抽取“两个单元累计体积12 L”，并引用“累计体积/L”和数值格。
程序可以定位12和L、核对同行关系，但如果引用没有分别说明两个单元是谁，
成员逐项出处仍未核验。12 L继续交付，不因核验尚未进行而自动删除或改值。

## 预算、格式错误与恢复

正文与表格共用一个请求预算和检查点。已识别的表格按整张调用A，不自动拆表，
也不通过 `--format-retries` 对表格生成第二次回答；该选项仍只影响正文格式重生成。
显式选择的后端兼容切换、传输策略仍受其配置和总预算约束，不能把“一张表”当作费用保证。

一条记录有协议错误，只隔离该条；重复编号涉及的记录全部隔离。合法记录继续保存。
此时整篇 `format_status` 标为部分格式错误；CLI保存结果和报告后返回退出码2，
不能将这个结果当作完全成功。若全部输出记录均被隔离，则为 `format_error`。
JSON损坏、重复键、整体截断等错误仍明确失败，不从残缺响应中猜出记录。
表格的空回答及上述内容错误也不会触发备用模型补答；明确的接口不兼容仍可按已有自动适配配置处理。

中断后按CLI显示的 `--resume` 命令恢复。已完成响应会重新检查而不重新请求；
已预留但没有安全完成记录的表格任务不会自动重发，避免重复扣费。
任务、输入、表格模式及相关配置必须与检查点一致；改变这些条件需新建运行。
旧检查点若使用正文表格协议，应明确使用 `--tables text`，不可当作A响应读取。

目前旧档案接受流程和离线决策包尚未支持 `table_results`：传入含表结果会明确拒绝，
不会静默只处理正文并丢掉表格。Jev尚未接入；未来核验应读取原始表格候选与证据，
将决定另存，不能用位置检查状态冒充核验决定。

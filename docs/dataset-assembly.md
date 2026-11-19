# 从冻结观测生成科研数据表

一项观测包含原值、直接对象、条件和证据位置。例如原文报告“样品 A 的质量为 3.70 g”，正文或表头还可能指明它取自设备 R。要检索 R 的相关观测，可以沿有证据的来源关系找到 A。设备质量一列只能接收直接描述设备的质量值。跨论文统一身份和含多个角色的对照任务超出本版范围。

## 历史运行

保留输入时的 UTF-8 原文和换行。将下列路径替换成自己的文件。命令只读取本地文件，新建一个输出目录。

```sh
attestextract assemble-dataset --input source.md --run-dir run --output-dir rebuilt --dataset-config dataset-options.json
```

默认读取 `run/result.json` 和 `run/analysis/analysis.json`。自定义路径可用 `--result` 和 `--analysis` 指定。文件缺失、文档校验失败或配置无效会返回错误。重建时选另一个输出目录，以便保留可比较的旧版本。

dev48 更新了简单任务的抽取提示与表格补读策略。早期版本尚未完成的简单任务可用原安装版本续跑，也可用 dev48 在新目录重新抽取。已完成的观测优先运行可直接用上述离线命令重新组表。

`--dataset-config` 可省略。框架从冻结任务读取完整字段列表和定义，从已有分析读取主表行的主体描述。以下为一个合成任务的选项文件。

```json
{
  "row_subject": "individual specimens",
  "identity_fields": ["specimen"],
  "subject_label_field": "specimen",
  "field_policies": {"mass": {"allowed_units": ["g"]}}
}
```

`identity_fields` 中的字段必须已存在于任务。只有显式指定的 `subject_label_field` 可以接收有出处的目标名。序列号、站点编码等身份字段需要各自有证据的原值。省略 `identity_fields` 时，`subject_label_field` 可单独作为身份字段。配置多个身份维度时须包含显示名字段（如有）。各列的证据和冲突分别判断。完整明细始终可用于审阅。新指定的行主体会重新约束已有关联。

可用字段约束包括 `allowed_units`、`allowed_purposes`、`allowed_statistics`、`required_conditions`。每项都是允许的字符串列表，按原值精确匹配。可从已有观测明细查看可用值。不同条件下的测量在组表时分别保留。

## 新抽取

对使用 `prompt_description` 和 `field_list` 的任务，有两种入口。

- 传入 `table_subject` 时，原有文档归属分析按单独的 `max_analysis_calls` 预算执行。随后默认生成数据表。Python 可额外传入 `dataset_options`，CLI 对应 `--dataset-config`。
- 只希望基于已有候选离线组表时，在 `extract(..., dataset_options={"row_subject": "individual specimens", "subject_label_field": "specimen"})` 中提供配置。此方式仅使用原抽取的模型预算。Python 从 `result.dataset` 读取数据，使用 `output_dir` 的运行同时写入 `dataset/`。

对没有保存归属分析的原始运行，可显式传入 CLI 的 `--raw-only`，或调用 `assemble_dataset(source, result_dict, analysis=None, **options)`。常规回放默认要求归属分析文件，以便识别错误路径或意外缺失的产物。

## 确认值与待定值

主表末列「记录状态」支持筛选。「已确认」表示局部归属、目标身份和字段适配均有依据。有来源的待定观测也可进表。例如原文明确某一读数属于甲、乙设备中的一台，就只显示一个候选行；同名设备的厂区仍有歧义时，在状态列列明范围待定。原文只给出毛料的长度，任务明确排除毛料长度时，仅保留观测明细。若原文给出长度、但未说明是否属于任务所指的可用长度，可按字段口径待定展示。状态区分原文直接报告的属性和用户请求的属性。未找到任何有依据的候选目标时，在明细中保留原值和引文。

原文名称与规范名可以指向同一篇内主体。明确的定义或分组以及同范围的局部属性陈述支持名称对应；样品属于设备、部件属于整体等关系各有方向。名称对应和属性记录可以分别查证。

普通抽取在首轮完成后检查可识别的表头及原始值格。有明确目标表头、其下有数值、相应格没有返回该字段时，可在同一抽取预算内定向补读一次。保留共享表头与已有值格的出处。图像、无法定位的网格或缺乏可识别任务字段的表头仍可能漏抽。离线 `assemble-dataset` 只使用已存输入与结果。

## 读取结果

| 文件 | 读取方式 |
| --- | --- |
| `main.csv` | 用户定义的列按原顺序输出，再附「记录状态」。如有重名，实际列名见 `dataset.json` 的 `status_column`。使用身份字段可直接读取主体，或按行序对照 `lineage.json`。 |
| `observations.csv` | 每条观测保留原始 `entity`、直接与目标主体名、原值、单位、条件、用途和候选指针。复合证据及各判断以 JSON 保存在单元格内。 |
| `lineage.json` | 查看每个主表行和每个值关联的观测 ID，再沿明细内的候选指针和 Unicode 字符偏移回读原文。 |
| `dataset.json` | 完整的可重用 JSON，含两张表、证据、独立的归属和字段适配状态、覆盖缺口与预算。可用它区分空值、字符串和数字。 |

省略主体显示名配置时，CSV 在「记录状态」中附上主体名。CSV 第一条数据行的主体在 `lineage.json` 的 `rows[0].subject_label`，第二条对应 `rows[1]`，依此类推。如需用单独的列筛选主体，请在 schema 中声明主体显示名列，并用 `subject_label_field` 指向它。

严格查询、关联检索和探索查询共用观测 ID。严格查询只返回已确认的入表观测。关联查询要求指定主体存在有依据的连接，可以返回部件、样品或组合对象的观测。探索查询还返回具有候选名称的待定记录。

```python
from pathlib import Path
from attestextract import assemble_from_files, query_dataset

dataset = assemble_from_files(Path("source.md"), Path("run"), subject_label_field="specimen")
measures = query_dataset(dataset, "A", field="mass")
related = query_dataset(dataset, "R", association_only=True)
leads = query_dataset(dataset, "R", exploratory=True)
```

## 可选的语义补查

先用零预算查看尚待处理的工作。此命令可在无凭据环境中运行，结果以 JSON 写到标准输出。每篇文档选一个单独的 SQLite 文件。

```sh
attestextract supplement-dataset --input source.md --run-dir run --dataset-config dataset-options.json --ledger review/work.sqlite --max-calls 0
```

启用模型判别时，设置 `--model-id` 和正整数 `--max-calls`，并配置该预设的凭据。重复使用同一账本，已派发的尝试继续占用累计额度，输入片段会发往所选服务。`attestextract models` 可列出预设。Python 也可用 `attestextract.dataset_supplement.run_supplement(dataset, source_text, ledger_path=..., max_calls=..., backend=...)` 显式传入单次尝试的后端。

返回值分别给出模型判别、失败、待核项、尝试数和后端报告的 token 用量。有失败且尚待处理的补查任务时，CLI 输出报告并返回状态码 2。模型判别带有证据和来源标记，独立保存在补查报告中。原文中缺少匹配候选的数字只构成查漏线索。例如页码、编号也可能进入队列。漏抽率和科学准确率需要与人工标注对照计算。

对于跨句或多处引证的肯定判断，模型须在 `applicability` 中说明适用对象与条件，并引用含有该项观测的局部原文，程序验证其坐标。只有单条局部引文时，可以直接以这处引文标出范围，`scope_basis` 会注明采用了这一方式。新发现的观测也要满足任务对单位、条件、统计量和用途的显式约束。

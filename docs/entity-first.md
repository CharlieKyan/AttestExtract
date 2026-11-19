# 实体优先抽取

主表一行代表一个明确对象时，可以选用实验策略 `entity-first`。例如日报说“A 线早班产出 80 箱，B 线早班产出 60 箱，两线合计 140 箱”。第一遍登记文章提到的生产线。第二遍填属性，即使 B 漏登了，只要本次原文清楚、符合任务且身份没有冲突，也可当场补登 B 并把 60 填给它。140 在明细中按组合保存一次。只有名字的 C 留在名册。

## 开始使用

准备原文 `log.md` 和现有格式的任务 `task.json`。

```json
{"prompt_description":"Extract the count produced by each named line.","field_list":{"line":"Production line name","count":"Number of boxes produced by this individual line"}}
```

如需把可读的名册名称填到指定身份列，另存 `dataset-options.json`。

```json
{"subject_label_field":"line"}
```

```sh
attestextract extract --input log.md --task task.json --table-subject "individual named production lines" --dataset-config dataset-options.json --subject-strategy entity-first --model-id deepseek:deepseek-flash --env-file .env --max-model-calls 24 --output-dir output/lines-01
```

加 `--check` 可做离线预检。正常抽取请选择新的输出目录。省略 `--subject-strategy` 时使用观测优先。Python 的 `extract` 支持相同的 `subject_strategy`、`table_subject`、`dataset_options`，预算由 `ExecutionOptions(max_model_calls=...)` 指定。

## 两遍怎样读

1. **登记对象。** 每张卡保存篇内 ID、原文名称、身份依据、明确的别名和适用的限定信息。模型根据任务的行粒度提名对象。跨章节复用一张卡需要有相符的原文身份。相同短名存在多个厂区、批次或情景时，可分别登记。
2. **填写属性。** 每个可读原文块都进入队列。请求放得下时提供完整紧凑名册；大名册按名称、别名和局部语境选卡，可能竞争的同名卡成组展示。一次可以给多个对象填属性，也可只报告对象、交回空结果或标记待查区域。每条提议分别给出原文直接主体、原值、字段依据和可为空的所选 ID。已有卡可直接接收有据且口径相符的值。新增对象经同块证据和篇内身份核对后即可收值。留有歧义的属性进入明细。

表格支持原始行列坐标，行首、列头、统计子标题及值格可分别引用。组合和分类用途需要按用户字段定义解释。图像或网格损坏会限制可读范围。局部证据检查也可能漏掉隐含矛盾，科研正确性仍须按任务验收。

两个阶段共用一个生成预算。一篇长文通常有多次名册和属性请求。局部响应被截断时，程序保留用量，先继续其他独立块，再在剩余预算内尝试补全失败区域。鉴权或送达状态未知的失败会单独记录。`--no-entity-rescue` 可用于严格名单对照。它关闭第二遍补登，零命中块仍会读取。

## 查看结果

| 文件 | 可以查到什么 |
| --- | --- |
| `dataset/main.csv` | 用户 schema 的正式行，每行至少一项已接收的非身份属性。条件或统计口径不同可以分行。 |
| `dataset/roster.csv` | 所有已登记对象、别名、范围、登记来源、出处以及各自的正式行数。首次在属性阶段登记的卡标记为 `rescue`。 |
| `dataset/annotation.md` | 可读的正式记录和待核提议，包括直接主体、候选身份、字段、原值及引文。 |
| `dataset/observations.csv`、`dataset/proposals.csv` | 一条科学观测及其各次模型提议。按 `observation_id` 计数，按 `proposal_id` 追踪回答。 |
| `dataset/lineage.json` | 对象、行、单元格和原文位置。schema 没有身份列时，可按此文件的行序查询主体。 |
| `entity-first.json`、`report.md` | 每个块的读取状态、补登情况、实际调用阶段和用量。模型读完一个块而未报告属性，与该块未派发或回答不完整分别记录。 |

正常完成返回 0。返回 2 请查看报告中哪些原文块或协议回答未完成。单条属性待核可以出现在已完成的运行中。行数反映可用观测和条件分组，评价时同时核对对象、属性、值、口径、出处和错挂。

## 离线重建和按需补读

```sh
attestextract assemble-dataset --input log.md --run-dir output/lines-01 --output-dir output/rebuilt-01
```

dev46 的快照标记 `entity-first-v3`。重建核对原文、任务、候选及快照，用抽取时保存的同一判断生成各视图。旧 v1/v2 快照继续按其版本重建。dev44 的逐提议审计可添加 `--audit-admission`，需要原始请求和回答诊断；dev45 的提议已内嵌在快照中。

若明确希望模型补看有来源的疑难项，另行启动：

```sh
attestextract supplement-dataset --input log.md --run-dir output/lines-01 --ledger output/lines-review.sqlite --max-calls 3 --model-id deepseek:deepseek-flash --env-file .env
```

用同一账本继续时累计计费尝试。结果按模型判断单列，保留原抽取快照供对照。普通两遍抽取结束后由用户决定是否启动补读；完全漏提的事实需要另行读原文评测。

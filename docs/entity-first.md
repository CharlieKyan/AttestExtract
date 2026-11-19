# 实体优先抽取

当主表一行代表一种明确的对象时，可选择实验模式 `entity-first`。例如车间日报列出 A 线 80 箱、B 线 60 箱和两线合计 140 箱。框架先建生产线名册，再在同一处原文比较局部对象。若名册漏掉 B 线，可以保留 B 的提名和 60 箱的原文；通过独立确认后写入 B 行。合计数可留在观测明细。

## 命令行

使用现有 `prompt_description` 和 `field_list` 任务。下面的 `task.json` 和 `dataset-options.json` 分别是一个可用的合成任务和身份列配置。

```json
{"prompt_description":"Extract the count produced by each named line.","field_list":{"line":"Production line name","count":"Number of boxes produced by this individual line"}}
```

```json
{"subject_label_field":"line"}
```

```sh
attestextract extract --input log.md --task task.json --model-id deepseek:deepseek-flash --env-file .env --table-subject "individual named production lines" --dataset-config dataset-options.json --subject-strategy entity-first --max-model-calls 24 --output-dir output/lines-01
```

添加 `--check` 可在本地查看源块数和调用上限。正式运行读取原始 UTF-8 文本，输出目录使用新路径。比较严格名单时添加 `--no-entity-rescue`，使用相同预算和另一输出目录。省略策略参数时沿用观测优先。Python 的 `extract` 支持 `subject_strategy="entity-first"`、`entity_rescue=False` 和原有的 `table_subject`、`dataset_options`。

## 调用和证据

每个文档共享生成预算，优先使用 `--max-model-calls`，其次使用显式 `--max-analysis-calls`，两者都未设置时按可读块数给出预算。名单阶段可以合并局部原文。属性阶段独立遍历可读块，一个块可提供多个对象的属性。提名在本块集中确认；有已恢复对象、原值和待解归属时，最多一轮局部回查。格式重试也使用同一预算。严格模式仍然读取零命中块。Python 可以通过 `ExecutionOptions(max_model_calls=...)` 显式设置上限。

关系与字段口径由模型提出。程序核对证据的原文位置、局部端点、数值及表格位置等约束。返回的对象名、属性名或值即使同时出现在引文里，语义仍可能有误，需根据任务评价。涉及多个同名对象时保留区分语境；定性值与数值均可抽取。未能解析的网格或图片、未读块、失败调用、引文问题和未确认提名保存在报告中。文本分块覆盖与实体召回率分别评价。

选预算时先看 `--check` 的 `readable_blocks`。当前实现每个可读块各需一次属性请求；建名册、确认新对象和可选格式重试也占用上限。例如有 58 个可读块时，48 次调用不足以读完属性。`--check` 显示的是运行计划，实际使用量以 `entity-first.json` 的 `budget` 为准。

## 阅读和复现

dev44 生成 `entity-first-v2` 档案。假设原文为“B 线完成 60 箱”，程序可以在已定位的原句内截取唯一的“B 线”和“60”，同时保存较长引文、原始提名和模型给出的口径。定性短语也可以这样定位。数值边界、身份范围、属性归属和字段定义分别检查。提名待核或字段含义有冲突时，可以按候选名称探索明细。主表还要求对象与字段适用性得到支持。语言上的隐含条件和矛盾仍可能需要专业复核。

表格窗口提供从零开始的原始行列编号。例如 `{"window":"block-00001@r2c1"}` 表示该窗口所展示的第 3 行、第 2 列的格。主体、值、属性和单位都能引用可见格。一个块可同时返回对象提名、属性与少量需要查看的原文区域。`blocks[].attribute_outcome=no_target_facts_reported` 表示这次完整回答未报告目标属性，`review_regions` 给出保留范围。未派发、失败和不可读的块分别保留处理状态。

返回 `finish_reason=length` 时，调用账本保留原回答的位置或脱敏文本、用量和实际结束原因。属性阶段先读其余独立块，然后在剩余预算内对失败块补读一轮。可拆的表块分配互不重叠的值格范围，共同表头和相邻主体格仍可作证据。如果无法确定分界，可重读整个失败块。截断文本不产生正式事实。全局连接或鉴权错误会停止派发。整个过程使用本次运行的累计预算；再次启动新的在线抽取会开启另一次计费运行。

- `report.md` 给出运行状态和数量。`entity-first.json` 保存首轮对象、后补对象、提名判定、每块各阶段状态，以及共享预算与用量。`result.json` 内含冻结快照。
- `summary` 列出首轮提议数、准入对象数、使用空名单的属性请求数、实际带属性的主表行数。检查 `calls`、`blocks[].recovery`、`issues` 可区分格式、截断、证据或输入问题。明细保留模型原始主张及证据位置，`lineage.json` 保留提名与待核区域的索引。旧 v1 档案仍可从原始快照重建。
- `dataset/main.csv` 保持任务字段顺序，`subject_label_field` 指定可读的对象名列。`dataset/observations.csv` 保存原值、主体、条件、统计口径、判定及证据。`dataset/lineage.json` 可逐格回溯。如果完整 schema 没有身份或统计量列，分发时请同时提供明细和溯源。
- `attestextract assemble-dataset --input log.md --run-dir output/lines-01 --output-dir output/rebuilt-01` 可从实体优先快照离线重建。读取带分析文件的历史观测优先运行沿用原入口。
- 退出码 0 表示此次流程完成。退出码 2 表示覆盖或判定链有未完成工作，应查看每块状态和已有结果；配置错误返回 1。本实验使用固定的单次尝试后端，不支持旧分析检查点的恢复或旧版补查协议。重新启动一次在线抽取会创建一个新的计费运行。

## 怎样作比较

使用相同的原文、字段、模型和总预算。对照观测优先时，将其抽取和查证两项预算合计；实体优先计入名册、属性、遗漏确认和局部回查。在源码范围明确且人工逐项核对的段落或表块计算对象和属性召回，分别统计错误建行、错挂、遗漏对象连带损失的属性、调用和 token。固定模型回答并故意从首轮清单删去一个对象，只能验证恢复机制；自然遗漏率和线上质量需要独立的模型运行与评审。

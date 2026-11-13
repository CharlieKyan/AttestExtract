# 专家审阅与表格定向恢复

dev31 的普通 Python `output_dir`、CLI `--output-dir` 及 CLI 默认运行目录
同时生成 `result.json`、`report.md`、`review.json`、`review.md` 和诊断目录。
显式使用旧 `--output` / `--report` 的路径仍保持原行为。

## 阅读结果

先看 `review.md`：索引展示哪些目标共同组成一条审阅行，随后是原值、
单位、完整引文、候选引用及检查问题。`review.json` 是对应的精简结构，
不重复任务定义或模型原始回答。同字段多值使用列表，绝不任意选择一个。

对于简便任务，只有对象、条件和适用范围的原标签一致才共同展示；对象
缺失的候选单独展示。条件缺失不会被解释为同一实验情景，正文未声明的
范围仍显示未声明。相同名称也不能证明全文实体相同。这是审阅分组，
不是数据库层位归并，也不生成领域 ID。普通 TaskSpec 保守地逐候选展示。

原值、范围字符串、单位与条件不改写，不推算 min/max 或平均值。每条
观测保留候选 ID 和原结果 JSON Pointer。`result.json` 仍是完整档案；
完整测量证据组及其他详细内部结构以它为准。已有失败检查与候选关联
会显示，科学含义尚未经过专家或 Jev 确认。

格式隔离记录单列，包含模型原声明与拒绝原因，不进入正常审阅行。
“没有保留有值候选的目标”不能当成原文未报告的结论。

已有结果无需重新调用模型：

```powershell
python -m attestextract export-review --input '.\old-run\result.json' --output-dir '.\expert-review'
```

不指定输出目录时创建新的同级目录。输出不得覆盖原运行或其子目录。
该命令不读取凭据、不联网；不能用它修正原结果或解除复核。

## 为什么全部块处理完仍会部分完成

`responses processed` 表示该块响应已处理。合法记录保留、非法表格记录
隔离，因此处理完全部块不等于所有记录通过格式检查。终端另报正文候选、
表格保留数与隔离数；`format_status=partial_format_error` 时 CLI 返回 2。

旧协议允许模型省略有值 Binding 的 `relation`，但本地检查需要它。
dev31 的请求明确要求有值或有引用的 Binding 声明来源关系，并提供纯合成
格式示例。完全空 Binding 保留兼容。程序不会替模型填写关系；模型仍可能
输出错误或错误关联位置，新的提示不构成真实质量通过证明。

## 只重抽隔离表格

普通恢复复用兼容检查通过的已缓存完整表格响应，不会再次生成。固定配置入口可显式用：

```powershell
python -m attestextract extract --input '.\paper.md' --task '.\task.json' --model-id 'deepseek:deepseek-flash' --env-file '.env' --resume '.\old-run\diagnostics\execution\checkpoint.json' --retry-tables --max-model-calls 11 --max-attempts 1 --output-dir '.\new-run'
```

这里的 `11` 是示例累计上限：若旧运行用了 9 次，选择两张隔离表格时最多
补两次。实际必须依据旧账本确定，额度不会恢复为零。附加 `--check` 可先
离线检查 `table_retry_chunks`、可复用块与剩余额度；使用另一个新输出目录。

Python 对应同一 `extract()` 的 `resume_from=...`、`retry_tables=True`、
`execution=ExecutionOptions(max_model_calls=11)`，并将 `output_dir` 指向新目录。
其他原文、任务、模型和生成参数必须保持一致。

边界：

- 每张完整收到但隔离的表格在一条检查点恢复链中仅有一次显式重抽机会，HTTP 尝试必须为 1。
  其他块必须已有完整可验证缓存；不会顺带重跑正文或未知结果的块。
  后续应从最新检查点恢复；回到旧检查点另开分支不能跨分支自动汇总调用。
- 超时、断线、占额后中断均保留账本，不自动补发；再次隔离也不会获得新额度。
- 保存旧表格响应副本、校验值和迁移记录；旧运行目录只读。
- dev30 的 v3 固定 Chat 运行仅在原任务、原文、全部请求、Schema、缓存和
  账本完整匹配时迁移。只支持所有根块均一次调用并完整收到的
  `partial_format_error` 或 `format_error` 运行。
  普通旧表格恢复不能静默跨协议修订；不支持的历史运行明确报错。
- 本轮不支持自动多路径 v4 的表格重抽。旧结果仍可离线导出专家视图。
- 重抽是新付费调用，结果仍需格式、证据及人工审阅，不能承诺自动修好。

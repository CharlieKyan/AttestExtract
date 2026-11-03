# 分块、调用预算与恢复（dev13）

CLI 默认按任务的输出复杂度规划分块；遇到明确的输出截断时，在统一调用预算内继续细分失败块。已完成的模型回答保存到本地检查点，恢复时无需再次生成，但必须重新校验结构、定位证据并计算候选关联。它不把截断回答中的部分记录当成成功结果，也不实现 Jev 或语义正确性判定。

## 默认行为

| 选项 | 默认值及含义 |
| --- | --- |
| `--chunking adaptive` | 根据字段数、测量证据组、数字语句密度、预计引文长度缩小初始块；`fixed` 只关闭这一步预规划 |
| `--max-chunk-chars 6000` | 初始字符目标；优先完整句子，原子词／公式／表格行可以超过目标 |
| `--max-output-tokens` | 模型预设 8192；`provider` 不发送上限，但不代表无限输出 |
| `--max-model-calls` | 未指定时为 **初始规划块数的 3 倍**，空文档至少设为 1；不是无限重试 |
| `--max-split-depth 3` | 初始块深度为 0；截断后的细分最多深入 3 层；设 0 禁止截断细分 |
| `--format-retries 1` | CLI 未显式配置时默认一次格式重生成，显式 CLI／profile 的 0 保留；也占用统一预算。Python 默认仍为 0 |
| `--resume PATH` | 选择以前的运行目录、诊断目录或 `execution/checkpoint.json`；输出保存到新的运行目录 |

输出估计是通用启发式，不是服务商 tokenizer。数字语句按每三个密度单位估计一条观测，避免把数值、条件、公式中的每个数字都当成独立记录；同时保留按文本长度估计的下界。当前按输出预算的 70% 规划；服务商模式没有明确请求上限时用 8192 作为规划参考，不将它发送为上限。默认最小细分目标 500 字符，Python 可调整。

初始分块和截断细分共用句子边界：段落内优先完整中英文句子，超长句依次考虑换行、子句、词边界，最后在未受保护的字符边界降级。保护小数、科学计数、常见缩写及可识别的 LaTeX，不改写原始 Unicode、空格或换行；表格仍保留完整行和带来源的重复表头。长原子单元可以超过目标，`chunk_plan.json` 标注 `character_target_exceeded`。`fixed` 关闭复杂度规划，但仍使用句子边界。规则不是完整语言解析器，也不添加跨块语义上下文；长句仍可能拆开，跨块关系仍需单独评估。

不降低任务字段、不修改原文、不补 JSON、不静默换模型、不自动提高输出上限。只有 `truncated_response` 触发细分；网络、鉴权、拒绝、空回答和格式错误不会通过拆小块反复试错。到达深度／最小块／不可拆表格边界时明确失败。

## 最少命令与预检

在仓库根目录配置好本地 `.env` 后：

```powershell
python -m attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --model-id deepseek:deepseek-flash --env-file .env --check
```

`--check` 显示块数、总调用预算、已预留次数及内置适配器 HTTP 尝试上界，不发请求、不验证密钥或模型效果，也不产生可恢复的抽取检查点。去掉 `--check` 即运行；输出位置沿用 `outputs/<时间-ID>/`。切换到 Qwen 时只改 `--model-id qwen:qwen3.8-27b`；切换模型是新运行，不能复用另一个模型的检查点。

需要自己限定工作量时，例如增加 `--max-model-calls 12`。CLI 显示预算、请求／已复用块、截断细分、失败原因和恢复路径；原文与完整响应在诊断目录，不刷到终端。

## 调用预算的精确定义

预算单位是统一后端的 **`backend.generate()` 调用次数**，包括原始请求、格式再生成、被截断的父块和所有子块。调用前原子保存预留；进程被中断时不退还这次预留，因为无法证明服务商没有处理它。格式重生成在同一恢复链内每块最多一次，预留也在调用前保存：中断或传输失败不会退还重生成额度。第一次格式失败后如果仅因预算不足没能发起重生成，恢复可以接着使用这一次机会，并携带已保存的结构反馈。

内置兼容适配器的 HTTP 重试另由 `max_attempts` 限制。预设为 1，因此一次生成最多一次 HTTP 尝试；高级 profile 默认 3，因此总 HTTP 尝试上界为 `max_model_calls * max_attempts`。自定义适配器必须自行限制内部请求；框架不能约束自定义代码内部的网络操作。预算限制次数，不是金额／总 token 的财务上限。

预算针对所选检查点及其后续恢复链，不是账号级全局账本。不要同时从同一旧检查点启动多个恢复进程；分叉、重开新运行及其他程序的请求均不共享预算。恢复时不指定预算便继承原预算；若已耗尽会立即停止。传入新值是**这条恢复链的总额度**，不是追加次数。例如旧记录已预留 8 次，`--max-model-calls 12` 只允许最多 4 次新生成。小于已预留次数的值会被拒绝。

## 恢复与证据文件

使用相同 input、task、模型配置和规划参数，并增加 `--resume <旧运行目录>`。CLI 默认在旁边创建新输出目录。显式 `--output` 必须另配新的 `--log-dir`，且输出、报告、诊断均不得写入此前运行及恢复历史目录。不要删除旧运行：历史失败响应仍保存在那里，新检查点 `history` 保留路径。

恢复前核对精确原文 hash、任务、包版本、模型／端点、生成参数和规划参数。只允许改变总调用预算和格式重生成开关，变更写入 `policy_changes`；已用次数不变。把 0 改成 1 可以使用尚未预留的重生成机会，把 1 改成 0 会阻止待发的重生成。反复切换或增加总预算不能复活已经预留／耗尽的机会，此时明确报 `format_retry_exhausted`。密钥不进入指纹，可以轮换。改变模型、Schema 模式、输出 token 上限、任务或分块策略均需新运行，不能混用旧缓存。源文件名也属于身份。

| 路径（位于 diagnostics 内） | 内容 |
| --- | --- |
| `execution/checkpoint.json` | 带 SHA-256 的块树、状态、预留账本、逐块格式失败／重生成计数与有限反馈、配置指纹、策略变更、历史路径；每次状态变更原子替换 |
| `execution/cache/<chunk>.json` | 已通过 JSON／任务结构检查的统一响应；仍是待证据复核的模型数据 |
| `execution/summary.json` | 初始／当前叶块数、已完成／复用数、预算、已观察统计 |
| `chunks/<chunk>.json` | 原文块及原始 Unicode spans，调用前保存 |
| `responses/<chunk>.txt`、`.meta.json` | 回答、已知用量及结束原因；格式第二次请求使用 `.format-2` |
| `http/`、`validation/`、`error.json` | 已有传输／格式／运行失败诊断 |

恢复先检查**全部**完成缓存的完整性、结束原因与任务结构，再发任何新请求；缓存损坏、缺失、被脱敏改写时明确失败，不默默重发。校验和防止意外损坏，不提供真实性认证；只使用自己可信的运行目录。候选每次重新从原文定位、去重、分级和计算关联，未完成整轮前没有成功的 `result.json`。输出文件写入失败后也可从已完成检查点重新生成报告而不再调用模型。

`response_count`、`request_attempts` 和 `usage` 是恢复链中**已观测**的累计统计，包含能读取元数据的失败回答；`elapsed_seconds` 仅为本轮时间。连接断开／进程中断后的真实服务端用量可能未知，因此不能把这些统计当成完整账单。`calls_reserved` 用来保守控制预算，可能大于已观测响应数。

本版检查点格式为独立版本 2；dev12 的版本 1 检查点与新分块计划不兼容，需要新运行，不能删除版本检查强行复用。dev11 及更早运行没有检查点，也不能直接恢复；历史原始回答仍可人工查看。现有结果 schema 0.2、诊断 schema 0.3 保持不变。没有持久化诊断目录的调用只有本轮内存预算，不支持跨进程恢复。

终端与 `execution/summary.json` 显示完成／首次调用通过／格式恢复／细分／复用／失败／待处理块数和调用预算。计数描述模型输出的结构处理过程，不代表事实接受或完整召回。解析失败不会返回空记录继续冒充成功；只有所有必要块处理完成才输出成功结果。

## Python 接口

为保持旧调用与固定响应回放兼容，Python 默认不启用新的执行策略，需要显式传 `ExecutionOptions`：

```python
from pathlib import Path
from attestextract import ExecutionOptions, extract, load_task
from attestextract.diagnostics import RunTrace

result = extract(
    Path("src/attestextract/examples/procurement.md").read_bytes().decode("utf-8"),
    load_task("src/attestextract/examples/procurement.task.json"),
    model="deepseek:deepseek-flash", env_file=".env",
    execution=ExecutionOptions(max_model_calls=12),
    trace=RunTrace("outputs/python-run-1/diagnostics"),
)
```

恢复时增加 `resume_from="outputs/python-run-1"`，新 trace 使用 `outputs/python-run-2/diagnostics`；其他参数保持一致。自定义适配器还需显式传不含密钥的 `backend_identity` 字符串，绑定模型及所有会影响输出的配置；改变配置就改变该身份。自定义响应应归一化为 `finish_reason="stop"`／`None`，截断为 `"length"` 或抛出 `code="truncated_response"` 的后端错误。

## 不花费模型额度的验收

```powershell
python scripts/execution_smoke.py --output-dir outputs/execution-acceptance
```

使用尚不存在的目标目录；脚本只使用合成文本和 MockTransport，固定演示：父块截断 → 完成 1 个子块 → 预算用尽 → 恢复另外 2 个子块 → 再次恢复新增请求为 0。总计 4 次模拟 HTTP，真实请求 0 次；验证旧文件不变、值为 11／22／33、证据精确回指原文。它验证工程行为，不代表真实模型的召回率或准确率。

格式恢复另有独立验收（目标目录同样必须不存在）：

```powershell
python scripts/format_recovery_smoke.py --output-dir outputs/format-recovery-acceptance
```

固定 5 次模拟 HTTP、0 次真实调用：已完成块复用，格式失败块预算恢复后只重生成一次；连续两次格式失败后，多次恢复并增大预算仍不新增调用，不细分、不输出成功结果。检查完整响应、错误反馈、旧目录哈希和原文证据。

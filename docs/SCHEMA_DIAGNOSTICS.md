# 定位 json_schema 问题

`--structured-output json_schema` 会在 OpenAI 兼容请求里发送
`response_format.json_schema`，并设置 `strict: true`。它不会切换到服务商的原生
API，也不会在失败后自动改成 `off`。同一个模型名称经由不同代理或路由，支持情况可能不同。

新增诊断只检查和记录，不修改模型响应，不自动重试，也不把 HTTP 200 当成服务端确实
执行了 Schema 约束的证明。原有 `--format-retries` 行为保持独立。

## 先用小样本定位

在仓库根目录运行下面的 PowerShell 命令。使用合成输入和实际任务配置，避免先把整篇
论文发到接口。此命令会调用你配置的真实模型，按服务商规则计费。

```powershell
$root = Join-Path (Split-Path $PWD) "research\attestextract"
$probe = Join-Path $root "runs\schema-probe\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $probe | Out-Null
.\.venv\Scripts\python.exe -m attestextract extract `
  --env-file .env --structured-output json_schema --format-retries 0 `
  --input src/attestextract/examples/measurements.txt `
  --task src/attestextract/examples/measurements.task.json `
  --output "$probe\extraction.json" --report "$probe\review.md" `
  --log-dir "$probe\diagnostics"
```

以上先使用打包的合成任务；检查实际任务时，把 `--task` 替换为自己的任务文件（例如本地的 `literature_parameters.dev7.task.json`）。如果你使用别的工作目录，替换 `$root` 和任务路径。探测文件可以换成自己的短片段；
先保持模型、任务、输入一致，只比较 `json_schema` 与 `off`，每次使用新日志目录。
不要用整篇论文反复试接口兼容性。

## 按层检查日志

1. `requests/chunk-0001.request.json`：实际发送的 HTTP JSON。
   确认包含 `response_format`、`strict: true` 和预期字段；里面也有完整提示词和原文。
2. `schema/chunk-0001.request.json`：请求模式、`schema_sent`、规范化 Schema 的
   SHA-256。相同 hash 表示比较的是相同 Schema；这不代表模型、提示词、输入也相同。
3. HTTP 失败时看 `error.json` 指向的 `http/*.error.json`：先读
   `status_code`、`provider_error.code/message/param`、请求 ID。
   400/422 可能是参数不支持、Schema 子集不支持、路由配置错误等；具体原因取决于服务端
   返回的证据。`compatibility_hint` 只是提示，`root_cause_verified: false` 保留这个区别。
4. HTTP 成功后看 `schema/chunk-0001.compliance.json`：把原始响应逐项与本次
   目标 Schema 对照，检查字段缺失、类型、额外字段、枚举、重复键和非法 JSON。
   `errors[].path` 定位到缺失或不合规字段，最多保留 50 项，不回显字段值或未知键名。
   配置测量证据组后，也会检查 `records[i].measurements.<组名>` 的四个字符串字段；
   例如缺少 `value_text` 会指出对应路径。新增证据组会改变 Schema hash，比较接口
   行为时应保留这一差异。结构通过仍不代表数值、单位、条件的语义关系正确。
5. `responses/chunk-0001.txt` 保留原始模型文本；`events.jsonl` 中的
   `model_response/completion_status` 给出结束原因；`validation/*.errors.json`
   给出本地候选契约的错误。二次格式生成另存 `.format-2` 文件，不覆盖第一次。

| 观察 | 解释与下一步 |
| --- | --- |
| HTTP 400/422，无 compliance 文件 | 尚未获得可检查的模型正文。根据服务端 `param/message` 联系服务商或调整明确不支持的参数。不能据此断言 Gemini 不支持结构化输出。 |
| HTTP 200，compliance `failed` | 本次原始输出没有遵守目标 Schema；按路径查看响应。若服务端宣称 strict 支持，可携请求 ID、Schema 和脱敏小样本询问路由实际行为。 |
| HTTP 200，compliance `passed` | 只证明这一份输出符合本次 Schema，不证明服务端强制约束，也不证明语义或证据正确。 |
| compliance `not_evaluated` | 没有目标 Schema，或检查器不支持该自定义 Schema；不能按通过处理。 |
| `schema_sent: false` | 本次为 `off`，未请求服务端结构约束。仍可对照目标 Schema观察输出质量。 |
| 本地候选保留成功，但 compliance `failed` | 本地契约允许部分缺失信息进入复核；严格请求 Schema 则要求完整包装。这两种检查用途不同。 |

检查器覆盖程序生成的 `type/properties/required/additionalProperties/enum/items/anyOf`
子集；新增不支持的 Schema 约束会明确标记 `not_evaluated`，不会假装完整验证。Markdown
代码围栏不是原始 JSON，因此会在本诊断中失败，即使后续候选解析能去掉围栏继续处理。

成功一次后，再分别测试“最小任务”和“实际完整任务”。如果仅完整任务失败，缩减到服务商
明确指出的 Schema 片段做对照。保留两次请求与服务端错误证据，不要先删除所有约束或默默
退回 `off`。服务商原生协议适配、自动能力探测均未在本版实现。

## 离线验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_schema_diagnostics.py tests/test_structured_backend.py tests/test_http_diagnostics.py
```

这些测试使用模拟 HTTP，没有真实接口费用。它们验证日志、脱敏和检查器行为，不能代替
对你所用服务商路由的真实探测。日志包含论文和响应内容，请留在本地，不提交公开仓库。

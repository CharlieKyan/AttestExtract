# 结构化输出与格式恢复（dev17）

dev15 发布后的真实反馈已显示：发送 `text.format.type=json_schema` 后，服务仍返回了
非法 JSON。已核查的失败链中，一次重生成也未恢复。因此 dev16 默认不再追加这次调用，
但保留显式选择。此证据支持撤回默认策略，不能推出重生成对所有模型和任务都无效。

本次看到的错误分别为字段对象提前闭合、属性名缺少开头双引号，均超出下面匿名 `{`
诊断器的范围。发送的任务示例与 Schema 本身通过本地检查。协议适配器负责请求／响应
转换，无法替代服务端的约束解码；本指南的 Responses 命令是可选协议用法，**不是已验证
能解决格式问题的推荐**。软件测试验证行为，不能以模拟回复恢复成功证明真实模型效果。

dev15 增加的诊断针对“对象期待属性名，却遇到匿名对象”错误。程序在标准 JSON
解析失败后尝试给出更具体的结构解释，仍然拒绝原回答；显式启用后可在一次重生成额度内，
让模型依据原文重新生成完整块。没有字符串替换修复、部分字段抢救或空列表跳过。

## 错误反馈发生了什么变化

例如合成回答的结构是 `{"subject": <value>, {"amount": <value>}}`。
`<value>` 仅为去值示意，不是可直接提交的 JSON。反馈现在指出：

- 位置 `records[0].values`，分类 `unexpected_object_in_object`。
- 这里需要带引号的属性名，却遇到匿名 `{`；相邻的命名属性应属于同一对象。
- 正确形状为 `{"subject": <value>, "amount": <value>}`；values/quotes 完整结构仍以任务示例为准。

提示只包含声明过的键名和固定占位符；未知键替换为 `<undeclared>`，不把模型值或引文
再次塞进系统提示。完整错误回答保留在 `diagnostics/responses/`，不要提交公开仓库。
`validation/*.errors.json` 保留原有 `invalid_json`、行列和 Unicode offset，并增加
`errors[0].structure`。终端只增加一行结构位置，不输出原始回答。

扫描器只诊断上述可确认的语法形态，最多扫描 131,072 个字符、32 层、8,192 个词法单元；
合法对象数组以及字符串内的括号不会触发。无法确认或超过上限时保留通用解析错误。
诊断不保证模型下一次一定正确；第二次仍失败就停止，保留已完成块、两次回答和检查点。
增加总预算或反复恢复不会重置已用的重生成额度。
缺少具体 `structure` 提示时，重生成仅包含通用解析原因与位置，不再附加没有对应数据的
`wrong_shape/correct_shape` 占位说明。这是清理无效说明，未证明模型成功率提高。

## 显式选择 DeepSeek Responses Schema

[官方 Responses API](https://api-docs.deepseek.com/api/create-response/) 文档定义
`text.format` 的 Schema 请求方式；[协议指南](https://api-docs.deepseek.com/guides/responses_api/)
定义请求、输出状态和参数映射（核对日期 2026-11-03）。框架新增独立适配器和预设，
不把 Chat 的 `response_format` 或 `thinking` 参数照搬过去。

| 选择 | 请求路径与格式 | 非思考配置 |
| --- | --- | --- |
| `deepseek:deepseek-flash` | `/chat/completions`；`response_format.type=json_object` | `thinking.type=disabled` |
| `deepseek-responses:deepseek-flash` | `/responses`；`text.format.type=json_schema` | `reasoning.effort=none` |

两者都读取 `DEEPSEEK_API_KEY`，默认 8192 输出 token、温度 0、60 秒超时、一次 HTTP
尝试；CLI／Python 默认零次格式重生成，显式设置 1 才允许一次。新通道仅允许官方 HTTPS 根地址或 `/v1`；不支持自定义网关、
流式、工具和服务端会话。保留证书校验的 TLS 兼容设置由预设处理。

在仓库根目录配置好 `.env`，先运行不发网络请求的预检：

```powershell
.\.venv\Scripts\python.exe -m attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --env-file .env --model-id deepseek-responses:deepseek-flash --check
```

下面是实际抽取命令，会调用真实服务并计费；本次发布没有执行它：

```powershell
.\.venv\Scripts\python.exe -m attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --env-file .env --model-id deepseek-responses:deepseek-flash
```

Python 使用同一个选择入口：

```python
from attestextract import extract, load_task

result = extract("Lamp A uses 8 W.", load_task("task.json"),
                 model="deepseek-responses:deepseek-flash", env_file=".env")
```

Python 默认仍不格式重生成；显式传 `format_retries=1` 才启用。预算与持久化恢复需要
`ExecutionOptions`，详见[恢复指南](EXECUTION.md)。高级配置使用 `adapter="deepseek-responses"`
时须对应设置 `output_token_parameter="max_output_tokens"`；不能混用 Chat 的 token 参数。
高级设置开启思考时，官方说明 `temperature` 不生效；Flash 非思考模式的 `top_p` 固定为 1。
这些服务端限制不等于用户传入的采样值已实际执行；默认预设不发送 `top_p`。

`completed` 响应只提取 assistant 的 `output_text`，原样拼接并转换 token 用量；
`incomplete/max_output_tokens` 映射为截断，走已有预算内细分；拒绝、失败、空文本和工具
输出明确失败，不进入格式重生成。HTTP 400 不降级到 Chat 或 JSON Object。
`responses/<chunk>.protocol.json` 单独保留脱敏响应 ID、协议状态及已知截断原因，
避免统一响应元数据覆盖原生状态；它也不包含服务端推理内容。

Schema 的发送、返回内容是否符合 Schema、服务端是否真正强制约束分别记录。
`schema_sent=true` 不能证明强制约束；本轮始终标明 `server_enforcement=not_verified`。
现有严格本地候选契约与证据检查继续执行；更严格的请求 Schema 对照仍是诊断，例如
缺失字段可进入复核，不把 Schema 诊断当作语义接受决定。

## 不调用模型的完整验收

在仓库根目录直接执行；每次使用新的输出目录：

```powershell
$acceptance = Join-Path (Split-Path $PWD) ("research\attestextract\audits\structured-output-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff"))
.\.venv\Scripts\python.exe scripts/structured_output_smoke.py --output-dir "$acceptance"
Get-Content -Raw -Encoding UTF8 (Join-Path $acceptance "summary.json")
```

预期：`passed=true`、4 次模拟 HTTP、0 次真实调用，结构定位与重生成、Responses
Schema 请求、已完成检查点零调用恢复、截断拒绝均通过。该脚本也进入 wheel 仓库外安装
验收与 CI。它验证框架实现，不证明真实模型遵守 Schema 或抽取完整性。
其中重生成用例显式设置 1；普通 CLI 最小命令默认 0，由独立回归验证失败后不发第二次请求。

升级包版本或换模型／协议需要新运行，已有原始回答和检查点不改写。相同版本、配置下
才可恢复；若旧块已用完格式重生成，不能以增加总预算再试第三次。

## dev17 已实施与验证边界

新模型调用统一使用 flat-v1（record_type、values、quotes、声明的 measurements），不提供旧协议的在线选项；native-v1 仅供显式历史离线回放。解析器不自动识别协议、不包装旧裸字段、不修补非法 JSON。

提示词、合成示例、任务 Schema 和重生成反馈使用同一格式。缺引文进入复核，同时保留原回答的 Schema 诊断；未知键、孤立引文键、类型错误、重复键和非有限数明确失败。原始回答保存在 responses，转换后的内部字段保存在 normalized，二者不互相替代。

格式简化不代表服务端硬约束已生效，也不保证模型永不生成非法 JSON。默认重生成仍为零，显式一次重生成及截断细分维持现有预算。结果 schema 0.3 的范围证据改进见 [测量指南](MEASUREMENT_REVIEW.md)。真实效果与软件模拟、旧回答离线回放分别报告。

# CLI 默认值与验收（dev24）

这是正式包入口的行为，不依赖论文启动脚本。密钥配置一次；用户主要填写模型、输入和任务。`.env` 明确通过 `--env-file` 读取；已有进程环境变量仍优先。

```powershell
attestextract extract --model-id deepseek:deepseek-flash --env-file .env --input paper.md --task task.json
```

更换为 `--model-id qwen:qwen3.8-27b` 即使用 Qwen。地址、凭据和生成参数由已支持的模型预设提供；连接策略和一次 HTTP 尝试的默认值也适用于高级 profile 与直接创建的内置适配器。格式重生成默认关闭，显式设置仍保留。

| 配置 | 默认值与原因 |
| --- | --- |
| API 地址、凭据变量、思考参数 | 根据模型预设解析；不猜测未知模型或地域 |
| `structured_output` | `json_object`；通用性优先，本地结构和证据检查仍必须通过 |
| `temperature` | `0`；减少采样变化，不保证重复运行完全一致 |
| `max_output_tokens` | `8192`；由原先 `2048` 提高，给字段引文和测量证据留出空间。这是每次生成的上限，不是固定消耗；可能增加最大费用 |
| 超时 | 默认连接／读取／写入／连接池为 `15/180/30/15` 秒；不是整篇总时限。`--timeout` 显式覆盖全部，`--read-timeout` 单独覆盖读取；有效范围 `(0, 600]` |
| `max_attempts` | `1`；不自动重发可能已经在服务端生成的请求 |
| `format_retries` | CLI 与 Python 默认 `0`；显式 CLI／profile 设置为 `1` 才允许失败块重生成一次 |
| 分块 | 优先完整句子，字符目标 `6000`，按输出复杂度进一步规划；每批最多 `12` 行简单 Markdown 表格；原子词／公式／表格行可以超出目标 |
| 输出路径 | 不填 `--output` 时，在输入所在目录的 `outputs/<时间-随机标识>/` 保存；若文件直接位于 `inputs/`（不区分大小写），则使用与 `inputs/` 同级的 `outputs/`。包含 `result.json`、`report.md`、`diagnostics/` |
| 终端 | 默认 `--progress normal`：模型和预算、总块数、当前块、等待提示、实际返回的 token 数、候选与复核项数量、完成路径 |

显式提供 `--output` 仍兼容原有调用：输出父目录需存在，报告和文件诊断仍通过 `--report` / `--log-dir` 自行开启。正常终端进度不依赖文件日志；`quiet` 隐藏进度和成功路径，但错误仍显示；`verbose` 增加阶段名称，不打印原文、提示词和响应正文。等待时每 15 秒最多输出一行，不伪造生成百分比。复核项数量不等于需要复核的候选数量。

自动诊断目录包含原文和模型原始输出，仅留在用户本地。公共仓库忽略 `outputs/` 和旧的 `attestextract-runs/`，分享结果前仍需选择适当文件。模型或校验失败不会生成成功结果；文件写入阶段失败可能已保存部分产物，需检查诊断。`--resume` 恢复兼容检查点；不指定它仍是重新处理全文。dev12 检查点与新版分块不兼容，需要新运行；dev11 及更早没有检查点。没有日志目录的调用只接收事件，不落盘原文或提示词。

## 输出目录与服务商默认上限

目录以输入文件位置计算，不取决于启动终端所在目录。例如 `project/inputs/paper.md` 默认保存到 `project/outputs/<run>/`；`project/paper.md` 也保存到 `project/outputs/<run>/`。规则只识别文件的直接父目录，不向上搜索项目根目录。每次自动新建运行子目录，不移动或删除旧记录。显式 `--output`、`--report`、`--log-dir` 的路径选择规则不变。

CLI 的三种输出上限语义：

| 写法 | 请求行为 |
| --- | --- |
| 省略 `--max-output-tokens` | 使用当前预设或profile；模型预设仍发送8192 |
| `--max-output-tokens 16384` | 明确发送16384 |
| `--max-output-tokens provider` | 清除预设／profile的上限，HTTP请求不包含 `max_tokens` 或 `max_completion_tokens`；其余生成参数保留 |

`provider` 意为交给服务商默认值，**不是无限输出**。`--check` 的 `generation.max_output_tokens` 会显示 `null`；实际请求文件中相应字段缺省。普通格式重试保持这一选择，不自行增加上限。Python现有写法 `model_options={"generation": {"max_output_tokens": None}}` 与此对应。

```powershell
attestextract extract --model-id deepseek:deepseek-flash --env-file .env --input paper.md --task task.json --max-output-tokens provider --check
```

DeepSeek当前文档说明：非思考模式不传 `max_tokens` 默认8K。因此本项目关闭思考的预设采用 `provider` 时，可能仍遭遇同样的截断；其他服务商默认值可能不同。[官方参数说明](https://api-docs.deepseek.com/api/create-chat-completion/)

有限输出预算无法保证任意密度文档一次生成完整。持续提高上限或省略上限都不能代替分块策略；dev12 默认按输出复杂度预先缩小块，并在总调用预算内细分截断块，保存检查点供恢复。原文位置和失败证据保留，不把截断 JSON 中的前几条记录当成成功结果。默认额度为初始规划块数的 3 倍；细分深度默认 3。详见[分块与恢复指南](EXECUTION.md)。

## TLS 兼容处理

TLS 是 HTTPS 加密连接的协议，不改变模型或抽取格式。dev24 未显式配置时，模型预设、高级 profile 和内置 Chat／Responses 适配器统一使用 `tls12_fallback`，包括用户配置的兼容端点。普通用户无需填写 TLS 参数。

| 策略 | 行为 |
| --- | --- |
| `tls12_fallback`（新默认） | 先固定 TLS 1.2 探测；仅遇到白名单内的 TLS 协商／记录错误，再尝试系统自动协商 |
| `auto` | 直接使用系统自动协商，不做兼容性探测 |
| `1.2` | 固定 TLS 1.2，不做兼容性探测 |
| `auto_fallback` | 保留旧行为：先自动协商探测，仅遇到已识别的 MAC 错误才探测固定 TLS 1.2 |

兼容性探测向同一服务地址的 `/models` 发出不带密钥、论文或提示词的 GET，最多两次，每次各网络阶段限时 5 秒。收到任意 HTTP 状态仅说明加密连接已建立，不代表凭据、模型或生成可用。不跟随重定向、不读取或保存正文、不把探测 cookie 带入生成。

新策略只接受异常链内 SSL 库提供的原因码：`TLSV1_ALERT_PROTOCOL_VERSION`、`UNSUPPORTED_PROTOCOL`、`SSLV3_ALERT_HANDSHAKE_FAILURE`、`SSLV3_ALERT_BAD_RECORD_MAC`、`DECRYPTION_FAILED_OR_BAD_RECORD_MAC`。证书错误优先阻断；DNS、代理、超时和普通 `RemoteProtocolError` 不触发切换。不会依据可能含敏感内容的异常字符串猜测原因。

选择只缓存在当前后端实例内；环境代理、CA、地址或策略变化后重新判断。正式 POST 的读取、写入或协议故障可能已经在服务端产生工作，因此停止且不自动换 TLS 重发，`max_attempts > 1` 也不例外。所有内置入口未显式设置时只尝试一次；用户显式增加尝试次数，仍受既有可重试错误分类限制。读取超时默认 180 秒，不保证所有生成都能完成；失败保留检查点，用户决定是否恢复。探测失败可能已预留一次生成预算，但实际模型 POST 为 0；这不是模型计费证据。

这是基于当前环境验证结果作出的兼容性默认取舍，不能证明 TLS 1.2 对所有用户最优。`auto` 仍可显式选择，自动协商的实际版本只有网络观测后才知道。证书和主机名验证、HTTPX 环境代理及 `SSL_CERT_FILE` / `SSL_CERT_DIR` 继续生效。自定义 transport 跳过探测，实际 TLS 和证书验证状态记为未知；HTTP 本地服务不做 TLS 探测。

CLI 可使用 `--tls-mode`，配置文件使用 `tls_mode`，Python 使用 `model_options={"tls_mode": "auto", "read_timeout": 240}`；显式旧配置不会被新默认覆盖。`timeout` 省略时使用 `15/180/30/15` 秒；老 profile 明确 `timeout=60` 仍为每阶段 60 秒。CLI `--timeout 120` 清除继承的 profile `read_timeout`；同时指定 `--read-timeout 240` 则读取为 240 秒。

普通终端显示连接检查完成或兼容路径切换、读取上限、等待及恢复提示；`verbose` 和诊断提供策略、探测次数和已观测的 TLS／HTTP 版本。未知版本如实显示未知。原文、密钥和异常原始字符串不进入终端。传输失败文件仅保存异常类型、数字错误码和允许的 SSL 原因码。

普通 v3 检查点允许仅改变连接设置并记录差异，保留成功块和历史调用额度；改变模型、地址、生成或抽取内容设置仍拒绝复用。历史冻结实验另有严格环境身份，升级后不要改写旧实验清单，见[实验指南](PROVIDER_EXPERIMENTS.md)。

依据：[HTTPX SSL](https://www.python-httpx.org/advanced/ssl/)、[HTTPX 环境变量](https://www.python-httpx.org/environment_variables/)、[Python SSLContext](https://docs.python.org/3/library/ssl.html#ssl.SSLContext)。

## JSON 错误：看见原因，保留证据

例如 `"fields":{"first":{...},{"second":{...}}}` 不合法：对象逗号后需要下一个属性名，不能出现匿名 `{`。dev17 新请求改用 values/quotes 映射；原始语法位置和结构诊断仍保留，已有任务示例同步提供新结构。

框架不删括号、不补引文、不接受部分 JSON。失败终端直接给出原始响应路径和校验文件路径。`responses/chunk-0001.txt` 保留原响应（已知凭据仍脱敏）；`validation/chunk-0001.errors.json` 记录解析器原因及位置：

- `line` / `column`：去掉外围空白和 Markdown fence 后的 JSON，均从 1 开始。
- `raw_line` / `raw_column`：原始响应位置，从 1 开始；有 fence 时用这组坐标查看文件。
- `offset` / `raw_offset`：从 0 开始的 Unicode 字符位置，不是 UTF-8 字节偏移。

CLI 默认不格式重生成。显式设置 `--format-retries 1` 后，仅对本地 JSON／记录结构／任务字段校验失败的块重新生成一次，反馈包含有界解析原因和行列，不回传非法原文。原响应、新响应和 token 用量均保留；新结果继续做完整检查。显式 `--format-retries 0` 关闭重生成。网络错误、空响应、拒绝和截断不会触发格式重试。每次块尝试最多 HTTP 请求数为 `max_attempts * (1 + format_retries)`；截断后的子块是新生成，CLI 全部生成另受 `--max-model-calls` 统一约束（含已恢复调用）。检查点保存逐块格式额度，恢复／增加总预算不会重置已预留或中断的重生成机会。终端给出结构恢复、失败／待处理／复用数量；失败块不会被空列表替代。真实调用效果不能由模拟测试保证。

## 可执行验收

使用上面的实际模型／文件配置，追加 `--check`：

```powershell
attestextract extract --model-id deepseek:deepseek-flash --env-file .env --input paper.md --task task.json --check
```

它只检查本地配置、凭据形式、任务、输入和分块；与 `--resume` 合用时还验证全部缓存并报告可复用块数、剩余块数和预算，输出 `mode: local_check`、`network_calls: 0` 和实际默认值。诊断 manifest 为 `checked`，不产生 `result.json`，不验证密钥有效性或服务端 Schema 支持。去掉 `--check` 才会调用模型。

仓库开发环境中的合成验收命令（无需密钥，不调用真实端点）：

```powershell
python -m pytest -q tests/test_cli_experience.py tests/test_model_selection_cli.py tests/test_format_retry.py tests/test_schema_diagnostics.py
```

覆盖最小调用生成三类产物、模型切换、JSON 原样保留及坐标、显式重生成、进度与静默、心跳退出、TLS 验证与脱敏、服务端不支持 Schema 时明确失败。`scripts/model_selection_smoke.py` 在安装后的 wheel 上再检查 Python／显式 CLI／最小 CLI 切换，以及零网络的 `--check`。

需要验收 `json_schema` 时显式添加 `--structured-output json_schema`。检查诊断目录的 `schema/*.request.json`（是否发送）和 `schema/*.compliance.json`（返回文本是否合规）；HTTP 200 不等于服务端严格执行 Schema。服务端不支持则保留错误，不自动退回 `json_object`。

新调用固定使用 flat-v1，启动摘要及 `--check` 显示 output_protocol；用户无需额外选择。旧 native-v1 只可通过内置 ReplayBackend 显式离线回放。报告中的范围绑定展示全部端点及单位位置，候选仍不自动接受。

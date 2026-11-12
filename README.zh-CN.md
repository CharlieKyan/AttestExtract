# AttestExtract

dev30 增加 Python `output_dir` 和 CLI `--output-dir`：一个新目录或空目录保存结果、报告、诊断与恢复检查点。普通 Python 模型入口默认使用与 CLI 一致的执行管理；仍可只取内存结果。先在终端安装包，再从自己的项目中 import，无需修改框架源码。

dev29 支持“任务说明＋完整字段名与定义＋可选标注示例”直接抽取。Python 使用原有 `extract()`，CLI 将这些材料放进同一个 `--task` 文件；不必手写 TaskSpec、注册任务或先跑验收。框架离线编译并检查输入，示例不会缩小字段清单；正文和表格均使用这些材料。完整使用方法见[简便任务输入](docs/SIMPLE_TASKS.md)。没有新增任务准备模型调用。

dev28 将表格 A 路线接入普通 `extract` 入口：自动识别完整 Markdown／HTML 表格，正文与表格分别抽取，统一保存结果并共用预算和恢复流程。检查明确区分局部位置规则、累计成员出处和科学核验，表格字段继续原样交付。无需准备实验包；先按[统一抽取说明](docs/TABLE_EXTRACTION.md)运行不联网的 `--check`。

dev27 的表格 A 路线原样交付合法模型字段，程序只检查和标记，不再自动改值、改引用、合并候选或用旧结果替换新结果。原文定位、字面完整性和关联检查独立显示；支持合法空字段及单条格式错误隔离。历史 A/B 文件只读导入旧 A 回答作离线重算，参考成绩与格式等价比较分开呈现。未确认关联不自动调用核验模型，离线验证不代表新的真实模型效果。查看[操作说明与v3迁移](docs/TABLE_ROUTE_EXPERIMENTS.md)。

dev25 新增最少输入的通用入口：提供 API 地址、密钥、原始模型名、任务和原文即可开始抽取，无需注册或先跑付费样例。自动模式记录每次生成与实际模型来源，支持受限兼容切换；超时断线仍保存停止。完整命令见[最少输入说明](docs/MINIMAL_API.md)。

dev24 统一内置 HTTP 适配器的连接默认：TLS 1.2 优先，生成前遇到明确兼容错误才自动协商兜底；默认一次生成尝试。普通用户准备好凭据后只需切换模型标识。详见[连接与验收](docs/CLI_EXPERIENCE.md)。

dev23 新增离线语义复核附件：核验 C 阶段的原始回答和复核范围后，为 D 选择配置，保留严格分数和调用账本。历史实验继续使用其冻结版本；详见[复核接入说明](docs/semantic-review.md)。

按任务模式从正文和表格抽取候选数据，保留原文证据，并预留可替换的决策后端。

**早期原型 · `0.1.0.dev30` · Python 3.11+**

dev21 增加正式请求实际 TLS/HTTP 与阶段耗时观测，以及最多 108 次的冻结跨供应商实验工具。
准备、校验和模拟验收均不调用真实模型；付费执行需要单独授权。没有响应不计为 JSON 错误，
已预留试次不能自动重发。普通抽取默认值、flat-v1 和结果格式 0.4 保持不变。
详见[实验工具与证据边界](docs/PROVIDER_EXPERIMENTS.md)。

dev20 增加 Qwen 的自动 TLS 兼容探测、默认 180 秒读取等待和可调整连接设置的断点恢复。读超时保存进度后停止，不自动重发；`--check --resume` 可离线核验缓存。详见[连接设置](docs/CLI_EXPERIENCE.md)和[恢复指南](docs/EXECUTION.md)。

dev19 增加 D4 结构索引、MinerU HTML 单元格引用、保守规范化定位及覆盖检查。
`--context structure` / `--context neighbors --neighbor-chars 500` 显式启用结构或相邻段落；
默认保持 `none`。结果格式 0.4 保留模型引文、精确原文和实际提供的上下文，规范化命中仍需复核。
历史档案保持原身份。接口及限制见 [D4 原文与上下文](docs/D4_EVIDENCE.md)，
实际收益和风险的证据边界见 [四臂对照实验](docs/D4_CONTEXT_EXPERIMENT.md)。

dev18 新增离线 `prepare-decisions` / `check-decisions`：完整导出成功运行的全部候选与合法空块，校验并记录模拟建议，不应用判断。同值候选的身份、条件或证据不明确时优先显示关联依据不足。该版本抽取用 flat-v1、结果 schema 0.3（dev19 已升至 0.4），该离线功能不增加模型调用，Jev 尚未接入。用法、返回格式和边界见[离线决策材料指南](docs/DECISION_MATERIALS.md)。

dev17 新模型调用统一使用 flat-v1：字段值放在 `values`，逐字引文放在 `quotes`，保留测量证据组。用户无需额外选择协议；旧 native-v1 仅供内置 ReplayBackend 显式离线回放。新增范围端点与单位位置绑定，区分明确下标和独立数值；混合单位、损坏 OCR 继续复核，绝不自动接受事实。结果格式升级为 0.3，旧档案按原规则读取。

dev16 将 CLI 自动格式重生成默认值回退为 0，保留显式 `--format-retries 1` 与 profile 配置。用户保存的真实运行表明，改用 Responses Schema 仍可能返回非法 JSON；保留协议适配和诊断，但撤回将切换协议作为格式稳定性修复的建议。只有存在具体结构提示时才添加对应形状说明。证据与限制见[结构化输出指南](docs/STRUCTURED_OUTPUT.md)。

dev15 增加对象内匿名 `{` 的定位与脱敏结构反馈，以及显式使用 DeepSeek Responses Schema 的 `deepseek-responses:deepseek-flash` 预设。已有 Chat 预设保持不变；仍只允许预算内一次格式重生成，不修补 JSON、不跳过失败块。用法和验收见[结构化输出与格式恢复](docs/STRUCTURED_OUTPUT.md)。新通道本轮仅离线验证，未验证真实服务端约束效果；升级或切换协议后需新运行。

dev14 修复 dev13 结果版本元数据未同步的问题：新结果读取当前包版本，历史结果显式保存的版本不被改写。

dev13 增加句子优先分块、CLI 默认一次格式重生成及跨恢复保留的逐块重生成额度；已知不兼容的结构化输出请求在 HTTP 前拒绝。Python 保持默认零格式重试，通过 `execution=ExecutionOptions(...)` 显式启用预算与恢复。dev12 检查点与新版分块不兼容，需要新运行。默认值、预算含义和模拟验收见[分块与恢复指南](docs/EXECUTION.md)。

dev9 增加最少配置的模型选择入口：首次配置服务商密钥，之后只修改 CLI 的 `--model-id` 或 Python 的 `model=` 即可切换。已有 TOML profile 和后端注入继续可用。配置规则见 [D2 适配器指南](docs/BACKENDS.md)，完成范围和证据边界见 [D2 验收清单](docs/D2_ACCEPTANCE.md)。离线验证、既有真实调用和内容准确性分开报告。

dev7 增加测量上下文与数值／单位片段位置、任务声明的条件必填规则、可选空字段说明项，以及不合并候选的待确认关联。报告中的数学文本按原文显示。配置与效果评估见[测量证据和复核指南](docs/MEASUREMENT_REVIEW.md)，接口诊断见[json_schema 定位指南](docs/SCHEMA_DIAGNOSTICS.md)。

本项目从 CCUS 抽取工作流的经验出发，独立实现通用小内核，不依赖 LangExtract，也不导入原有 CCUS 工程。当前实现字段验证、候选保留、原文位置回溯和复核清单；可另行保存包含精确原文、显式审阅决定和接受记录的档案。**Jev 尚未接入**；当前检查不能证明抽取结果在语义上得到原文支持或事实正确。

[English README and API details](README.md)

[每次修改的审查摘要](docs/reviews/README.md)：按日期和改动主题查看行为变化、潜在问题、实际验证及人工重点，无需逐行翻阅全部 Diff。

## 无需密钥运行演示

```bash
python -m pip install "git+https://github.com/CharlieKyan/AttestExtract.git"
attestextract demo --output demo-results.json
attestextract demo --example ccus
attestextract demo --example procurement
```

演示使用**预先编写的合成模型响应回放**，不调用模型、不进行自动抽取，也不代表模型效果。它运行与真实抽取相同的字段验证和证据定位流程。两组示例分别包含中文 CCUS 条件观测和英文采购正文／Markdown 表格；后者使用不同任务配置运行同一内核。

默认 `--example all` 输出单个 JSON 文件，结构为 `{"examples": {"ccus": ..., "procurement": ...}}`；单例输出一个抽取结果。不指定 `--output` 时 JSON 写入标准输出，回放说明写入标准错误。

## D1 三类手工样例

```bash
attestextract d1 --output d1-archives.json
attestextract d1 --example attribute
attestextract d1 --example observation
attestextract d1 --example relation
```

这些是**人工编写预期答案的合成样例**：回放固定响应，再应用样例作者明确给出的审阅标签。不调用模型，不是实际专家验证，也不用于证明模型准确率。每个目录包含 `source.txt`（原文）、`task.json`（任务）、`response.json`（固定候选响应）、`reviews.json`（显式标签）和 `expected.json`（手工预期正式结果）。数据打包在 `src/attestextract/examples/d1/` 中，安装 wheel 后也可运行。

| 类别 | 验收重点 | 候选数 / 接受记录数 |
| --- | --- | --- |
| 属性 `attribute` | 保存产品额定功率；拒绝故意写错数值且引文不存在的候选 | 2 / 1 |
| 条件观测 `observation` | 同一试样、同一通量在两个温度下保留为两条观测 | 2 / 2 |
| 关系 `relation` | 保存原文明示的主体、关系、客体 | 1 / 1 |

关系样例使用任务声明的三个字符串字段，是 D1 的最小关系表示，尚不包含图谱推理、实体消歧或通用事件本体。`reviews.json` 的 `candidate_index` 是候选回放顺序的零基索引，必须唯一且不能越界；加载时才转换为实际候选 ID。标签带有 `origin: "synthetic_fixture"`、作者和理由。`expected.json` 独立写明候选数、正式记录数、状态和每个字段的值、引文及字符位置，排除运行时间和自动生成的 ID。

当前 D1 已实现独立安装工程，文档／任务／候选／证据／正式记录的代码格式，三类合成手工样例，以及带校验的 JSON 保存与重读。默认 `--example all` 输出 `{"examples": {"attribute": ..., "observation": ..., "relation": ...}}`，其中每项是完整档案；单例返回一份档案。原有 `demo` 继续输出抽取候选结果。

本地专家意见用于识别需要覆盖的失败模式。论文、真实批注和原始机器输出留在公共工程之外；公开样例重新编写，不复制这些材料，也不把模拟标签标为专家标准答案。

## 连接真实模型

```bash
git clone https://github.com/CharlieKyan/AttestExtract.git
cd AttestExtract
python -m pip install -e ".[dev]"
```

使用预设模型时，在本地 `.env` 中配置需要使用的服务商密钥即可，默认官方端点不需要额外填写地址或 TOML：

```dotenv
DEEPSEEK_API_KEY=你的DeepSeek密钥
DASHSCOPE_API_KEY=你的Qwen北京区密钥
```

先查看本地支持的预设；`models` 命令不联网。抽取时选择一个模型：

```bash
attestextract models
attestextract extract --model-id deepseek:deepseek-flash --env-file .env --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json
```

切换 Qwen 时，只将 `--model-id` 改为 `qwen:qwen3.8-27b`，原文和任务保持不变。Python 写法：

```python
from attestextract import extract, load_task

result = extract(
    "Purchase request: 5 desk lamps at USD 24.50 each, delivery within 10 days.",
    load_task("src/attestextract/examples/procurement.task.json"),
    model="deepseek:deepseek-flash",  # 切换为 qwen:qwen3.8-27b
    env_file=".env",
)
```

首批明确支持 `deepseek:deepseek-flash`、`qwen:qwen3.8-27b`、`qwen:qwen-plus` 三个预设，Qwen 限北京区。默认请求 JSON object、关闭思考、每次生成仅一次 HTTP 尝试、temperature 为 0、最多输出 8,192 token。CLI 与 Python 默认均关闭格式重生成；显式设置为 1 才允许每个格式失败块重生成一次。它们是请求默认值，不代表抽取准确率或服务端执行保证。已有的 `VLM_API_KEY`／`VLM_BASE_URL` 可作为 Qwen 配置，具体优先级见 [配置指南](docs/BACKENDS.md)。`.env` 仍需显式传入，不会自动加载。

通过 `--max-output-tokens provider` 可让请求不发送输出上限，服务商仍使用自己的默认值和硬上限，不表示无限输出。省略该参数时，模型预设仍使用8192。

未知模型、其他地域和特殊协议使用高级 profile 或适配器。原 CLI `--model` 保留“服务原生模型名”的语义，不解析 `provider:model`；新的 `--model-id` 不能与 `--model`、`--adapter`、`--config`、`--profile` 混用。

原有高级配置方式继续支持，例如 PowerShell：

```powershell
$env:ATTESTEXTRACT_BASE_URL = "https://your-provider.example/v1"
$env:ATTESTEXTRACT_MODEL = "your-model-name"
$env:ATTESTEXTRACT_API_KEY = "your-key"
attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output extraction-results.json
```

模型端点需兼容 OpenAI Chat Completions 的非流式文本协议。`base_url` 包含 `/v1` 等 API 前缀，后端追加 `/chat/completions`，发送 `model` 与 `messages`，读取 `choices[0].message.content`。运行时将把文档分块发送给配置的端点。

也可以使用下面的 `--env-file` 显式加载配置文件。配置优先级为 **命令行参数 → 当前进程已有环境变量 → 显式指定的 `.env` 文件**，程序不会自动搜索或加载 `.env`。`--api-key-env` 可指定保存密钥的变量名，不接受命令行密钥值。无需认证的本地端点可以不设置密钥。默认连接／读取／写入／连接池超时为 15/180/30/15 秒。`--timeout` 显式覆盖各阶段，`--read-timeout` 单独覆盖读取，范围均为 `(0,600]` 秒；不是整篇总时限。

内置入口默认仅一次生成 HTTP 尝试。显式提高上限时，仅安全连接失败和指定暂时性 HTTP 错误允许有限重试。读写及结果不确定的协议错误停止，不自动重发。鉴权和配置错误直接失败。CLI 对响应格式错误默认不重生成，显式 CLI／profile 的 1 可启用一次。程序不伪装成“未抽到数据”，也不自动退回演示。任一分块最终未恢复会使整个运行以非零退出码失败；不输出部分成功结果。新运行的检查点支持在新目录恢复已完成回答，详见[分块与恢复指南](docs/EXECUTION.md)。

### 可选的结构化输出

`extract --structured-output off|json_object|json_schema` 可覆盖当前配置。高级配置默认 `off`，模型预设默认 `json_object`；后者请求有效 JSON，不保证服务端执行任务 Schema。选择 `json_schema` 时，框架根据任务自动生成 JSON Schema，发送 `response_format.type: "json_schema"` 和 `strict: true`。Python 后端对应参数为 `OpenAICompatibleBackend(..., structured_output="json_schema")`。

严格模式要求 `values` 与 `quotes` 包含全部声明字段；任务标量类型约束 `values` 中的值，`quotes` 中的引文始终为字符串。未报告字段使用 null 值与空引文。可选空字段只产生一条信息项；任务要求的关键条件缺失仍进入复核。关闭结构化输出时，可继续省略未报告的可选字段。配置测量组后，严格 Schema 还要求返回该组的上下文及片段字段。

模型服务需支持相应格式。HTTP 400／422 会明确失败，不自动降级为非结构化请求，也不触发格式重试；安全连接失败及指定暂时性 HTTP 错误仍有有限重试；读写错误不自动重发。[Wenwen 官方结构化说明](https://wenwen-ai.apifox.cn/305807074e0) 给出了这类请求格式，但需针对实际模型渠道确认兼容性。即使端点接受参数，仍保留全部本地校验。

### 可选的一次格式重试

提示明确要求每个 JSON 对象的键名唯一、标量类型符合任务。任务字段名为 value 时，数值保存在 `values.value`，引文保存在 `quotes.value`；同一对象中重复键会被拒绝，即使值相同。解析器也拒绝非有限数值。日志分别用 `duplicate_key`、`non_finite_number` 标明错误路径，未声明的键名会隐藏；普通 JSON 语法错误保留行列位置。程序不会静默清理重复键或覆盖值。

CLI `extract --format-retries 0|1` 默认 `0`；显式 CLI／profile 设置为 `1` 时，本地 JSON、记录结构或任务字段类型校验失败才允许**只对失败分块重新生成一次**：原文、任务和输出模式不变，追加有长度限制的错误反馈。新响应仍要经过完整结构和证据检查；此前成功分块不重跑。Python 保持默认 `0`，启用写法为 `extract(text, task, backend=backend, format_retries=1)`。检查点 v3 保存失败反馈和已预留的重生成额度，恢复不会重置；中断或传输失败也不退还已预留机会。增加总调用预算不能复活耗尽的格式重生成额度。

开启后每个失败分块可能增加一次付费生成，每次生成仍沿用原有有限 HTTP 重试。HTTP 400／401、接口响应外层结构错误、拒答及被截断的输出不触发格式重试。引文缺失、找不到或多义仍进入复核，也不触发格式重试。第二次格式尝试仍失败就明确终止；生成候选不等于接受结果或事实核验。

### 显式加载 `.env` 并保存运行日志

使用旧版端点配置时，参照 `.env.example` 创建本地 `.env`，填入以下地址、模型和密钥。前面的模型选择快捷入口只需要服务商专用密钥，以及可选的专用地址：

```dotenv
ATTESTEXTRACT_BASE_URL=https://your-provider.example/v1
ATTESTEXTRACT_MODEL=your-model-name
ATTESTEXTRACT_API_KEY=replace-with-your-key
```

文件支持 `KEY=value`、可选的 `export` 前缀和带引号的值，只作为数据解析，不执行 shell 命令。密钥保存在本地，不加入提交。已有环境变量优先于文件；切换模型服务时，需清除或更新终端中残留的旧变量。

从工程根目录运行下面的 PowerShell 命令，可将研究结果和日志保存在 Git 工程之外：

```powershell
$runDir = "..\research\attestextract\runs\procurement\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
attestextract extract --env-file .env --structured-output json_schema --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output "$runDir\extraction.json" --report "$runDir\review.md" --log-dir "$runDir\diagnostics"
```

不填 `--output` 时，正式 CLI 在输入所在目录的 `outputs/<时间-随机标识>/` 保存；如果文件直接位于 `inputs/`（不区分大小写），则在与 `inputs/` 同级的 `outputs/` 中保存，保存 `result.json`、`report.md` 和 `diagnostics/`。显式指定 `--output` 时，仍只有传入 `--log-dir` 才保存诊断文件；该目录必须为空或尚不存在。两种方式默认都显示简洁进度，可用 `--progress quiet` 静默。完整默认值、无模型调用的 `--check` 和验收方法见 [CLI 体验说明](docs/CLI_EXPERIENCE.md)。

| 日志文件 | 用途 |
| --- | --- |
| `events.jsonl` | 结构化流程事件，按模块、分块查看进度。 |
| `manifest.json` | 运行状态、版本、运行摘要、产物清单及说明用途的 `artifact_roles`。 |
| `document.json` | 输入原文、文档标识、哈希和来源元数据。 |
| `task.json` | 实际使用且已通过验证的抽取任务。 |
| `backend.json` | 去敏后的后端配置，不含端点或密钥值。 |
| `chunk_plan.json` | 分块计划，包括分块 ID、大小和原文区间。 |
| `chunks/{chunk_id}.json` | 分块文本及对应的原文位置映射。 |
| `requests/{chunk_id}.request.json` | 去敏后的实际 HTTP 请求体，含 `model`、`messages` 和启用时的 `response_format`；无 HTTP 的 Replay／自定义后端保存 `ExtractionRequest`，并在清单／事件中标明请求种类。不含 HTTP 头或端点。 |
| `prompts/{chunk_id}.system.txt` | 可直接阅读的系统提示。 |
| `prompts/{chunk_id}.task.json` | 该分块提示中的任务配置。 |
| `prompts/{chunk_id}.source.txt` | 该分块提示中的原文。 |
| `responses/{chunk_id}.txt` | 模型原始响应文本，便于定位格式或抽取问题。 |
| `responses/{chunk_id}.meta.json` | 仅保存模型、用量、请求尝试次数和耗时等响应元数据。 |
| `http/{chunk_id}.attempt-{n}.error.json` | 单次 HTTP 失败的安全详情：出错层、分块、尝试次数、HTTP 状态、请求 ID 及可取得的服务商错误字段。 |
| `validation/{chunk_id}.errors.json` | 验证阶段、错误总数及最多 50 条安全详情：字段路径、错误代码、预期类型、实际类型和说明，不含被拒绝的输入值。 |
| `candidates/{chunk_id}.json` | 该分块的候选及结构／证据检查。 |
| `result.json` | 运行成功后的完整抽取结果。 |
| `error.json` | 运行失败后的安全摘要；`http_diagnostics` 汇总 HTTP 失败，`http_details` 关联其相对路径。格式验证失败继续关联验证详情及错误总数。 |

新版日志使用 `trace_schema_version: "0.3"`，不修改已有日志。首次尝试保留上表文件名；格式重试使用 `{chunk_id}.format-2`，例如 `requests/chunk-0001.format-2.request.json`、`responses/chunk-0001.format-2.txt`，以及被拒绝时的 `validation/chunk-0001.format-2.errors.json`。其 HTTP 错误路径为 `http/chunk-0001.format-2.attempt-{n}.error.json`。两次请求和原始响应分别保存，`.meta.json` 只包含元信息；候选的分块 ID 不变。`format_retry` 事件记录 `retry`、`succeeded` 或 `failed`，`format_attempt` 表示生成次数，与 HTTP `attempt` 区分。成功结果的响应数、请求尝试数和用量包含被拒绝及恢复后的响应。

失败运行可能留下此前已处理分块的文件，供排查使用，不代表部分结果已成功。即使已去除凭据和后端敏感信息，日志仍可能包含**整篇论文、任务说明和模型输出**。请像上述示例一样放在仓库之外，不提交到 GitHub；对外分享前先检查内容。日志只对应当前已实现的抽取流程，尚未实现或模拟 Jev、Stage2.5／Stage3 决策模块。

`extract` 将 JSON 写入可选的 `--output` 文件；省略时自动建立本次运行的结果目录。增加 `--report review.md` 可从同一次成功结果生成 Markdown 审阅报告，不额外调用模型。报告展示候选、字段值、逐字引文、原文位置、证据状态和复核原因，便于直接阅读；它不会接受候选或证明事实正确，JSON 仍是供程序处理的结果。报告会隐藏已配置的已知 API 密钥；抽取失败时不生成成功报告。结果和报告路径必须互不相同，也不能覆盖输入、任务或 `.env`，且都需位于 `--log-dir` 之外。

开启日志后，关键阶段开始、完成和重试会实时显示在标准错误，写入提示和错误摘要也发往标准错误；字段级细节保存在 `events.jsonl`，模型响应和跟踪内容不逐条打印到标准输出。原有 `demo`、`d1` 在不传 `--output` 时仍将 JSON 写入标准输出；中间结果可查看各分块文件。

排查 HTTP 错误时，先读 `error.json`，再沿 `http_details` 找到对应尝试，结合出错层、分块、尝试次数、HTTP 状态和可取得的请求 ID，查看服务商返回的 `error.code`、`type`、`message`、`status`、`param`。应用侧的兼容性提示是单独推测，不能当作服务商明确给出的原因。日志仅保留字段白名单，并做脱敏和限长，不保存完整 HTTP 错误体或响应头。没有可用原因时标为 `provider_detail_unavailable`；这些信息不能保证揭示服务商内部根因，也不能回补旧版 400 错误日志中未保存的内容。

Dev5 加强格式提示和错误定位，并提供明确开启的格式重试。离线测试只能证明这些机制按约定工作，不能证明真实模型的格式合格率或抽取准确率。未接入 Jev 也不意味着可以接受不存在的证据。

### 抽取完整文章，并集中保存每次结果

将完整 UTF-8 `.md`／`.txt` 文章传给 `--input`，程序自动分块，不需要手动拆文献或逐段提交。PDF 需先由外部工具转换。建议在 Git 仓库之外，将可重复使用的论文、任务配置与运行产物分开：

```text
../research/attestextract/
  inputs/paper.md
  tasks/paper.task.json
  runs/paper/<timestamp>/
    extraction.json
    review.md
    diagnostics/
  RUN_HISTORY.md
```

先把已有论文和任务配置放到选定位置，再从工程根目录执行以下 PowerShell 命令。它会使用 `.env` **调用真实模型并产生相应用量**；`--format-retries 1` 明确允许前述额外生成。尚未验证端点兼容性时使用 `off`。每次按文章名和时间新建目录，结果文件统一命名。

```powershell
python -m attestextract --version
$researchRoot = (Resolve-Path "..\research\attestextract" -ErrorAction Stop).Path
$paper = (Resolve-Path (Join-Path $researchRoot "inputs\paper.md") -ErrorAction Stop).Path
$task = (Resolve-Path (Join-Path $researchRoot "tasks\paper.task.json") -ErrorAction Stop).Path
$paperStem = [IO.Path]::GetFileNameWithoutExtension($paper)
$runDir = Join-Path $researchRoot "runs\$paperStem\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$output = Join-Path $runDir "extraction.json"
$report = Join-Path $runDir "review.md"
$logs = Join-Path $runDir "diagnostics"
python -m attestextract extract --env-file .env --structured-output off --format-retries 1 --input "$paper" --task "$task" --output "$output" --report "$report" --log-dir "$logs"
$extractExitCode = $LASTEXITCODE
Get-Content "$logs\manifest.json" -Raw -Encoding UTF8
$events = @(Get-Content "$logs\events.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$events | Where-Object { $_.stage -eq "format_retry" -or ($_.stage -eq "validation" -and $_.event -eq "rejected") } | Format-Table stage,event,chunk_id,format_attempt,path
if ($extractExitCode -eq 0) {
    $result = Get-Content "$output" -Raw -Encoding UTF8 | ConvertFrom-Json
    $result.run | Format-List
    Write-Output "请打开候选审阅报告：$report"
}
```

使用 Markdown 预览打开 `review.md` 查看易读结果；程序处理用 `extraction.json`，排查问题用 `diagnostics/`。在诊断目录内结合 `validation/*.errors.json` 和对应的 `responses/*.txt` 定位错误。首次错误保留、随后出现 `format_retry/succeeded`，表示新响应通过格式校验，不表示原始 JSON 被修改。整轮成功需同时满足退出码 `0`、清单状态 `succeeded`、生成最终文件。空结果成功不能证明没有漏抽，包含复核项的报告也不是已接受的事实清单。

如需先降低试验用量，可以用保留完整主体、条件和表头的原文片段。片段有新的文档标识，证据位置从片段开头计数，不能当作全文偏移。观察重试效果时，用相同输入、任务和模型分别设置 `--format-retries 0`、`1`，每次新建运行目录。分别比较首次格式失败、重试恢复分块、用量及字段值／引文质量；失败运行只统计实际尝试的分块。可在预算内对固定的小组片段重复测试，一对运行不能证明稳定提升，也不能单独衡量跨版本的提示词改进。

`RUN_HISTORY.md` 是建议手工维护的本地试验摘要，不是 CLI 自动维护的文件。删除旧运行目录前，先记录各次版本、输入／任务标识、模型、结果状态、失败或恢复原因及保留结果的位置，并把仍需使用的论文和任务配置独立保存。后续审计仍需要的原始证据应保留，摘要无法重建已删除的响应。清理旧产物属于明确执行的本地整理步骤，`extract` 不会自动删除历史运行。

## 任务、证据和扩展接口

JSON 任务包含 `name`、可选 `description` 和 `record_types`。每种记录声明 `fields`，字段类型为 `string`、`integer`、`number` 或 `boolean`，并可声明说明和 `required`。类型约束作用于模型回答 `values` 中的对应字段；`quotes` 中对应字段必须是字符串，布尔值不能当作整数。`identity_fields`、`condition_fields` 引用已声明的字段；缺少这些字段会进入复核。完整配置见打包示例。

提示会根据当前任务的记录类型、全部字段和标量类型构造完整合成示例，用于展示输出约定，不是论文证据，也不能把示例直接复制为抽取结果。模型输出通过 `values` 与 `quotes` 保留每个字段的值和逐字引文；引文必须保留 OCR 错误、空格、Unicode 符号和 LaTeX 写法。例如原文为 `$10^{-3}$`，不能把引文改写成 `0.001`，也不能默默纠正 OCR。标量值可表达原文报告的数值，但引文仍须是原文子串；在 JSON 字符串中，LaTeX 的字面反斜杠应按 JSON 规则写成 `\\`。

Python API：

```python
from attestextract import extract, load_task
from attestextract.backends import OpenAICompatibleBackend

task = load_task("src/attestextract/examples/ccus.task.json")
backend = OpenAICompatibleBackend(base_url="http://localhost:8000/v1", model="your-local-model")
result = extract("样品A在25 °C时的CO2吸附压力为12 bar。", task, backend=backend)
print(result.model_dump_json(indent=2))
```

- 输入支持 UTF-8 TXT、Markdown 正文和简单管道表格；默认按 6,000 字符及每批最多 12 行表格分块。单个表头或表格行保持完整，可能超过字符目标。后续表格批次重复携带原表头并保留原文位置。PDF 需先用 MinerU 等外部工具转换。
- 每个字段保留值、逐字引文、原文位置和检查结果。位置为 Python 解码字符串的 Unicode 字符偏移，采用左闭右开区间；不是 UTF-8 字节数或 UTF-16 单元数。唯一匹配时可用 `text[start:end]` 取回引文。
- 缺失、找不到或多处匹配的引文进入复核，不猜测位置。只合并值和证据位置完全相同的候选；不同位置或条件下的同值观测继续保留。未知字段、未知记录类型、错误标量类型和无效 JSON 明确失败。
- 新输出的 `schema_version` 为 `0.3`，包含任务、源文档摘要、候选、复核项、待确认关联及运行信息。读取仍兼容旧 `0.1/0.2`；旧程序读取新格式需要升级。`review[].severity` 区分 `review` 与 `info`，统计复核错误时只计前者。新增测量证据和可选空字段身份规则可能改变候选 ID，不应盲目复用旧审阅决定。原文定位通过只说明引文存在，**不等于语义支持性核验或事实验证**。
- `ExtractionBackend` 负责生成；`ReplayBackend` 仅显式回放。`DecisionBackend`、`DecisionTask`、`DecisionResult` 和 `validate_decision` 为后续决策预留边界，只允许选择已有候选、拒绝或转复核。当前抽取流程尚未调用决策后端。

## 文档、审阅与正式结果的保存约定

`extract(text, task, backend=...)` 保留原接口，也接受 `Document`；其输出仍然是候选结果。正式档案通过单独的 `create_archive` 建立：

```python
from attestextract import Document, create_archive, extract, load_archive, save_archive

document = Document.from_text("合成原文。", source_name="source.txt")
# task、backend 按上面的真实抽取示例配置。
result = extract(document, task, backend=backend)
archive = create_archive(document, result)
assert archive.records == []  # 引文定位通过不能自动接受候选。
save_archive(archive, "pending-archive.json")
restored = load_archive("pending-archive.json")
```

实际审阅后，使用 `CandidateReview(candidate_id=..., status="accepted", reviewer=..., rationale=...)` 记录决定，再调用 `create_archive(document, result, reviews=[...])`。只有明确接受且必填字段、证据检查均通过的候选才生成正式记录。也可以直接用 `from attestextract.d1_examples import load_d1_example` 和 `load_d1_example("attribute")` 查看带合成标签的完整例子。

| 代码对象 | 保存内容与规则 |
| --- | --- |
| `Document` | 精确解码原文、文档 ID、SHA-256、字符数、来源名、正文／Markdown 表示及表示版本；`DocumentMetadata` 可选保存题名、作者、年份、DOI、来源 URI。 |
| `TaskSpec` | 任务名、记录类型、标量字段、字段说明和身份／条件字段引用。 |
| `Candidate` / `EvidenceRef` | 候选 ID、字段值、逐字引文、字符位置或未定位状态、分块引用和结构检查。 |
| `CandidateReview` | 候选 ID、接受／拒绝／待复核／未审定状态、审阅者、理由、可选标注引用及来源类别。人工或样例的非“未审定”决定必须填写审阅者和理由。 |
| `FinalRecord` | 接受候选的原字段和证据，以及文档、来源候选和记录 ID；不覆盖候选值。 |
| `ExtractionArchive` | `archive_schema_version: "0.1"`，包含 `document`、`extraction`、`reviews`、`records`；候选结果保留自身格式版本与运行信息。 |

未显式提供审阅时，结构正常的候选标为 `unreviewed`，存在未解决检查的候选标为 `needs_review`；系统检查不会设为 `accepted`。`origin: "human"` 表示调用者记录的人工归因，不提供身份认证；`synthetic_fixture` 表示合成标签，`system` 表示系统补齐的待审状态。“正式／接受记录”指有明确审阅决定的记录，不表示事实已获独立证明。改值必须形成新候选并重新审阅，不能直接改正式记录而保留旧候选引用。

`save_document`／`load_document` 和 `save_archive`／`load_archive` 读写 UTF-8 JSON，并在重读时验证原文哈希、字符数、ID、引用及证据。文档 ID 对应精确文本表示；元数据不构成来源真实性认证。读取文件可用 `Path(path).read_bytes().decode("utf-8")` 保留原有换行；证据偏移始终指向这份精确字符串。PDF 转换后的 Markdown 是独立文本表示，位置不代表 PDF 页码或坐标框。

## 开发和后续方向

```bash
python -m pytest
python -m build
```

CI 在 Windows／Ubuntu、Python 3.11／3.13 下运行，公共测试仅使用合成固定响应和模拟 HTTP，不调用付费模型；测试逐字段对比三类手工预期答案，并验证档案保存、重读和错误引用。另外在源码目录外的新环境安装 wheel，运行两个原有示例和三类 D1 档案。格式回归测试覆盖非法输出、有限重新生成、保留各次尝试和后续证据检查。真实端点兼容性与抽取效果需针对所用模型和数据单独评估。

后续优先接入 Jev 类决策模型，再增加冲突判别、支持性核验和旧 CCUS 格式兼容。首版尚不替代旧工作流完整的 Stage2.5／Stage3，也不承诺零幻觉或模型效果。

公开内容仅包含新框架、文档、测试和合成示例，不包含论文原文、专家标注、专利材料、研究目录、运行输出或凭据。相关项目：[LangExtract](https://github.com/google/langextract)、已有的 [JevExtract](https://github.com/gabazureus/jevextract)。本项目独立实现，没有直接包含它们的代码。

当前未添加许可证，也未发布到 PyPI；公开可读不代表已授予开源许可。


## dev17 离线验收

在仓库根目录运行以下命令，只使用合成材料和历史回放，不读取密钥、不调用模型。每次使用新输出目录。

```powershell
$acceptanceOutput = Join-Path (Split-Path (Get-Location) -Parent) ('attestextract-check-' + [guid]::NewGuid().ToString('N'))
& '.\.venv\Scripts\python.exe' -X utf8 '.\scripts\protocol_evidence_smoke.py' --output-dir $acceptanceOutput
if ($LASTEXITCODE -ne 0) { throw '离线验收失败，请检查终端错误。' }
Get-Content -LiteralPath (Join-Path $acceptanceOutput 'summary.json') -Raw -Encoding utf8
```

该验收检查范围两端单位、共享单位、混合单位复核、独立数值、档案往返，以及旧格式必须显式离线选择。新诊断的 `output-protocol.json` 记录回答协议，`normalized/` 保存转换后的内部结构，`responses/` 继续保存原始回答。它不证明未来模型生成一定成功。

旧回答回放使用 `ReplayBackend(responses, output_protocol="native-v1")`；默认回放和真实调用均为 flat-v1。不要把历史原始回答当作新回答，也不要自动修补失败 JSON。升级版本后新建运行，旧 dev16 检查点须使用原版本继续。

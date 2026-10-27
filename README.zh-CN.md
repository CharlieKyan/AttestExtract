# AttestExtract

按任务模式从正文和表格抽取候选数据，保留原文证据，并预留可替换的决策后端。

**早期原型 · `0.1.0.dev3` · Python 3.11+**

本项目从 CCUS 抽取工作流的经验出发，独立实现通用小内核，不依赖 LangExtract，也不导入原有 CCUS 工程。当前实现字段验证、候选保留、原文位置回溯和复核清单；可另行保存包含精确原文、显式审阅决定和接受记录的档案。**Jev 尚未接入**；当前检查不能证明抽取结果在语义上得到原文支持或事实正确。

[English README and API details](README.md)

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

PowerShell 配置示例：

```powershell
$env:ATTESTEXTRACT_BASE_URL = "https://your-provider.example/v1"
$env:ATTESTEXTRACT_MODEL = "your-model-name"
$env:ATTESTEXTRACT_API_KEY = "your-key"
attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output extraction-results.json
```

模型端点需兼容 OpenAI Chat Completions 的非流式文本协议。`base_url` 包含 `/v1` 等 API 前缀，后端追加 `/chat/completions`，发送 `model` 与 `messages`，读取 `choices[0].message.content`。运行时将把文档分块发送给配置的端点。

也可以使用下面的 `--env-file` 显式加载配置文件。配置优先级为 **命令行参数 → 当前进程已有环境变量 → 显式指定的 `.env` 文件**，程序不会自动搜索或加载 `.env`。`--api-key-env` 可指定保存密钥的变量名，不接受命令行密钥值。无需认证的本地端点可以不设置密钥。`--timeout` 为每次请求超时，默认 60 秒，范围为大于 0 且不超过 60 秒。

网络传输失败和暂时性 HTTP 错误默认最多尝试三次；鉴权、配置及格式错误明确失败，不伪装成“未抽到数据”，也不自动退回演示。任一分块失败会使整个运行失败，避免把部分输出当作完整成功结果。

### 可选的结构化输出

`extract --structured-output off|json_schema` 默认值为 `off`。选择 `json_schema` 时，框架根据任务自动生成 JSON Schema，发送 `response_format.type: "json_schema"` 和 `strict: true`。Python 对应参数为 `OpenAICompatibleBackend(..., structured_output="json_schema")`。

严格模式要求每个已声明字段都带 `{ "value": ..., "quote": ... }` 包装；任务中的标量类型限制的是内部 **`value`**，`quote` 始终是字符串。未报告的字段返回 `value: null`、`quote: ""`，按既有规则进入复核，不会自动接受。关闭结构化输出时，可继续省略未报告的可选字段。

模型服务需支持相应格式。HTTP 400／422 会明确失败，不自动降级为非结构化请求，也不发起修复重试；网络及暂时性 HTTP 错误仍使用原有有限重试。[Wenwen 官方结构化说明](https://wenwen-ai.apifox.cn/305807074e0) 给出了这类请求格式，但实际 Gemini 渠道尚未实测，不能据此保证兼容。

### 显式加载 `.env` 并保存运行日志

参照 `.env.example` 创建本地 `.env`；已有文件时直接编辑，填入实际端点、模型和密钥：

```dotenv
ATTESTEXTRACT_BASE_URL=https://your-provider.example/v1
ATTESTEXTRACT_MODEL=your-model-name
ATTESTEXTRACT_API_KEY=replace-with-your-key
```

文件支持 `KEY=value`、可选的 `export` 前缀和带引号的值，只作为数据解析，不执行 shell 命令。密钥保存在本地，不加入提交。已有环境变量优先于文件；切换模型服务时，需清除或更新终端中残留的旧变量。

从工程根目录运行下面的 PowerShell 命令，可将研究结果和日志保存在 Git 工程之外：

```powershell
New-Item -ItemType Directory -Force ..\research\attestextract_runs | Out-Null
attestextract extract --env-file .env --structured-output json_schema --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output ..\research\attestextract_runs\paper-001-schema.json --log-dir ..\research\attestextract_runs\logs\paper-001-schema
```

`--log-dir` 开启文件日志，并在标准错误实时显示关键阶段进度。指定目录必须为空或尚不存在，不会覆盖已有运行；每次运行请使用新目录。不传该参数时，照常抽取且不生成这些跟踪文件。

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
| `validation/{chunk_id}.errors.json` | 验证阶段、错误总数及最多 50 条安全详情：字段路径、错误代码、预期类型、实际类型和说明，不含被拒绝的输入值。 |
| `candidates/{chunk_id}.json` | 该分块的候选及结构／证据检查。 |
| `result.json` | 运行成功后的完整抽取结果。 |
| `error.json` | 运行失败后的安全错误摘要；适用时关联验证详情路径及错误总数。 |

新版日志使用 `trace_schema_version: "0.2"`，不修改已有 `0.1` 日志。新运行改用明确的 `.request.json`、`.meta.json` 文件名，不再生成旧版含义模糊的同名 JSON；模型原文在 `.txt`，元信息在 `.meta.json`。

失败运行可能留下此前已处理分块的文件，供排查使用，不代表部分结果已成功。即使已去除凭据和后端敏感信息，日志仍可能包含**整篇论文、任务说明和模型输出**。请像上述示例一样放在仓库之外，不提交到 GitHub；对外分享前先检查内容。日志只对应当前已实现的抽取流程，尚未实现或模拟 Jev、Stage2.5／Stage3 决策模块。

`extract` 将 JSON 写入必填的 `--output` 文件。开启日志后，关键阶段开始、完成和重试会实时显示在标准错误，写入提示和错误摘要也发往标准错误；字段级细节保存在 `events.jsonl`，模型响应和跟踪内容不逐条打印到标准输出。原有 `demo`、`d1` 在不传 `--output` 时仍将 JSON 写入标准输出；中间结果可查看各分块文件。

## 任务、证据和扩展接口

JSON 任务包含 `name`、可选 `description` 和 `record_types`。每种记录声明 `fields`，字段类型为 `string`、`integer`、`number` 或 `boolean`，并可声明说明和 `required`。类型约束作用于字段内部的 `value`，不是 `{value, quote}` 包装对象；`quote` 必须是字符串，布尔值不能当作整数。`identity_fields`、`condition_fields` 引用已声明的字段；缺少这些字段会进入复核。完整配置见打包示例。

提示会根据当前任务的记录类型、全部字段和标量类型构造完整合成示例，用于展示输出约定，不是论文证据，也不能把示例直接复制为抽取结果。每个输出字段都保留 `value` 与逐字 `quote`；引文必须保留 OCR 错误、空格、Unicode 符号和 LaTeX 写法。例如原文为 `$10^{-3}$`，不能把引文改写成 `0.001`，也不能默默纠正 OCR。标量值可表达原文报告的数值，但引文仍须是原文子串；在 JSON 字符串中，LaTeX 的字面反斜杠应按 JSON 规则写成 `\\`。

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
- 输出为版本化 JSON，包含任务、源文档摘要、候选、复核项及运行信息。当前 `schema_version` 为 `0.1`。原文定位通过只说明引文存在，**不等于语义支持性核验或事实验证**。
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

CI 在 Windows／Ubuntu、Python 3.11／3.13 下运行，公共测试仅使用合成固定响应和模拟 HTTP，不调用付费模型；测试逐字段对比三类手工预期答案，并验证档案保存、重读和错误引用。另外在源码目录外的新环境安装 wheel，运行两个原有示例和三类 D1 档案。本次 dev3 先进行离线验收，已有真实日志只在本地回放，不自动再次调用真实模型。真实端点兼容性与抽取效果需针对所用模型和数据单独评估。

后续优先接入 Jev 类决策模型，再增加冲突判别、支持性核验和旧 CCUS 格式兼容。首版尚不替代旧工作流完整的 Stage2.5／Stage3，也不承诺零幻觉或模型效果。

公开内容仅包含新框架、文档、测试和合成示例，不包含论文原文、专家标注、专利材料、研究目录、运行输出或凭据。相关项目：[LangExtract](https://github.com/google/langextract)、已有的 [JevExtract](https://github.com/gabazureus/jevextract)。本项目独立实现，没有直接包含它们的代码。

当前未添加许可证，也未发布到 PyPI；公开可读不代表已授予开源许可。

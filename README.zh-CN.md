# AttestExtract

按任务模式从正文和表格抽取候选数据，保留原文证据，并预留可替换的决策后端。

**早期原型 · `0.1.0.dev0` · Python 3.11+**

本项目从 CCUS 抽取工作流的经验出发，独立实现通用小内核，不依赖 LangExtract，也不导入原有 CCUS 工程。当前实现字段验证、候选保留、原文位置回溯和复核清单。**Jev 尚未接入**；当前检查不能证明抽取结果在语义上得到原文支持或事实正确。

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

`--base-url`、`--model` 优先于对应环境变量；`--api-key-env` 可指定保存密钥的环境变量名，不接受命令行密钥值。无需认证的本地端点可以不设置密钥。`.env.example` 仅作变量模板，程序不会自动加载 `.env`。`--timeout` 为每次请求超时，默认 60 秒，范围为大于 0 且不超过 60 秒。

网络传输失败和暂时性 HTTP 错误默认最多尝试三次；鉴权、配置及格式错误明确失败，不伪装成“未抽到数据”，也不自动退回演示。任一分块失败会使整个运行失败，避免把部分输出当作完整成功结果。

## 任务、证据和扩展接口

JSON 任务包含 `name`、可选 `description` 和 `record_types`。每种记录声明 `fields`，字段类型为 `string`、`integer`、`number` 或 `boolean`，并可声明说明和 `required`。`identity_fields`、`condition_fields` 引用已声明的字段；缺少这些字段会进入复核。完整配置见两个打包示例。

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

## 开发和后续方向

```bash
python -m pytest
python -m build
```

CI 在 Windows／Ubuntu、Python 3.11／3.13 下运行，使用固定响应和模拟 HTTP，不调用付费模型；另外在源码目录外的新环境安装 wheel，运行两个打包示例。真实端点兼容性与抽取效果需针对所用模型和数据单独评估。

后续优先接入 Jev 类决策模型，再增加冲突判别、支持性核验和旧 CCUS 格式兼容。首版尚不替代旧工作流完整的 Stage2.5／Stage3，也不承诺零幻觉或模型效果。

公开内容仅包含新框架、文档、测试和合成示例，不包含论文原文、专家标注、专利材料、研究目录、运行输出或凭据。相关项目：[LangExtract](https://github.com/google/langextract)、已有的 [JevExtract](https://github.com/gabazureus/jevextract)。本项目独立实现，没有直接包含它们的代码。

当前未添加许可证，也未发布到 PyPI；公开可读不代表已授予开源许可。

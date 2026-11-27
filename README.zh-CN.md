<p align="center">
  <img src="docs/assets/niceextract-logo.png" alt="NiceExtract：将科研论文整理成可核查的数据集。" width="560">
</p>

# NiceExtract

[English](README.md) · [快速开始](#快速开始) · [任务与标准示例](#定义任务与编写标准示例) · [配置 API](#配置-api-与切换模型) · [多篇文献与 CSV（dev60 预览）](#多篇文献共用一份字段定义)

## 简介

NiceExtract 是一个用语言模型从论文正文和表格中提取观测记录的 Python 框架。你定义要收集的字段，通过命令行或 Python 接口获得带原文引文的候选记录。即将发布的 dev60 增加了组表功能。指定每行代表的对象后，还可以把每篇文献的观测整理成 CSV，再从单元格查回原值、单位和出处。

## 快速开始

下载并解压 [NiceExtract 预览版源码 ZIP](https://github.com/CharlieKyan/NiceExtract/archive/5b19b8f25b987771beca119c0b7251dd199c6123.zip)。这份较早的源码可以运行下文的演示和抽取示例。dev60 的 CSV 组表功能将在后续版本发布。在解压后含有 `pyproject.toml` 的目录，用 Python 3.11 或更高版本运行以下命令。

```sh
python -m pip install .
niceextract demo --output result.json
```

打开 `result.json`，查看两份合成材料的结果。在科研示例里，样品 A 在 25 °C 和 50 °C 下的压力都是 12 bar。结果保留两条候选，每条都能查到对应温度和原句。这个演示在本地回放预设回答，无需 API 密钥。[示例原文](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/ccus.txt) · [科研示例 JSON](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/examples/ccus-result.json)

## 为什么选择 NiceExtract？

- **在多篇文献中沿用同一套字段定义。** 在 dev60 预览中，你可以声明哪些字段收文字、哪些收数值，再用 `prepare-task` 保存给后续文献使用。材料牌号 `003` 可以保留前导零。浓度字段则可区分 `5 ppm`、`2–5 ppm` 和 `≥5 ppm`。
- **回到原文核对每个字段。** 正文字段保留引文，成功匹配时还会给出原文位置。缺少引文、同一句话能匹配多处等情况会被标出，方便你有针对性地复核。
- **正文和表格可以一起读。** 一次运行就能处理正文及其中的 Markdown 或 HTML 表格。两类结果保存在同一份 JSON 中，表格观测还可通过单元格引用追溯到原表的行和列。
- **保留理解数值所需的上下文。** 核对一个报告值时，可以一起查看它描述的对象、单位和适用条件。即使数值相同，在不同条件下得到的观测也可以分别记录。
- **长文档中断后可以接着处理。** 你可以设置模型调用次数上限并保存运行记录。恢复时可以复用已完成的片段，省去重复调用。[运行与恢复指南](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/EXECUTION.md)

## 从原文定位到科研记录

LangExtract 可以在文档中高亮抽取到的文本。在它的 v1.7.2 数据结构中，一条抽取记录可以带一个字符区间、一个对齐状态，以及一组属性。读论文时，一个数值、它的单位和适用条件却可能分别来自不同位置。NiceExtract 可以为这些字段分别保留依据，让你逐项核对。[LangExtract 数据结构](https://github.com/google/langextract/blob/v1.7.2/langextract/core/data.py#L64-L92)

| 复核时会遇到的问题 | 在 NiceExtract 中怎么查 |
| --- | --- |
| **原文有几处相同的表述，这个字段引用了哪一处？** | 正文字段各自记录引文的匹配状态。引文在检索范围内出现多次时，可以查看各个可能的位置。如果只在折叠空白或对齐部分标点后才找到匹配，结果会同时保留模型引文和原文片段，供你对照。[引文定位说明](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/D4_EVIDENCE.md) |
| **这个表头的单位适用于哪一列数值？** | 沿着单元格引用，可以从数值查到为它引用的表头、单位或脚注。结果分别记录引文是否找到、行列位置是否符合局部规则。遇到跨列表头时，可以回到原表判断这个单位是否适用。[表格检查说明](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/TABLE_EXTRACTION.md) |
| **轴承的温度能填进整台电机的温度栏吗？** | 以 dev60 的组表功能为例。假设原文写“电机 M4 的轴承 B7 达到 60 °C”。字段允许收录部件温度时，可以在 M4 下查到这条观测，直接测量对象仍是 B7。若字段要求整机平均温度，且该读数已判定不适用，主表相应格留空，明细仍保留轴承的 60 °C 及引文。指定 `--table-subject` 后，可在设定的查证预算内回读原文，归属有歧义时保留待核状态。[预览 CSV 组表流程](#多篇文献共用一份字段定义) |
| **如果模型的回答只写到表格中间，怎么办？** | 当服务明确报告输出达到长度上限时，框架可以把整张表按行拆成几次抽取，必要时再细分到单元格。每次重试仍能看到原表及表头，所有重试共用本次运行的调用预算。截断的回答不会进入抽取结果。[表格恢复说明](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/TABLE_EXTRACTION.md#预算格式错误与恢复) |

## 浏览示例

下面三种任务都使用合成材料，可直接在浏览器里对照阅读。

| 你想收集什么？ | 可以看哪里？ |
| --- | --- |
| 测量结果和实验条件 | 对照[研究片段](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/ccus.txt)和[抽取结果 JSON](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/examples/ccus-result.json)，看同一样品的两次测量如何分别记录。 |
| 物品、数量和交付要求 | 对照[采购需求](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/procurement.md)和[任务定义](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/procurement.task.json)，看换一组字段后如何处理另一类文档。 |
| 原文明示的关系 | 对照[原句](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/d1/relation/source.txt)和[人工编写的预期记录](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/d1/relation/expected.json)，看如何表达谁供应什么。 |

## 抽取自己的文档

在同一目录准备两个文件。`paper.md` 放这次要处理的原文，保存为 UTF-8 文本或 Markdown。`task.json` 保存本研究的抽取规则，换一篇论文时也可以继续用。如果从 PDF 开始，请先转换，并核对需要的表格是否保留为 Markdown 或 HTML 表格。[表格抽取指南](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/TABLE_EXTRACTION.md)

### 定义任务与编写标准示例

抽取观测时，最少只需填两项。在 `prompt_description` 里写明要抽什么、哪些情况应分别记录。在 `field_list` 里列出**所有目标字段**，为每个字段写一句定义，说明什么情况算入。如果某个字段容易混淆，可以加上 `examples`，用一段短文和你期望看到的标注来说明取舍。

下面是一份可直接保存为 `task.json` 的完整示例。任务要收集两个指标，并示范同一试样出现两个质量值时，哪一个应计入干质量。

```json
{
  "prompt_description": "收集文中明确报告的试样属性。不同条件下的测量分别记录，保留原单位。",
  "field_list": {
    "dry_mass": "试样干燥后测得的质量。排除干燥前的测量值。",
    "thickness": "原文报告的试样厚度，保留测量时的条件。"
  },
  "examples": [
    {
      "text": "试样 B 干燥前的质量为 5.1 g。干燥后，试样 B 的质量为 4.2 g。干燥温度为 60 °C。",
      "extractions": [
        {
          "field": "dry_mass",
          "entity": "试样 B",
          "value": "4.2",
          "unit": "g",
          "conditions": "60 °C",
          "quote": "干燥后，试样 B 的质量为 4.2 g。",
          "quotes": {"conditions": "干燥温度为 60 °C。"}
        }
      ]
    }
  ]
}
```

**怎样给自己的任务写标准示例**

- `text` 放一小段示例原文。`extractions` 放你认为**从这段原文中**应该抽出的观测，每条观测单独写一项。上例只有 4.2 g 符合干质量定义。示例没报告厚度，但 `thickness` 仍是抽取目标。要增加示例，可以在 `examples` 数组中追加一组 `text` 和 `extractions`。如果字段定义已足够清楚，也可以省略整个 `examples`。
- `field` 填 `field_list` 中的字段名。`value` 填非空字符串，数值或范围也加双引号，单位按原文保留。原文没写的对象（`entity`）、单位（`unit`）或条件（`conditions`），可以省略或填 `null`。
- `quote` 从这条示例自己的 `text` 中连续、逐字摘录。上例第二句支持质量和单位，第三句才给出温度，所以在 `quotes` 中单独为 `conditions` 引用第三句。正式抽取时，模型参考示例了解你的口径，结果中的引文会回到本次输入的 `paper.md` 中核对。

安装后，在 `task.json` 所在目录运行下面的命令，可以先检查任务，无需模型或 API 密钥。

```sh
python -c "from niceextract import load_task; print(list(load_task('task.json').record_types))"
```

上例应输出 `['dry_mass', 'thickness']`。如果标注用了未声明的字段，或引文在示例原文中找不到，错误会指出对应条目。引文的含义是否支持这条标注，仍由你判断。若需要记录“主体—关系—客体”等自定义结构，可参考关系任务。[另一份质量与长度任务](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/observations.task.json) · [关系任务](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/src/niceextract/examples/d1/relation/task.json) · [任务编写指南](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/SIMPLE_TASKS.md)

## 配置 API 与切换模型

正式抽取会将文档内容和任务中的示例发送给选定的模型服务商，可能产生费用。

### 在内置模型之间切换

先为打算使用的服务配置密钥。可以设置系统环境变量，也可以将密钥放在私有的 `.env` 文件里。以下示例显式读取该文件。如果只用环境变量，命令行省略 `--env-file .env`，Python 省略 `env_file=".env"`。同名变量以当前进程环境中的值为准。

| 预览版内置连接 | 模型标识 | 密钥变量 |
| --- | --- | --- |
| DeepSeek Chat | `deepseek:deepseek-flash` | `DEEPSEEK_API_KEY` |
| DeepSeek Responses | `deepseek-responses:deepseek-flash` | `DEEPSEEK_API_KEY` |
| 通义千问 Chat（北京） | `qwen:qwen3.8-27b` 或 `qwen:qwen-plus` | `DASHSCOPE_API_KEY` 或 `QWEN_API_KEY` |

如果两家模型都要试，可以在私有 `.env` 中分别填写凭据。下面的值是占位符，按需替换。

```dotenv
DEEPSEEK_API_KEY=your-deepseek-key
DASHSCOPE_API_KEY=your-dashscope-key
```

运行 `niceextract models` 可以查看当前安装版本内置的预设。下面两条命令任选一条。要对照两个模型的表现，只需使用同一篇文档和同一份任务，分别保存结果。

```sh
niceextract extract --input paper.md --task task.json --env-file .env --model-id deepseek:deepseek-flash --output deepseek.json
niceextract extract --input paper.md --task task.json --env-file .env --model-id qwen:qwen3.8-27b --output qwen.json
```

想先看运行计划，可以在命令末尾加上 `--check`，离线查看配置、文档分块和调用预算。切换模型时新开一次抽取。恢复已有运行则沿用创建检查点时的模型和任务。

### 在 Python 中复用同一份任务

在脚本或 Notebook 中，修改 `model` 这一项即可切换预设。下面的示例读取与命令行相同的原文和任务，以 JSON 打印结果。

```python
from pathlib import Path
from niceextract import extract, load_task

model = "deepseek:deepseek-flash"  # or "qwen:qwen3.8-27b"
result = extract(
    text=Path("paper.md").read_bytes().decode("utf-8-sig"),
    task=load_task("task.json"),
    model=model,
    env_file=".env",
)
print(result.model_dump_json(indent=2))
```

### 接入实验室网关或复用团队配置

**自定义兼容接口。** 如果服务支持非流式、使用 Bearer 密钥的 OpenAI 兼容 Chat Completions 接口，填写服务的 API 基础地址和原始模型名即可。将下面的示例地址和模型名替换为服务商给出的值，并将该端点的密钥保存在 `LAB_MODEL_KEY` 变量中。在 Python 里，同一个 `extract()` 接受 `base_url`、原始模型名 `model` 和 `api_key_env="LAB_MODEL_KEY"`。[兼容接口接入说明](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/MINIMAL_API.md)

```sh
niceextract extract --input paper.md --task task.json --env-file .env --base-url https://gateway.example/v1 --model lab-model --api-key-env LAB_MODEL_KEY --output gateway.json
```

**团队共用配置。** 如果要共用端点、超时或生成参数，可以把它们存成 TOML 文件里的命名配置，运行时按名字选择。密钥各自保存在环境变量或私有 `.env` 中。下面示例里的 `qwen` 配置读取 `DASHSCOPE_API_KEY`。[配置文件示例](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/examples/backends.toml) · [模型与配置指南](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/BACKENDS.md)

```sh
niceextract extract --input paper.md --task task.json --env-file .env --config examples/backends.toml --profile qwen --output profile-result.json
```

## 多篇文献共用一份字段定义

**dev60 功能预览。** 以下命令适用于 dev60 发布后的版本。届时可在上文的任务顶层添加 `field_types`。像下面这样把两个字段都声明为数值型，就可以离线准备共用任务。数值型默认接受单值、范围和单侧界限。

```json
{
  "field_types": {
    "dry_mass": {"kind": "numeric"},
    "thickness": {"kind": "numeric"}
  }
}
```

```sh
niceextract prepare-task --task task.json --output-dir prepared
```

随后对每篇文献调用已配置的模型进行抽取。下面的命令把“单个试样”指定为主表的行主体。

```sh
niceextract extract --input paper.md --task prepared/task.json --env-file .env --model-id deepseek:deepseek-flash --table-subject "单个试样" --output-dir run
```

每篇文献用同一份 `prepared/task.json` 单独抽取，结果各存一个目录。如果有字段希望交给模型判断，可省略这些字段的类型，并在 `prepare-task` 时提供 `--model-id` 和相应的凭据。准备结果记录类型来自用户还是模型。复用它可以省去后续文献的字段准备调用。

在上例的 `run/dataset/` 中，打开 `main.csv` 查看各字段的值、依据和引文状态。要核对某个单元格，在 `lineage.json` 的 `rows` 数组中找到与 CSV 数据行同序的一行，从该行的 `cells[字段名]` 取得观测编号。再用编号到 `observations.csv` 或 `dataset.json` 查看原值、单位、直接对象与证据。“引文已定位”说明能在原文中找到这段文字。它是否支持这项判断，仍可回到原文核对。

对于仍有争议的观测，`niceextract review-dataset` 可将本地模型的复核意见写入独立目录。使用时需另行准备可信的 Intern-Decision-4B 检查点、配套推理代码和运行环境。

## 核对抽取结果

打开已保存的抽取 JSON，可以查看候选记录、证据和警告。如果想用更方便阅读的格式逐条核对，可以在预览版源码 ZIP 的解压目录中，对随包提供的单篇示例结果运行以下命令。它会离线生成一份审阅材料，写入新目录。

```sh
niceextract export-review --input examples/ccus-result.json --output-dir review-example
```

打开 `review-example/annotation.md`，即可一起阅读字段、原值、条件和原文引句。同一份内容也会保存为 `annotation.json`。换成自己的 `deepseek.json` 或 `qwen.json` 时，请选一个新的输出目录。[结果复核指南](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/docs/EXPERT_REVIEW.md)

## 许可证

上文提供的预览版源码使用 Apache License 2.0。版权所有 © 2026 Yuzhi Kuang。[Apache License 2.0](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/LICENSE) · [署名声明](https://github.com/CharlieKyan/NiceExtract/blob/5b19b8f25b987771beca119c0b7251dd199c6123/NOTICE)

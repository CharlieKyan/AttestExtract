# NICEExtract

[English](README.md)

## 简介

NICEExtract 是一个开源 Python 框架，帮助不同领域的研究者从文献中提取结构化信息。你可以按自己的研究问题定义要收集的内容。NICEExtract 借助语言模型从正文和表格中提取候选记录，将结果和原文依据一起保存为 JSON，方便逐项核对。

## 为什么选择 NICEExtract？

- **用同一套研究口径读多篇文献。** 用自己的话定义要收集的字段。遇到有特定含义的术语，可以给一个示例说明取舍，再把同一份任务用于下一篇文献。
- **回到原文核对每个字段。** 正文字段保留引文，成功匹配时还会给出原文位置。缺少引文、同一句话能匹配多处等情况会被标出，方便你有针对性地复核。
- **正文和表格可以一起读。** 一次运行就能处理正文及其中的 Markdown 或 HTML 表格。两类结果保存在同一份 JSON 中，表格观测还可通过单元格引用追溯到原表的行和列。
- **保留理解数值所需的上下文。** 核对一个报告值时，可以一起查看它描述的对象、单位和适用条件。即使数值相同，在不同条件下得到的观测也可以分别记录。
- **长文档中断后可以接着处理。** 你可以设置模型调用次数上限并保存运行记录。恢复时可以复用已完成的片段，省去重复调用。[运行与恢复指南](docs/EXECUTION.md)

## 快速开始

准备 Python 3.11 或更高版本，下载并解压[预览版源码](https://github.com/CharlieKyan/AttestExtract/archive/refs/tags/v0.1.0a1-preview.1.zip)，在包含 `pyproject.toml` 的目录运行以下命令。

```sh
python -m pip install .
niceextract demo --output result.json
```

打开 `result.json`，即可查看两份合成材料的候选记录及对应引文。演示在本地回放预设回答，无需 API 密钥。

## 浏览示例

下面三种任务都使用合成材料，可直接在浏览器里对照阅读。

| 你想收集什么？ | 可以看哪里？ |
| --- | --- |
| 测量结果和实验条件 | 对照[研究片段](src/niceextract/examples/ccus.txt)和[抽取结果 JSON](examples/ccus-result.json)，看同一样品的两次测量如何分别记录。 |
| 物品、数量和交付要求 | 对照[采购需求](src/niceextract/examples/procurement.md)和[任务定义](src/niceextract/examples/procurement.task.json)，看换一组字段后如何处理另一类文档。 |
| 原文明示的关系 | 对照[原句](src/niceextract/examples/d1/relation/source.txt)和[人工编写的预期记录](src/niceextract/examples/d1/relation/expected.json)，看如何表达谁供应什么。 |

## 抽取自己的文档

1. **准备原文。** 将文档保存为 UTF-8 文本或 Markdown，例如 `paper.md`。如果从 PDF 开始，请先转换，并核对需要的表格是否保留为 Markdown 或 HTML 表格。[表格抽取指南](docs/TABLE_EXTRACTION.md)
2. **定义任务。** 将示例任务另存为 `task.json`。根据自己的研究问题重写任务说明和字段定义，再调整或移除可选的标注示例。[示例任务](src/niceextract/examples/observations.task.json) · [任务编写指南](docs/SIMPLE_TASKS.md)
3. **连接模型。** 以下命令使用 DeepSeek，请先在环境中设置 `DEEPSEEK_API_KEY`。这一步会把文档发送给模型服务商，可能产生费用。[其他模型的连接方式](docs/BACKENDS.md)

```sh
niceextract extract --model-id deepseek:deepseek-flash --input paper.md --task task.json --output my-result.json
```

打开 `my-result.json`，把候选记录与对应的原文引句或表格单元格逐条核对，再用于自己的研究。[结果复核指南](docs/EXPERT_REVIEW.md)

## 许可证

版权所有 © 2026 Yuzhi Kuang。[Apache License 2.0](LICENSE) · [署名声明](NOTICE)

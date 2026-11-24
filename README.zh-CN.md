# NICEExtract

[English](README.md)

NICEExtract 是面向科研文献的 Python 抽取框架。你可以用自己的话定义想收集的指标，让框架从正文和表格里提取候选观测。结果以 JSON 保存，每条观测都能记录报告值、单位、测量条件和原文依据，便于逐项核对。

## 一个样品，两种温度

下面的合成片段记录了同一样品在两个温度下的吸附压力。虽然压力数值相同，抽取结果保留了两条观测，每条都附有各自的原句。你可以直接在浏览器里对照阅读。[查看原文](src/niceextract/examples/ccus.txt) · [打开示例 JSON](examples/ccus-result.json)

| 样品 | 压力 | 温度 | 对应原句 |
| --- | --- | --- | --- |
| 样品A | 12 bar | 25 °C | 样品A在25 °C时的CO2吸附压力为12 bar。 |
| 样品A | 12 bar | 50 °C | 样品A在50 °C时的CO2吸附压力为12 bar。 |

这两处引文均已在原文中定位。遇到证据缺失或引文位置有歧义时，框架会提示复核。即使找到了原句，数值是否属于目标样品、测量条件是否符合研究要求，仍需研究者结合原文判断。

## 用在你的研究中

- **按研究口径收集数据。** 你可以把“质量”限定为实际接受测试的试样质量。不同论文对同一术语用法不一时，还可以附一条示例说明取舍。
- **追溯表格里的数值。** 表格观测保留单元格引用，正文观测保留引文，两者保存在同一个 JSON 文件里，便于对照原表与正文核查。
- **从中断处接着读长文。** 保存运行目录后，可以复用已完成的抽取。继续前可先查看恢复计划，设定模型调用预算。[运行与恢复指南](docs/EXECUTION.md)

## 在本地运行示例

准备 Python 3.11 或更高版本，下载并解压[本次预览的源码包](https://github.com/CharlieKyan/AttestExtract/archive/refs/tags/v0.1.0a1-preview.1.zip)。在包含 `pyproject.toml` 的目录运行以下两条命令。演示离线回放预先编写的合成回答，无需 API 密钥。

```sh
python -m pip install .
niceextract demo --example ccus --output result.json
```

## 抽取自己的材料

将论文或片段保存为 UTF-8 文本或 Markdown，例如 `paper.md`。如果原文是 PDF，请先转换，并核对表格是否完整保留为 Markdown 或 HTML 表格。[表格抽取指南](docs/TABLE_EXTRACTION.md)

将示例任务另存为 `task.json`，换成你想收集的字段，为每个字段写一句定义，再改写或移除可选的标注示例。[示例任务](src/niceextract/examples/observations.task.json) · [任务定义与 Python 示例](docs/SIMPLE_TASKS.md)

下面以 DeepSeek 预设为例。先在环境变量中设置 `DEEPSEEK_API_KEY`。这次运行会将输入发送到模型服务，服务商可能计费。[其他模型的连接方式](docs/BACKENDS.md)

```sh
niceextract extract --model-id deepseek:deepseek-flash --input paper.md --task task.json --output my-result.json
```

打开 `my-result.json`，即可结合原文引句或表格单元格引用，逐条核对候选观测。[结果复核指南](docs/EXPERT_REVIEW.md)

## 许可证

版权所有 © 2026 Yuzhi Kuang。[Apache License 2.0](LICENSE) · [署名声明](NOTICE)

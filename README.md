# NICEExtract

[简体中文](README.zh-CN.md)

NICEExtract is a Python framework for extracting observations from research papers. Describe the quantities you want to collect. The framework reads the prose and tables and returns candidate observations as JSON, with reported values, units, measurement conditions and evidence you can check against the source.

## One sample, two temperatures

This synthetic excerpt reports the same adsorption pressure for sample A at two temperatures. The extraction keeps the two observations separate, each with its own quotation. Compare the source and the saved JSON directly in your browser. [Read the excerpt](src/niceextract/examples/ccus.txt) · [Open the example JSON](examples/ccus-result.json)

| Sample | Pressure | Temperature | Quotation from the source |
| --- | --- | --- | --- |
| 样品A (sample A) | 12 bar | 25 °C | 样品A在25 °C时的CO2吸附压力为12 bar。 |
| 样品A (sample A) | 12 bar | 50 °C | 样品A在50 °C时的CO2吸附压力为12 bar。 |

Both quotations were located in the excerpt. NICEExtract flags missing or ambiguous evidence for review. A match tells you where the words occur. Whether the value describes the intended sample under the right conditions is a scientific judgment.

## What you can do with your papers

- **Use your study’s definitions.** Specify which measurements count, such as the mass of a tested specimen. Add an example when authors use the same term in different ways.
- **Trace a value through a table.** Review measurements with their cell references alongside observations from the surrounding prose. Both are available in the same JSON file.
- **Pick up a long extraction where it stopped.** Save a run directory to keep completed work. You can inspect the recovery plan and set a model-call budget before continuing. [Run and resume guide](docs/EXECUTION.md)

## Run the example

With Python 3.11 or newer installed, download and unzip the [source for this preview](https://github.com/CharlieKyan/AttestExtract/archive/refs/tags/v0.1.0a1-preview.1.zip). In the directory containing `pyproject.toml`, run the two commands below. The demo replays a fixed synthetic response offline and needs no API key.

```sh
python -m pip install .
niceextract demo --example ccus --output result.json
```

## Use your own material

Save a paper or excerpt as UTF-8 text or Markdown, for example `paper.md`. If you start from a PDF, convert it first and check that any tables survived as Markdown or HTML tables. [Table extraction guide](docs/TABLE_EXTRACTION.md)

Copy the sample task to `task.json` and replace its fields with yours. Give each field a short definition, then update or remove the optional worked example. [Sample task](src/niceextract/examples/observations.task.json) · [Task definitions and Python examples](docs/SIMPLE_TASKS.md)

The command below uses the DeepSeek preset. Set `DEEPSEEK_API_KEY` in your environment first. This run sends the input to the model provider and may incur charges. [Other model connections](docs/BACKENDS.md)

```sh
niceextract extract --model-id deepseek:deepseek-flash --input paper.md --task task.json --output my-result.json
```

Open `my-result.json` to inspect the observations alongside their quotations or table cell references. [Reviewing results](docs/EXPERT_REVIEW.md)

## License

Copyright 2026 Yuzhi Kuang. [Apache License 2.0](LICENSE) · [Attribution](NOTICE)

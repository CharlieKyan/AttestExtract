# NICEExtract

[简体中文](README.zh-CN.md)

## Introduction

NICEExtract is an open-source Python framework for extracting structured information from research documents. You define what to collect. NICEExtract uses a language model to find it in prose and tables, then returns candidate records as JSON with evidence you can check against the source.

## Why NICEExtract?

- **Use the same definitions across papers.** Describe the fields your study needs in your own words. Add a worked example when a term has a specific meaning in your field, then reuse that task on the next paper.
- **Check a result against the source.** For text fields, inspect the quotation and its location when a match is found. Missing quotations and ambiguous matches are flagged so you can decide where to look more closely.
- **Read prose and tables together.** Process a document containing Markdown or HTML tables in one run. The JSON includes both sets of results, with cell references for tracing table observations back to their rows and columns.
- **Keep the context that gives a value meaning.** Review a reported value alongside the object it describes, its unit and its conditions. Observations made under different conditions can be kept separate even when their values are identical.
- **Resume work on a long document.** Set a limit on model calls and save the run. If it stops partway through, you can reuse completed chunks when you resume. [Run and resume guide (in Chinese)](docs/EXECUTION.md)

## Quick start

Download and unzip the [preview source](https://github.com/CharlieKyan/AttestExtract/archive/refs/tags/v0.1.0a1-preview.1.zip). With Python 3.11 or newer, run these commands in the directory containing `pyproject.toml`.

```sh
python -m pip install .
niceextract demo --output result.json
```

Open `result.json` to see the candidates and their quotations for two synthetic documents. The demo replays fixed responses and runs offline without an API key.

## Explore the examples

These synthetic examples show how the choice of task changes what you extract. You can read them directly in your browser.

| What do you want to collect? | What to look at |
| --- | --- |
| Measurements and their conditions | Compare a [research excerpt](src/niceextract/examples/ccus.txt) with its [saved JSON result](examples/ccus-result.json) to see how two measurements of the same sample are recorded. |
| Items, quantities and delivery terms | Read a [purchase request](src/niceextract/examples/procurement.md) alongside its [task definition](src/niceextract/examples/procurement.task.json) to see how the same framework handles a different vocabulary. |
| A relationship stated in the text | Compare a [source sentence](src/niceextract/examples/d1/relation/source.txt) with its [handwritten expected record](src/niceextract/examples/d1/relation/expected.json) to see who supplies what. |

## Use your own documents

1. **Prepare the source.** Save the document as UTF-8 text or Markdown, for example `paper.md`. Convert PDFs first and check that the tables you need are preserved as Markdown or HTML tables. [Table extraction guide (in Chinese)](docs/TABLE_EXTRACTION.md)
2. **Define your task.** Copy the sample task to `task.json`. Rewrite the task description and field definitions for your study, then adapt or remove the optional example. [Sample task](src/niceextract/examples/observations.task.json) · [Task guide (in Chinese)](docs/SIMPLE_TASKS.md)
3. **Connect a model.** For the command below, set `DEEPSEEK_API_KEY` in your environment. This sends your document to the provider and may incur charges. [Other model connections](docs/BACKENDS.md)

```sh
niceextract extract --model-id deepseek:deepseek-flash --input paper.md --task task.json --output my-result.json
```

Open `my-result.json` and compare the candidates with the cited passages or table cells before using them in your research. [Guide to reviewing results (in Chinese)](docs/EXPERT_REVIEW.md)

## License

Copyright 2026 Yuzhi Kuang. [Apache License 2.0](LICENSE) · [Attribution](NOTICE)

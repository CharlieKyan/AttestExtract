# NiceExtract

[简体中文](README.zh-CN.md) · [Quick start](#quick-start) · [Tasks and examples](#define-a-task-and-label-examples) · [API setup and model switching](#configure-the-api-and-switch-models)

## Introduction

NiceExtract is an open-source Python framework for extracting structured information from research documents. You define what to collect. NiceExtract uses a language model to find it in prose and tables, then returns candidate records as JSON with evidence you can check against the source.

## Why NiceExtract?

- **Use the same definitions across papers.** Describe the fields your study needs in your own words. Add a worked example when a term has a specific meaning in your field, then reuse that task on the next paper.
- **Check a result against the source.** For text fields, inspect the quotation and its location when a match is found. Missing quotations and ambiguous matches are flagged so you can decide where to look more closely.
- **Read prose and tables together.** Process a document containing Markdown or HTML tables in one run. The JSON includes both sets of results, with cell references for tracing table observations back to their rows and columns.
- **Keep the context that gives a value meaning.** Review a reported value alongside the object it describes, its unit and its conditions. Observations made under different conditions can be kept separate even when their values are identical.
- **Resume work on a long document.** Set a limit on model calls and save the run. If it stops partway through, you can reuse completed chunks when you resume. [Run and resume guide (in Chinese)](docs/EXECUTION.md)

## From a text match to a research record

LangExtract can highlight where an extraction appears in a document. In its v1.7.2 data model, each extraction has one optional character span, an alignment status and a dictionary of attributes. In a paper, the value, its unit and the conditions under which it was measured can each come from a different place. NiceExtract lets you examine the evidence for those fields separately. [LangExtract data model](https://github.com/google/langextract/blob/v1.7.2/langextract/core/data.py#L64-L92)

| Question during review | What you can inspect in NiceExtract |
| --- | --- |
| **Which occurrence supports this field?** | Each quoted text field has its own match status. When the wording occurs more than once in the search range, you can inspect the possible locations. If spacing or punctuation differs, a normalized match keeps both the model's quotation and the original source text for review. [Evidence guide (in Chinese)](docs/D4_EVIDENCE.md) |
| **Does this unit apply to this table value?** | Follow cell references from a reported value to the header, unit or footnote cited for it. The result distinguishes finding the quoted text from checking its row or column relationship. That gives you the original cells to consult when a header spans several columns. [Table checks (in Chinese)](docs/TABLE_EXTRACTION.md) |
| **Which object does this observation describe? Does it fit my definition?** | For field-list tasks, `table_subject` in Python (`--table-subject` in the CLI) enables an optional verification stage with its own model-call budget. The model can search and reread passages in the same document to assess ownership and field fit separately. A total mass may clearly belong to a specimen and still fall outside your definition of *dry mass*. Ownership can remain unknown, and the original value is preserved for review. [How verification works](docs/analysis-tables.md#what-the-loop-does) |
| **What if the model's output cuts off halfway through a table?** | When the service reports an output-length limit, NiceExtract can split the work by row and, if needed, by cell. Each retry still sees the original table and its headers. The retries share the run's call budget, and the incomplete response stays out of the results. [Table recovery (in Chinese)](docs/TABLE_EXTRACTION.md#预算格式错误与恢复) |

## Quick start

Open the [preview branch](https://github.com/CharlieKyan/AttestExtract/tree/release/0.1.0a1) on GitHub and choose **Code → Download ZIP**. Unzip the source and run these commands with Python 3.11 or newer in the directory containing `pyproject.toml`.

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

Keep two files side by side. `paper.md` contains the document you want to process, saved as UTF-8 text or Markdown. `task.json` describes what to collect and can be reused across papers. If you start from a PDF, convert it first and check that the tables you need are preserved as Markdown or HTML tables. [Table extraction guide (in Chinese)](docs/TABLE_EXTRACTION.md)

### Define a task and label examples

For observations, a task needs just two entries. In `prompt_description`, say what to extract and when to keep observations separate. In `field_list`, give **every target field** a name and a definition that tells a reader what counts. Add `examples` when a short passage and your expected labels can resolve a tricky choice.

Save this complete example as `task.json`. It asks for two properties and shows how to distinguish dry mass from a second mass reported for the same specimen.

```json
{
  "prompt_description": "Collect explicitly reported specimen properties. Keep measurements under different conditions separate and preserve the reported units.",
  "field_list": {
    "dry_mass": "Mass measured after drying. Exclude measurements taken before drying.",
    "thickness": "Reported specimen thickness at the stated measurement conditions."
  },
  "examples": [
    {
      "text": "Sample B weighed 5.1 g before drying. After drying, Sample B weighed 4.2 g. The drying temperature was 60 °C.",
      "extractions": [
        {
          "field": "dry_mass",
          "entity": "Sample B",
          "value": "4.2",
          "unit": "g",
          "conditions": "60 °C",
          "quote": "After drying, Sample B weighed 4.2 g.",
          "quotes": {"conditions": "The drying temperature was 60 °C."}
        }
      ]
    }
  ]
}
```

**How to label your own example**

- Put a short passage in `text` and the observations you would accept from **that passage** in `extractions`, one entry per observation. Here only 4.2 g meets the dry-mass definition. Thickness remains a target even though this passage gives no thickness measurement. To add another example, append another pair of `text` and `extractions` to the `examples` array. You can also omit `examples` entirely.
- Set `field` to a key from `field_list`. Write `value` as a nonempty string, even for numbers or ranges. Keep the original unit. You can omit `entity`, `unit` or `conditions`, or set them to `null`, when the passage leaves them unstated.
- Copy a continuous passage from the example's own `text` into `quote`. Here the second sentence supports the value and unit, while the third supplies the temperature. The `conditions` entry inside `quotes` cites that third sentence separately. When you run on `paper.md`, the examples guide the model and its output quotations are checked against `paper.md`.

After installation you can check the task locally without a model or API key. Run this in the directory containing `task.json`.

```sh
python -c "from niceextract import load_task; print(list(load_task('task.json').record_types))"
```

For the example above, this prints `['dry_mass', 'thickness']`. An undeclared field or a quotation the loader cannot find raises an error identifying the offending entry. You still decide whether the passage supports your label. For custom record shapes such as subject–relation–object, see the relation task. [Another task covering mass and length](src/niceextract/examples/observations.task.json) · [Relation task](src/niceextract/examples/d1/relation/task.json) · [Task guide (in Chinese)](docs/SIMPLE_TASKS.md)

## Configure the API and switch models

A live run sends document content and task examples to the chosen provider and may incur charges.

### Switch between built-in presets

Set the key for the provider you want to use. You can either set an environment variable or put it in a private `.env` file. The examples below explicitly load that file. If you use environment variables only, omit `--env-file .env` in the CLI and `env_file=".env"` in Python. An existing environment variable takes precedence over the file.

| Connection in this preview | Model ID | Key variable |
| --- | --- | --- |
| DeepSeek Chat | `deepseek:deepseek-flash` | `DEEPSEEK_API_KEY` |
| DeepSeek Responses | `deepseek-responses:deepseek-flash` | `DEEPSEEK_API_KEY` |
| Qwen Chat (Beijing) | `qwen:qwen3.8-27b` or `qwen:qwen-plus` | `DASHSCOPE_API_KEY` or `QWEN_API_KEY` |

A private `.env` file can hold both providers' keys. Replace the placeholders for the providers you use.

```dotenv
DEEPSEEK_API_KEY=your-deepseek-key
DASHSCOPE_API_KEY=your-dashscope-key
```

Run `niceextract models` to list the presets included in your installation. Pick either command below. The input and task are the same, and separate JSON files make it easy to compare the candidates if you try both models.

```sh
niceextract extract --input paper.md --task task.json --env-file .env --model-id deepseek:deepseek-flash --output deepseek.json
niceextract extract --input paper.md --task task.json --env-file .env --model-id qwen:qwen3.8-27b --output qwen.json
```

Append `--check` to preview the configuration, document chunks and call budget offline. To compare models, start a fresh run for each. A saved checkpoint belongs to the model and task that created it.

### Use the same task in Python

In a notebook or script, change `model` to switch presets. This reads the same two files used by the CLI and prints the result as JSON.

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

### Connect a lab gateway or reuse a team configuration

**Compatible API.** For a service offering a non-streaming OpenAI-compatible Chat Completions API with a bearer key, use its base URL and its own model name. Replace the example address and model below, and set `LAB_MODEL_KEY` to the key for that endpoint. In Python, pass `base_url`, the raw `model` name and `api_key_env="LAB_MODEL_KEY"` to the same `extract()` call. [Connection guide (in Chinese)](docs/MINIMAL_API.md)

```sh
niceextract extract --input paper.md --task task.json --env-file .env --base-url https://gateway.example/v1 --model lab-model --api-key-env LAB_MODEL_KEY --output gateway.json
```

**Named configurations.** If your team shares endpoints, timeouts or generation settings, store them as named profiles in a TOML file. Select a profile by name and keep its key in your environment or private `.env`. The bundled `qwen` profile reads `DASHSCOPE_API_KEY`. [Example configuration](examples/backends.toml) · [Provider and profile guide](docs/BACKENDS.md)

```sh
niceextract extract --input paper.md --task task.json --env-file .env --config examples/backends.toml --profile qwen --output profile-result.json
```

## Review a result

Open a saved extraction JSON to inspect its candidates, evidence and warnings. For a more readable view, try the following on the included single-document result. It runs offline and writes the review to a new directory.

```sh
niceextract export-review --input examples/ccus-result.json --output-dir review-example
```

Open `review-example/annotation.md` to read fields alongside their values, conditions and quotations. Use `annotation.json` for the same review in structured form. You can run this command on your own `deepseek.json` or `qwen.json` with a fresh output directory. [Guide to reviewing results (in Chinese)](docs/EXPERT_REVIEW.md)

## License

Copyright 2026 Yuzhi Kuang. [Apache License 2.0](LICENSE) · [Attribution](NOTICE)

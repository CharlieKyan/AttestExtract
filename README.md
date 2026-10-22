# AttestExtract

Schema-driven extraction from text and tables, with source-linked evidence and pluggable decision backends.

**Early prototype · `0.1.0.dev0` · Python 3.11+**

AttestExtract is an independent, domain-configurable extraction core. It grew out of work on a CCUS extraction workflow, but its task schema and runtime contain no CCUS-specific rules and do not depend on LangExtract. The first version produces typed candidates, exact source references, and a review queue. **Jev integration is planned and is not implemented.**

[中文说明](README.zh-CN.md)

## Try the offline demonstration

```bash
python -m pip install "git+https://github.com/CharlieKyan/AttestExtract.git"
attestextract demo --output demo-results.json
```

`demo` replays **fixed, synthetic model responses**. It performs no inference, needs no API key, and does not demonstrate model accuracy. It runs the same validation and evidence-mapping pipeline used for live extraction. The bundled examples contain two Chinese CCUS observations and three English purchase lines from prose and a Markdown table.

```bash
attestextract demo --example ccus
attestextract demo --example procurement
python -m attestextract --version
```

With `--example all` (the default), the output is `{"examples": {"ccus": ..., "procurement": ...}}`. A single example returns one extraction result. Without `--output`, demo JSON is printed to stdout; replay notices go to stderr.

## Extract with a model

Clone the repository to use the example input and task files:

```bash
git clone https://github.com/CharlieKyan/AttestExtract.git
cd AttestExtract
python -m pip install -e ".[dev]"
```

Configure an OpenAI-compatible **chat completions** endpoint. The base URL includes the API prefix, such as `https://your-provider.example/v1`; the adapter appends `/chat/completions`. It sends non-streaming text requests. The endpoint must accept `model` and `messages` and return text in `choices[0].message.content`.

```powershell
$env:ATTESTEXTRACT_BASE_URL = "https://your-provider.example/v1"
$env:ATTESTEXTRACT_MODEL = "your-model-name"
$env:ATTESTEXTRACT_API_KEY = "your-key"
attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output extraction-results.json
```

On Bash, use `export ATTESTEXTRACT_BASE_URL=...`, `export ATTESTEXTRACT_MODEL=...`, and `export ATTESTEXTRACT_API_KEY=...`. `.env.example` documents these variables; `.env` files are **not automatically loaded**. `--base-url` and `--model` override their environment variables. `--api-key-env OTHER_VARIABLE` reads a key from another environment variable; no command-line key value is accepted. A key may be omitted for a local endpoint that does not require authentication. `--timeout` accepts seconds greater than zero and at most 60, defaulting to 60 per attempt.

Live extraction sends document chunks to your configured endpoint. There is no automatic fallback to demo responses. Authentication failures, invalid configuration, invalid responses, and exhausted retries fail with a nonzero exit status. Transport failures and transient HTTP statuses are retried at most three times by default; schema/response errors and authentication failures are not retried. A failed chunk fails the run rather than returning a successful partial result.

## Python API and task format

```python
import os
from attestextract import extract, load_task
from attestextract.backends import OpenAICompatibleBackend

task = load_task("src/attestextract/examples/procurement.task.json")
backend = OpenAICompatibleBackend(
    base_url=os.environ["ATTESTEXTRACT_BASE_URL"],
    model=os.environ["ATTESTEXTRACT_MODEL"],
    api_key=os.environ.get("ATTESTEXTRACT_API_KEY"),
)
result = extract("Purchase request: 5 desk lamps at USD 24.50 each, delivery within 10 days.", task, backend=backend)
print(result.model_dump_json(indent=2))
```

A task declares record types, scalar fields, and optional identity/condition field lists. Names in these lists must refer to declared fields. Change the task and descriptions to change domains; no domain adapter is required.

```json
{
  "name": "measurements",
  "description": "Extract explicitly reported measurements without unit conversion.",
  "record_types": {
    "measurement": {
      "fields": {
        "subject": {"type": "string", "description": "Named subject", "required": true},
        "value": {"type": "number", "description": "Reported value", "required": true},
        "condition": {"type": "string", "description": "Reported measurement condition"}
      },
      "identity_fields": ["subject"],
      "condition_fields": ["condition"]
    }
  }
}
```

Types are `string`, `integer`, `number`, and `boolean`; booleans do not count as integers. The generated completion must follow this shape:

```json
{"records": [{"record_type": "measurement", "fields": {"subject": {"value": "Sample A", "quote": "Sample A measured 12 bar."}, "value": {"value": 12, "quote": "Sample A measured 12 bar."}}}]}
```

Only declared record types and fields are accepted. Invalid JSON, unknown fields, and wrong scalar types fail explicitly. Missing/null required, identity, or condition fields enter the review queue. Optional null values and empty records also require review. Omitting an optional field is allowed.

## Evidence, output, and limits

- Input is a UTF-8 `.txt` or `.md` document. Paragraphs and simple pipe tables are chunked, preserving original source segments; later table batches repeat their original headers. The default target is 6,000 characters and at most 12 rows per table batch. A header or row is atomic and may exceed the character target. Convert PDFs externally with a tool such as MinerU before extraction.
- Each candidate retains field values, quoted evidence, a stable ID, chunk IDs, and structural/evidence checks. Conditions remain ordinary schema fields. Half-open `start`/`end` offsets count Unicode characters in the decoded input string, not UTF-8 bytes or UTF-16 code units: `text[start:end] == quote` for uniquely matched evidence.
- Evidence must match exactly within the current chunk's original source segments. Missing, absent, or ambiguous quotes enter `review`; ambiguous matches have no guessed `start`/`end`. Only identical candidates grounded at the same source locations are deduplicated. Equal values from different locations or conditions remain separate.
- Result JSON has `schema_version: "0.1"`, `package_version`, `task`, `source`, `candidates`, `review`, and `run`. Run information includes backend, `live`/`replay` mode, model, counts, attempts, usage if reported, and elapsed time. A successful empty extraction has no candidates; failures raise exceptions instead.
- A matched quote proves that text occurs at a location. It **does not prove that the extracted value is supported or factually correct**. This version has no semantic support verification, factual adjudication, entity resolution, full PDF parsing, or complete replacement for the original CCUS workflow's Stage2.5/Stage3.

`ExtractionBackend` defines the generation boundary. `ReplayBackend` is available for explicit offline replay and tests. `DecisionBackend`, `DecisionTask`, and `DecisionResult` reserve a separate decision boundary: `validate_decision` allows selecting only a supplied candidate, rejecting, or requesting review. Decisions cannot invent a replacement candidate. The extraction pipeline does not yet call a decision backend.

## Development and roadmap

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

CI runs on Windows and Ubuntu with Python 3.11 and 3.13. Tests use fixed responses and mocked HTTP, never paid model requests. CI also installs the wheel in a clean environment outside the checkout and runs both packaged demos. End-to-end model quality and provider compatibility require separate evaluation against your chosen endpoint and corpus.

Next steps are a Jev-style bounded decision adapter, conflict handling and support verification, and compatibility outputs for the earlier CCUS workflow. These are plans, not implemented features or performance claims. The public prototype contains only new framework code and synthetic examples; it does not include research documents, annotations, patents, original workflow outputs, or credentials.

Related work: [LangExtract](https://github.com/google/langextract) and the existing [JevExtract](https://github.com/gabazureus/jevextract) project. AttestExtract is a separate early project and does not vendor either implementation.

No license has been added to this early repository. Public availability does not grant an open-source license. This version is not published to PyPI.

# AttestExtract

Schema-driven extraction from text and tables, with source-linked evidence and pluggable decision backends.

**Early prototype · `0.1.0.dev3` · Python 3.11+**

AttestExtract is an independent, domain-configurable extraction core. It grew out of work on a CCUS extraction workflow, but its task schema and runtime contain no CCUS-specific rules and do not depend on LangExtract. It produces typed candidates, exact source references, and a review queue. An optional archive preserves the exact document, explicit review decisions, and accepted records. **Jev integration is planned and is not implemented.**

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

## D1 document-to-record examples

```bash
attestextract d1 --output d1-archives.json
attestextract d1 --example attribute
attestextract d1 --example observation
attestextract d1 --example relation
```

These are **synthetic manual expected examples**, replaying fixed responses and explicit fixture review labels. They are not real expert validation, model inference, or an accuracy benchmark. Each case packages `source.txt`, `task.json`, `response.json`, `reviews.json`, and a hand-written `expected.json` under `src/attestextract/examples/d1/`.

| Case | Intended behavior | Candidates / accepted records |
| --- | --- | --- |
| `attribute` | Preserve a reported product power rating; reject a deliberately wrong candidate with an absent quotation | 2 / 1 |
| `observation` | Preserve two equal flow values under different temperature conditions | 2 / 2 |
| `relation` | Preserve an explicit subject–relation–object statement | 1 / 1 |

The relation case uses three declared string fields; it does not implement graph inference, entity resolution, or a general event ontology. `reviews.json` refers to candidates by zero-based `candidate_index` in replay order. Indices must be unique and in range; the loader resolves them to real candidate IDs. The fixture author supplies each review label and rationale with `origin: "synthetic_fixture"`. `expected.json` independently specifies counts, review states, and accepted field values with exact source quotations and offsets; it excludes generated IDs and run timing.

The D1 implementation covers a separate installable project, document/task/candidate/evidence/accepted-record contracts, all three manual cases, and validated JSON save/reload. `attestextract d1 --example all` (the default) outputs `{"examples": {"attribute": ..., "observation": ..., "relation": ...}}`; each value is an archive. A single case returns one archive. The earlier `demo` command retains its extraction-result output.

Private expert feedback informs which failure patterns to exercise. Original papers, expert comments, and machine outputs stay outside the public project. The public cases were written from scratch and do not reproduce those materials.

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

On Bash, use `export ATTESTEXTRACT_BASE_URL=...`, `export ATTESTEXTRACT_MODEL=...`, and `export ATTESTEXTRACT_API_KEY=...`. Alternatively, load a configuration file explicitly with `--env-file`, as below. Configuration precedence is **CLI arguments → existing process environment → the explicit `.env` file**. No `.env` file is loaded automatically. `--api-key-env OTHER_VARIABLE` selects the variable containing the key; no command-line key value is accepted. A key may be omitted for a local endpoint that does not require authentication. `--timeout` accepts seconds greater than zero and at most 60, defaulting to 60 per attempt.

Live extraction sends document chunks to your configured endpoint. There is no automatic fallback to demo responses. Authentication failures, invalid configuration, invalid responses, and exhausted retries fail with a nonzero exit status. Transport failures and transient HTTP statuses are retried at most three times by default; schema/response errors and authentication failures are not retried. A failed chunk fails the run rather than returning a successful partial result.

### Optional structured output

`extract --structured-output off|json_schema` defaults to `off`. With `json_schema`, AttestExtract generates the response schema from the task and sends `response_format` with `type: "json_schema"` and `strict: true`. The Python equivalent is `OpenAICompatibleBackend(..., structured_output="json_schema")`.

In strict mode, every declared field has a required `{ "value": ..., "quote": ... }` wrapper. The task's scalar type constrains the **inner `value`**, and `quote` remains a string. For an unreported field, return `value: null` and `quote: ""`; the existing review rules apply, and this does not create an accepted record. With structured output off, optional unreported fields may still be omitted.

The provider/model must support this schema format. HTTP 400/422 failures stop the run; AttestExtract does not silently fall back to unstructured output or issue a repair retry. Normal transient-network retry rules still apply. [Wenwen's structured-output documentation](https://wenwen-ai.apifox.cn/305807074e0) shows this request format; the actual Gemini route has not been tested, so provider documentation is not a compatibility guarantee.

### Explicit `.env` configuration and run logs

Create a local `.env` from `.env.example`, or edit your existing file, with the provider values:

```dotenv
ATTESTEXTRACT_BASE_URL=https://your-provider.example/v1
ATTESTEXTRACT_MODEL=your-model-name
ATTESTEXTRACT_API_KEY=replace-with-your-key
```

The file accepts `KEY=value` assignments, an optional `export` prefix, and quoted values. It is parsed as data; shell commands are not executed. Keep credentials in the local file and out of commits. An existing environment variable takes precedence over the file, so clear or update stale shell variables when switching providers.

From the repository root, this PowerShell example writes research outputs and detailed logs outside the Git project:

```powershell
New-Item -ItemType Directory -Force ..\research\attestextract_runs | Out-Null
attestextract extract --env-file .env --structured-output json_schema --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output ..\research\attestextract_runs\paper-001-schema.json --log-dir ..\research\attestextract_runs\logs\paper-001-schema
```

`--log-dir` enables file-based tracing and key stage progress on stderr. Its directory must be empty or not yet exist; an existing run is never overwritten. Choose a new directory for each run. Omit it for ordinary extraction without these trace files.

| Trace file | What it records |
| --- | --- |
| `events.jsonl` | Structured processing events for following progress by module/chunk. |
| `manifest.json` | Run status, versions, run summary, artifact list, and `artifact_roles` describing each artifact's purpose. |
| `document.json` | Input document text, identity, hash, and source metadata. |
| `task.json` | The effective, validated extraction task. |
| `backend.json` | Sanitized backend configuration, excluding endpoint and key values. |
| `chunk_plan.json` | Planned chunk IDs, sizes, and original source segments. |
| `chunks/{chunk_id}.json` | Chunk text and its mapping to the original source. |
| `requests/{chunk_id}.request.json` | Sanitized actual HTTP request body: `model`, `messages`, and `response_format` when enabled. Replay/custom backends without HTTP instead store their `ExtractionRequest`; request kind is identified in the manifest/events. No HTTP headers or endpoint are included. |
| `prompts/{chunk_id}.system.txt` | Directly readable system instructions. |
| `prompts/{chunk_id}.task.json` | The task supplied in this chunk's prompt. |
| `prompts/{chunk_id}.source.txt` | The source text supplied in this chunk's prompt. |
| `responses/{chunk_id}.txt` | Raw model response text for diagnosing parsing and extraction. |
| `responses/{chunk_id}.meta.json` | Response metadata only: model, usage, attempts, and elapsed time. |
| `validation/{chunk_id}.errors.json` | Validation phase, total error count, and up to 50 safe details with field paths, codes, expected types, actual types, and messages; no rejected input values. |
| `candidates/{chunk_id}.json` | Candidates and structural/evidence checks for the chunk. |
| `result.json` | Final extraction result after a successful run. |
| `error.json` | Safe failure summary, linking validation detail paths and total counts when applicable. |

New traces use `trace_schema_version: "0.2"`. Existing `0.1` log directories are not rewritten; new runs use the explicit `.request.json` and `.meta.json` names rather than the earlier ambiguous JSON names. The `.txt` response contains model text, while `.meta.json` contains its metadata.

A failed run can leave earlier chunk files for diagnosis; they are not a successful partial result. Trace files can contain **the full paper text, task instructions, and model output**, even when credentials and backend details are sanitized. Store them outside this repository, as above, and do not commit or publicly share them without reviewing their contents. These are logs of the implemented extraction pipeline; Jev and Stage2.5/Stage3 decision modules are not implemented or simulated by tracing.

`extract` writes JSON to its required `--output` file. With tracing enabled, key stages, completions, and retries appear on stderr in real time, alongside file-written notices and error summaries. Field-level details remain in `events.jsonl`; model responses and trace contents are not streamed to stdout. `demo` and `d1` still print JSON to stdout when `--output` is omitted. Inspect the per-chunk files for intermediate results.

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

A task declares record types, scalar fields, and optional identity/condition field lists. Names in these lists must refer to declared fields. Change the task and descriptions to change domains; no domain adapter is required. Prompts include a complete synthetic example tailored to the task's record types, fields, and scalar types. These generated examples explain the output contract; they are not evidence or records to copy from the input.

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

Types are `string`, `integer`, `number`, and `boolean`; booleans do not count as integers. These types apply to the inner `value`, not to its field wrapper; `quote` must always be a string. For the synthetic source `Sample A measured 12 bar at 20 °C.`, a complete completion for the task above is:

```json
{"records": [{"record_type": "measurement", "fields": {"subject": {"value": "Sample A", "quote": "Sample A measured 12 bar at 20 °C."}, "value": {"value": 12, "quote": "Sample A measured 12 bar at 20 °C."}, "condition": {"value": "20 °C", "quote": "Sample A measured 12 bar at 20 °C."}}}]}
```

Only declared record types and fields are accepted. Invalid JSON, unknown fields, and wrong scalar types fail explicitly. Missing/null required, identity, or condition fields enter the review queue. Optional null values and empty records also require review. Outside strict structured mode, omitting an optional field is allowed.

Evidence quotes must preserve the input **verbatim**, including OCR errors, spaces, Unicode symbols, and LaTeX notation. Do not replace a source expression such as `$10^{-3}$` with `0.001` inside `quote`, or silently fix OCR text. A typed field value can represent the reported scalar, but its quotation must remain the exact source substring. JSON string escaping is still required, for example `\\` for a literal LaTeX backslash.

## Document, review, and archive contracts

`extract(text, task, backend=...)` remains a candidate-producing API and also accepts a `Document`. Creating an archive is a separate, explicit action:

```python
from attestextract import (
    CandidateReview, Document, DocumentMetadata, create_archive, extract,
    load_archive, save_archive,
)

document = Document.from_text(
    "Synthetic source text.",
    source_name="source.txt",
    metadata=DocumentMetadata(title="Synthetic example"),
)
# `task` and `backend` are configured as in the extraction example above.
result = extract(document, task, backend=backend)
pending = create_archive(document, result)
assert pending.records == []  # Exact evidence matching does not accept a candidate.

# After actually reviewing the candidate and evidence, record that decision:
if result.candidates:
    review = CandidateReview(
        candidate_id=result.candidates[0].candidate_id,
        status="accepted",
        reviewer="Example reviewer",
        rationale="Reviewed this candidate against the exact source and task.",
    )
    reviewed = create_archive(document, result, reviews=[review])
    save_archive(reviewed, "reviewed-archive.json")
    restored = load_archive("reviewed-archive.json")
```

The sample review must be supplied only after real review; it is not a recipe for automatically accepting the first candidate. An accepted candidate must also have complete required fields and exact evidence. To inspect an already labelled synthetic case directly, use `from attestextract.d1_examples import load_d1_example` followed by `load_d1_example("attribute")`.

| Contract | Stored information and rules |
| --- | --- |
| `Document` | Embedded exact decoded `text`, `document_id`, SHA-256, character count, source name, representation (`text/plain` or `text/markdown`), and representation version. `DocumentMetadata` optionally stores title, authors, year, DOI, and source URI. |
| `TaskSpec` | Named task, record types, typed scalar fields, instructions, and identity/condition field references. |
| `Candidate` / `EvidenceRef` | Candidate ID, typed fields, exact quotations, Unicode character spans or unresolved evidence states, chunk references, and structural checks. |
| `CandidateReview` | Existing candidate ID, `accepted` / `rejected` / `needs_review` / `unreviewed`, reviewer, rationale, optional annotation reference, and origin. Nontrivial human/fixture decisions require a reviewer and rationale. |
| `FinalRecord` | Accepted candidate's unchanged fields/evidence, its candidate and document references, and a record ID. Rejected and pending candidates remain in the archive without becoming accepted records. |
| `ExtractionArchive` | `archive_schema_version: "0.1"`, `document`, `extraction`, `reviews`, and `records`; extraction retains its own schema version and run information. |

If no explicit review is supplied, candidates become `unreviewed`, or `needs_review` when checks are unresolved; system checks never set `accepted`. `origin: "human"` records caller-provided attribution and does not authenticate a person's identity. Synthetic cases are labelled `synthetic_fixture`, and automatically assigned pending states are labelled `system`. “Accepted record” means an explicit recorded review decision, not an independently proven fact. Changing a value requires a new candidate and review; a final record cannot silently overwrite its source candidate.

`save_document` / `load_document` and `save_archive` / `load_archive` persist UTF-8 JSON and validate hashes, counts, IDs, references, and evidence on reload. Document IDs identify an exact text representation; metadata does not establish source authenticity. Preserve the input string's line endings when reading source files, for example with `Path(path).read_bytes().decode("utf-8")`. Markdown converted from a PDF has its own text positions, not PDF page or bounding-box coordinates.

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

CI runs on Windows and Ubuntu with Python 3.11 and 3.13. Public tests use synthetic fixed responses and mocked HTTP, never paid model requests. They compare all three D1 cases to hand-written expected semantic output and check archive persistence and invalid references. CI also installs the wheel in a clean environment outside the checkout and runs both original demos and all three D1 archives. The dev3 changes were evaluated offline; any existing real run logs were replayed locally without automatically calling the model again. End-to-end model quality and provider compatibility require separate evaluation against your chosen endpoint and corpus.

Next steps are a Jev-style bounded decision adapter, conflict handling and support verification, and compatibility outputs for the earlier CCUS workflow. These are plans, not implemented features or performance claims. The public prototype contains only new framework code and synthetic examples; it does not include research documents, annotations, patents, original workflow outputs, or credentials.

Related work: [LangExtract](https://github.com/google/langextract) and the existing [JevExtract](https://github.com/gabazureus/jevextract) project. AttestExtract is a separate early project and does not vendor either implementation.

No license has been added to this early repository. Public availability does not grant an open-source license. This version is not published to PyPI.

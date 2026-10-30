# AttestExtract

Schema-driven extraction from text and tables, with source-linked evidence and pluggable decision backends.

**Early prototype · `0.1.0.dev8` · Python 3.11+**

Dev8 adds named model profiles, explicit backend registration for Python and CLI,
portable generation controls and a second-protocol simulation. See the
[D2 adapter and acceptance guide](docs/BACKENDS.md) for configuration, extension,
error contracts and bounded live acceptance.

dev7 adds task-declared measurement evidence groups, conditional missingness rules,
possible candidate associations, literal math evidence in review reports, and separate
observed JSON Schema diagnostics. See [measurement review and evaluation](docs/MEASUREMENT_REVIEW.md)
and [JSON Schema troubleshooting](docs/SCHEMA_DIAGNOSTICS.md). Jev and semantic adjudication remain unimplemented.

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

Live extraction sends document chunks to your configured endpoint. There is no automatic fallback to demo responses. Transport failures and transient HTTP statuses are retried at most three times by default. Authentication and configuration failures stop immediately; response format errors stop by default, with an optional single format retry described below. An unrecovered chunk fails the run with a nonzero exit status; there is no partial-success or resume mode.

### Optional structured output

`extract --structured-output off|json_schema` defaults to `off`. With `json_schema`, AttestExtract generates the response schema from the task and sends `response_format` with `type: "json_schema"` and `strict: true`. The Python equivalent is `OpenAICompatibleBackend(..., structured_output="json_schema")`.

In strict mode, every declared field has a required `{ "value": ..., "quote": ... }` wrapper. The task's scalar type constrains the **inner `value`**, and `quote` remains a string. For an unreported field, return `value: null` and `quote: ""`; the existing review rules apply, and this does not create an accepted record. With structured output off, optional unreported fields may still be omitted.

The provider/model must support this schema format. HTTP 400/422 failures stop the run; AttestExtract does not silently fall back to unstructured output or issue a format retry for HTTP errors. Normal transient-network retry rules still apply. [Wenwen's structured-output documentation](https://wenwen-ai.apifox.cn/305807074e0) shows this request format; support must be checked on the actual provider/model route. Local validation remains mandatory even when the endpoint accepts the parameter.

### Optional format retry

Prompts require unique keys in each JSON object, the declared scalar types, and exactly one `value` and one `quote` per supplied field wrapper. A nested `fields.value.value` is valid; two `value` keys in the same object are rejected, even when their values agree. The parser also rejects non-finite numbers. Diagnostics identify `duplicate_key` and `non_finite_number` paths, masking undeclared key names; ordinary JSON syntax errors retain line and column information. Nothing is silently repaired or overwritten.

`extract --format-retries 0|1` defaults to `0`. With `1`, a local JSON, record-contract, or task-schema failure allows **one new generation for that chunk** using the same source, task, and schema plus bounded validation feedback. The new response must pass structural validation and undergo the usual evidence checks. Earlier successful chunks are not rerun. The Python equivalent is `extract(text, task, backend=backend, format_retries=1)`.

Enabling this option can add a paid generation per failing chunk; each generation retains the usual bounded HTTP retries. It does not retry HTTP 400/401, malformed API response envelopes, refusals, or truncated completions. Missing, unmatched, or ambiguous quotations continue to require review without a format retry. If the second format attempt fails, the run fails explicitly. This is candidate generation, not acceptance or factual verification.

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
$runDir = "..\research\attestextract\runs\procurement\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
attestextract extract --env-file .env --structured-output json_schema --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output "$runDir\extraction.json" --report "$runDir\review.md" --log-dir "$runDir\diagnostics"
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
| `http/{chunk_id}.attempt-{n}.error.json` | Safe HTTP failure details for one attempt, including failure layer, chunk, attempt, HTTP status, request IDs, and allowed provider error fields when available. |
| `validation/{chunk_id}.errors.json` | Validation phase, total error count, and up to 50 safe details with field paths, codes, expected types, actual types, and messages; no rejected input values. |
| `candidates/{chunk_id}.json` | Candidates and structural/evidence checks for the chunk. |
| `result.json` | Final extraction result after a successful run. |
| `error.json` | Safe failure summary; `http_diagnostics` summarizes HTTP failures and `http_details` links their relative artifact paths. Validation failures retain their detail links and total counts. |

New traces use `trace_schema_version: "0.3"`; existing log directories are not rewritten. First-attempt paths above remain unchanged. A format retry uses the stem `{chunk_id}.format-2`, for example `requests/chunk-0001.format-2.request.json`, `responses/chunk-0001.format-2.txt`, and, if rejected, `validation/chunk-0001.format-2.errors.json`. Its HTTP errors use `http/chunk-0001.format-2.attempt-{n}.error.json`. Both requests and raw responses are preserved; `.meta.json` contains metadata, not model text. Candidate chunk IDs remain unchanged. `format_retry` events record `retry`, `succeeded`, or `failed`; `format_attempt` distinguishes generation from the HTTP `attempt`. Successful run counts and usage include both rejected and recovered responses.

A failed run can leave earlier chunk files for diagnosis; they are not a successful partial result. Trace files can contain **the full paper text, task instructions, and model output**, even when credentials and backend details are sanitized. Store them outside this repository, as above, and do not commit or publicly share them without reviewing their contents. These are logs of the implemented extraction pipeline; Jev and Stage2.5/Stage3 decision modules are not implemented or simulated by tracing.

`extract` writes JSON to its required `--output` file. Add `--report review.md` for a Markdown review report generated from the same successful result, with no additional model call. It shows candidates, field values, verbatim quotations, source positions, evidence status, and review reasons. The report does not accept candidates or establish factual correctness; JSON remains the machine-readable result. Known configured API keys are redacted from the report. A failed extraction produces no success report. Output and report paths must differ from each other and from input, task, and `.env` files, and both must be outside `--log-dir`.

With tracing enabled, key stages, completions, and retries appear on stderr in real time, alongside file-written notices and error summaries. Field-level details remain in `events.jsonl`; model responses and trace contents are not streamed to stdout. `demo` and `d1` still print JSON to stdout when `--output` is omitted. Inspect the per-chunk files for intermediate results.

To investigate an HTTP failure, start with `error.json`, then follow `http_details` to the failed attempt. Read the failure layer, chunk, attempt number, HTTP status, and available request IDs alongside provider-reported `error.code`, `type`, `message`, `status`, and `param`. Application compatibility hints are separate inferences; they are not the provider's stated reason. Only allowlisted, redacted, length-bounded fields are retained, rather than complete HTTP error bodies or headers. When no usable provider reason is available, diagnostics mark `provider_detail_unavailable`; they cannot reveal the provider's internal root cause or recover information absent from an older 400-error log.

Dev5 strengthens format prompts and diagnostics and adds the explicitly enabled format retry. Its offline tests establish the retry and validation behavior, not a real model's format success rate or extraction accuracy. Jev's absence does not make unsupported evidence acceptable.

### Run a complete paper and keep its outputs together

Pass the complete UTF-8 `.md` or `.txt` paper to `--input`; the framework chunks it automatically. There is no need to split the paper manually or submit excerpts one at a time. Convert PDFs externally first. Keep reusable papers and tasks separate from disposable run outputs, outside this Git repository:

```text
../research/attestextract/
  inputs/paper.md
  tasks/paper.task.json
  runs/paper/<timestamp>/
    extraction.json
    review.md
    diagnostics/
  RUN_HISTORY.md
```

Place your existing paper and task at the chosen paths, then run this PowerShell block from the repository root. It makes **real model calls** using `.env`; `--format-retries 1` explicitly allows the extra generation described above. Use `off` unless structured-output compatibility has been verified for your endpoint. Every run uses a new timestamp directory and fixed filenames.

```powershell
python -m attestextract --version
$researchRoot = (Resolve-Path "..\research\attestextract" -ErrorAction Stop).Path
$paper = (Resolve-Path (Join-Path $researchRoot "inputs\paper.md") -ErrorAction Stop).Path
$task = (Resolve-Path (Join-Path $researchRoot "tasks\paper.task.json") -ErrorAction Stop).Path
$paperStem = [IO.Path]::GetFileNameWithoutExtension($paper)
$runDir = Join-Path $researchRoot "runs\$paperStem\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$output = Join-Path $runDir "extraction.json"
$report = Join-Path $runDir "review.md"
$logs = Join-Path $runDir "diagnostics"
python -m attestextract extract --env-file .env --structured-output off --format-retries 1 --input "$paper" --task "$task" --output "$output" --report "$report" --log-dir "$logs"
$extractExitCode = $LASTEXITCODE
Get-Content "$logs\manifest.json" -Raw -Encoding UTF8
$events = @(Get-Content "$logs\events.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$events | Where-Object { $_.stage -eq "format_retry" -or ($_.stage -eq "validation" -and $_.event -eq "rejected") } | Format-Table stage,event,chunk_id,format_attempt,path
if ($extractExitCode -eq 0) {
    $result = Get-Content "$output" -Raw -Encoding UTF8 | ConvertFrom-Json
    $result.run | Format-List
    Write-Output "Read the candidate review report: $report"
}
```

Open `review.md` in a Markdown preview for the readable result; use `extraction.json` for programmatic processing and `diagnostics/` for investigation. Read `validation/*.errors.json` alongside the matching `responses/*.txt` inside diagnostics. A preserved first error followed by `format_retry/succeeded` means the new response passed format validation; it does not mean the original JSON was modified. A successful whole run requires exit code `0`, manifest status `succeeded`, and the final output. A successful empty result is not proof of completeness, and a report containing review items is not a set of accepted facts.

For a smaller paid experiment, use a verbatim excerpt containing complete subject/condition context and any table header. An excerpt has a new document identity and offsets starting at zero, rather than full-paper offsets. To assess retry behavior, run the same input/task/model with `--format-retries 0` and then `1`, using a fresh run directory each time. Compare first-attempt format failures, recovered chunks, usage, and values/quotations separately; count only attempted chunks in failed runs. Repeat a fixed small set within your budget. One pair of runs cannot establish reliable improvement, and this comparison does not isolate prompt changes between versions.

`RUN_HISTORY.md` is a suggested local experiment ledger, not a file automatically maintained by the CLI. Before removing old run directories, record each run's version, input/task identity, model, outcome, failure cause or recovery, and retained result locations; secure reusable inputs and task files in their separate directories first. Keep any raw evidence needed for later audits, because a summary cannot reconstruct deleted responses. Cleaning old outputs is an explicit local housekeeping step; `extract` does not automatically delete previous runs.

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

Only declared record types and fields are accepted. Invalid JSON, unknown fields, and wrong scalar types fail explicitly. Missing/null/blank required, identity, or condition fields enter the review queue. Optional missing values produce informational items; empty records still require review. Outside strict structured mode, omitting an optional field is allowed. `not_applicable_when` classifies missing values only; an explicitly reported unit such as `%` remains reported even for a dimensionless parameter.

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
- Evidence for reported values must match exactly within the current chunk's original source segments. Missing, absent, or ambiguous quotes require review; ambiguous matches have no guessed `start`/`end`. Optional null/blank/omitted fields produce one `info` item rather than two review failures. Task-declared critical conditions remain blocking. Only identical candidates grounded at the same source locations are deduplicated. Equal values from different locations or conditions remain separate.
- New result JSON uses `schema_version: "0.2"`, with `package_version`, `task`, `source`, `candidates`, `review`, `links`, and `run`. The reader still accepts `0.1`; older readers need upgrading for `0.2`. Review items have `severity: "review" | "info"`. Candidate `field_status` distinguishes reported, not reported by the model, task-declared not applicable, and required missing. Configured `measurements` keep shared context and exact token spans; their `semantic_status` remains `not_verified`. New identity rules for optional missing values and measurement metadata can change candidate IDs; do not reuse earlier review decisions without checking provenance. Run information includes backend, `live`/`replay` mode, model, counts, attempts, usage if reported, and elapsed time. A successful empty extraction has no candidates; failures raise exceptions instead.
- A matched quote proves that text occurs at a location. It **does not prove that the extracted value is supported or factually correct**. This version has no semantic support verification, factual adjudication, entity resolution, full PDF parsing, or complete replacement for the original CCUS workflow's Stage2.5/Stage3.

`ExtractionBackend` defines the generation boundary. `ReplayBackend` is available for explicit offline replay and tests. `DecisionBackend`, `DecisionTask`, and `DecisionResult` reserve a separate decision boundary: `validate_decision` allows selecting only a supplied candidate, rejecting, or requesting review. Decisions cannot invent a replacement candidate. The extraction pipeline does not yet call a decision backend.

## Development and roadmap

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

CI runs on Windows and Ubuntu with Python 3.11 and 3.13. Public tests use synthetic fixed responses and mocked HTTP, never paid model requests. They compare all three D1 cases to hand-written expected semantic output and check archive persistence and invalid references. CI also installs the wheel in a clean environment outside the checkout and runs both original demos and all three D1 archives. Format regression tests cover rejected output, bounded regeneration, preserved attempts, and subsequent evidence checks. End-to-end model quality and provider compatibility require separate evaluation against your chosen endpoint and corpus.

Next steps are a Jev-style bounded decision adapter, conflict handling and support verification, and compatibility outputs for the earlier CCUS workflow. These are plans, not implemented features or performance claims. The public prototype contains only new framework code and synthetic examples; it does not include research documents, annotations, patents, original workflow outputs, or credentials.

Related work: [LangExtract](https://github.com/google/langextract) and the existing [JevExtract](https://github.com/gabazureus/jevextract) project. AttestExtract is a separate early project and does not vendor either implementation.

No license has been added to this early repository. Public availability does not grant an open-source license. This version is not published to PyPI.

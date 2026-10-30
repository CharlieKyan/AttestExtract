# Changelog

## 0.1.0.dev9

- Add `extract(..., model="provider:model")`, CLI `--model-id` and an offline `models` list. Three explicit presets cover DeepSeek Flash, Qwen 3.8-27B and Qwen Plus; keep native CLI `--model`, profiles, registry and injected backends compatible.
- Resolve provider credentials and endpoints separately from legacy global settings. Support existing `VLM_API_KEY`/`VLM_BASE_URL` for Beijing Qwen, explicit overrides and actionable preflight errors; reject unsupported presets/regions instead of guessing capabilities.
- Presets use JSON object output, disabled thinking, 2,048 output tokens, one HTTP attempt and no format regeneration. Existing backend/profile defaults and result/trace schemas remain unchanged.
- Classify HTTP 402 as `billing_error` (previously `http_error`) and add safe guidance for authentication, billing, permission, missing-model/route and unsupported-parameter errors. Retrying and fallback behavior are unchanged.
- Add Python/CLI switching tests and a synthetic installed-wheel smoke check. Reuse separately recorded dev8 live endpoint evidence with an offline request-equivalence check; this release makes no new live model call or all-samples semantic-accuracy claim. No new dependency or Jev decision implementation.

## 0.1.0.dev8

- Add explicit backend registry/factory and named TOML profiles used by Python and CLI; preserve legacy environment configuration. Profile switching cannot inherit stale global endpoint/model settings, and outputs cannot overwrite TOML inputs.
- Add portable generation controls, `json_object`, explicit output-token field mapping and provider options. Reject reserved-field overrides and retain explicit provider failures without fallback.
- Add stable backend error codes/retryability and observed response metadata; preserve configured-model fallback, trace paths and result schema 0.2. Declare simulated backend mode explicitly.
- Add a synthetic heterogeneous HTTP-envelope adapter and three-task CLI acceptance runner. Official endpoint acceptance is opt-in, bounded and recorded separately; synthetic tests do not establish provider accuracy.
- Extend regression, credential-redaction, profile precedence and installed-wheel acceptance. No new runtime dependency or Jev integration.

## 0.1.0.dev7

- Add opt-in task-declared measurement groups with shared source context, exact value/unit spans, linked condition fields, same-column Markdown header checks, token boundaries and explicit unresolved binding issues. Matching remains mechanical; no semantic verification or Jev integration.
- Add exact task-value applicability/required rules. Optional missing fields become one informational item; critical missing conditions remain review failures. Blank strings count as missing without modifying their stored values.
- Preserve source observations and add opt-in possible-repeat/conflict/insufficient-context links. Equal values never resolve identity conflicts or erase repeated evidence.
- Render math-like report text literally and abbreviate displayed ambiguous positions while retaining every position in JSON.
- Record requested Schema mode/hash and observed raw-output compliance separately from local candidate retention. Preserve safe provider errors; no automatic fallback or compatibility claims based on HTTP 200.
- Emit result schema 0.2, read existing 0.1 results, and preserve archive review/identity validation for both versions. New measurement data and optional missing-value identity rules can change candidate IDs.
- Add synthetic regression tests and measurement/Schema evaluation guides. No live provider quality or compatibility claim is made by offline tests.

## 0.1.0.dev6

- Add optional CLI `--report PATH` to render a readable Markdown review report
  from the successful extraction result, including candidates, field values,
  quotations, source positions, evidence status and review reasons. The report
  makes no additional model call and does not accept or verify facts.
- Preserve the JSON result contract and original source/evidence mapping. Keep
  report/output paths distinct from protected input, task and configuration files
  and outside the diagnostic directory; redact known API keys from reports.
- Document complete-paper extraction and a local layout separating reusable
  inputs/tasks from timestamped run directories containing `extraction.json`,
  `review.md` and `diagnostics/`. Historical-run summaries and cleanup remain
  explicit local housekeeping, not automatic CLI deletion. No PDF reader or new
  runtime dependency is introduced. Verification is offline, not a real model test.

## 0.1.0.dev5

- Explicitly instruct unique object keys and standard JSON in extraction prompts.
  Report duplicate-key paths and occurrence counts, non-finite numbers (including
  overflow), and excessive nesting without silently discarding repeated values.
- Add opt-in `extract(..., format_retries=1)` / `--format-retries 1`: regenerate
  only a locally invalid chunk once, with unchanged source/task/schema and bounded
  structural feedback. The default remains zero; enabling this can add paid calls.
  HTTP errors, refusals, truncation and evidence-review items do not trigger it.
- Introduce trace schema `0.3` and request `format_attempt` (default 1). First
  artifact paths remain unchanged; regeneration uses `.format-2` paths and retry
  events, preserving both raw responses. Successful-run usage and request counts
  include rejected and regenerated completions. Result schema remains `0.1`.
- Preserve strict task JSON, evidence checks and whole-run failure on exhausted
  format retries. No automatic value selection, partial success or fact acceptance
  is introduced. Verification uses synthetic replay and mock HTTP, not a live model.

## 0.1.0.dev4

- Improve HTTP failure diagnostics with allowlisted provider error fields,
  failure layer, chunk, attempt, HTTP status, and available request IDs. Keep
  application compatibility inferences separate from provider-reported reasons.
- Save redacted, length-bounded details in `http/*.attempt-*.error.json` and link
  them from `error.json` through `http_diagnostics` and `http_details`. Do not
  retain complete HTTP error bodies or headers; mark unavailable provider
  reasons as `provider_detail_unavailable`. Older missing details cannot be
  recovered, and provider-internal root causes are not guaranteed to be known.
- Preserve fail-fast parsing, evidence-review rules, and existing retry limits;
  add no fallback or extra model calls. This update has no real-endpoint test.

## 0.1.0.dev3

- Build task-aware complete synthetic prompt examples, clarify scalar constraints
  on each field's inner `value`, and require verbatim OCR/LaTeX evidence quotes.
- Add opt-in `--structured-output json_schema` and the backend's
  `structured_output` parameter; default to `off`. Generate a strict task-derived
  schema with `value`/`quote` wrappers. Missing values remain explicit nulls and
  enter the existing review flow; they do not become automatically accepted.
- Fail explicitly on HTTP 400/422 structured-output errors without downgrading
  the request or issuing repair retries. Wenwen documents the request format;
  compatibility of the actual Gemini route has not been tested.
- Introduce trace schema `0.2`: distinguish sanitized actual HTTP bodies in
  `requests/*.request.json` from non-HTTP extraction requests, and separate raw
  response text from `responses/*.meta.json`. Keep existing trace directories
  unchanged and describe new artifact purposes in `manifest.artifact_roles`.
- Add directly readable system/task/source prompt files and bounded safe
  validation details (up to 50 errors, no rejected input values), with links and
  total counts in the failure summary.
- Keep public verification synthetic and offline. Existing real responses are
  replayed only in local research directories; no automatic live model rerun.

## 0.1.0.dev2

- Load an explicitly selected `--env-file` as plain data, without shell execution
  or changes to the process environment. Existing environment variables retain
  precedence; explicit CLI options override both.
- Add opt-in `--log-dir` diagnostics: source and chunk mappings, prompts, model
  text, HTTP retry events, field and evidence checks, duplicate handling, and
  safe failure summaries. Active API keys are redacted from trace artifacts;
  HTTP headers, response error bodies, and endpoint URLs are not logged.
- Keep each trace in a separate empty directory. Preserve invalid model JSON
  for diagnosis without returning a failed run as a successful extraction.
- Include the previously local D1 additions: document and archive contracts,
  explicit reviews and accepted records, validated JSON persistence, and three
  synthetic examples with manual expected results. The local dev1 state was
  not separately committed or published as a version tag.

## 0.1.0.dev0

- Initial standalone schema-driven text and Markdown extraction prototype.
- Configurable OpenAI-compatible backend, source-linked candidates, explicit
  review diagnostics, offline replay demos, tests, packaging, and CI.

Jev decision integration remains planned. Evidence checks do not establish
semantic support or factual correctness.

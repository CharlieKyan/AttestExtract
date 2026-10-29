# Changelog

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

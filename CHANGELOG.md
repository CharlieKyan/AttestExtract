# Changelog

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

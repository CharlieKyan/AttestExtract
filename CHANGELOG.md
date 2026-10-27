# Changelog

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

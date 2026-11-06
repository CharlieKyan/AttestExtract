# Changelog

## 0.1.0.dev19

- Add optional source-indexed Markdown/MinerU HTML structure, table grids and explicit header hints; fix escaped-pipe column misbinding and support pipe tables without outer delimiters.
- Add opt-in structure and neighboring-paragraph context, explicit body ownership, source coverage diagnostics, and context-preserving truncation/resume. Keep context `none` by default while quality benefits remain unproven.
- Emit result schema 0.4 with conservative normalized-search source mappings, cell references and supplied context passages. Normalized evidence always requires review; no OCR correction, unit conversion or LaTeX equivalence is inferred. Preserve historical archive and decision-material identities.
- Add offline D4 installation acceptance and a frozen four-arm context experiment. The authorized small real-endpoint pilot does not establish population-level accuracy or a general net benefit from added context; see the experiment documentation.

## 0.1.0.dev18

- Treat different identities or unresolved conditions/evidence as insufficient context for equal-value associations. Preserve all candidates and old saved links; this is not comprehensive different-value conflict detection.
- Add offline `prepare-decisions` with decision bundle 0.1: successful checkpointed runs only, verified final leaves, shared exact source/result snapshots, all candidates and review-only empty-chunk tasks, plus separate current association analysis. Never follow history paths or copy backend/request/response logs.
- Add offline `check-decisions`: validate simulated select/reject/review suggestions, reject invalid references and duplicate returns, record missing tasks and existing blockers. Suggestions never create accepted records, clear review issues or alter source results.
- Preserve flat-v1, result schema 0.3, extraction CLI defaults and historical archive loading. Add synthetic regression and installed-package acceptance; no real Jev, paid model calls, training, or demonstrated decision-quality/cost improvement.

## 0.1.0.dev17

- Use flat-v1 (`values`/`quotes`, with task-declared measurements) for all new model calls, schemas, examples, retries and cached response parsing. Keep native-v1 only as an explicit built-in offline ReplayBackend option; never auto-detect, repair, or fall back to legacy model output.
- Preserve original responses separately from normalized internal records; record protocol in requests, run metadata, CLI checks and checkpoint identity. Package/protocol changes require a new run. Provider structured-output modes remain independent of extraction protocol.
- Emit result schema 0.3 with exact, locally derived range endpoint/separator/unit bindings. Resolve repeated units only for a fully recognized same-unit range; retain review for mixed units, lists, damaged OCR and independent quantities. Classify clear identifier/subscript digits without discarding mathematical expressions.
- Preserve 0.1/0.2 archive identity/check semantics. New bindings are validated on archive load even when other review issues remain. Reports expose each original span and unit relationship; mechanical checks never accept facts.
- Add synthetic offline protocol/evidence package acceptance and regression tests. Historical saved responses may be replayed separately; no new paid model calls or universal live-format guarantee accompany this release.

## 0.1.0.dev16

- Roll back the CLI default format regeneration from one to zero after saved live failures showed no recovery in the observed attempts. Keep explicit CLI/profile one, Python behavior, per-chunk reservations and strict failures unchanged; this limited evidence does not establish that regeneration never helps.
- Include shape-placeholder explanations only when a specific structural hint exists. Preserve raw responses, error coordinates and the optional bounded scanner; do not claim that this prompt cleanup improves model accuracy.
- Retain the explicit Responses protocol adapter, but document the observed malformed JSON despite a Schema request. Withdraw switching protocols as a demonstrated reliability fix. New regression tests and acceptance runners distinguish opt-in regeneration from the default; no new paid calls or JSON repair.

## 0.1.0.dev15

- Diagnose an anonymous object where a quoted property name is expected, adding bounded, value-free structural paths and shape illustrations to validation artifacts, regeneration feedback and checkpoints. Preserve original text and Unicode error coordinates; never repair or skip failed output.
- Add explicit `deepseek-responses:deepseek-flash` selection with official endpoint binding, `text.format` JSON Schema, `reasoning.effort=none` and native token controls. Keep Chat presets unchanged; reject unsupported parameters, statuses and tool/refusal output without fallback.
- Reuse HTTP/TLS/redaction and finite call budgets; normalize Responses text/usage and retain failure evidence. Add Mock HTTP, resumed feedback and installed CLI acceptance. No new dependencies or paid model calls; documented Schema support remains separate from observed server enforcement.

## 0.1.0.dev14

- Fix result metadata left at dev12 in dev13: new extraction results now resolve the current package version instead of duplicating a version literal. Explicit versions in historical results remain unchanged when loaded.
- Add demo/D1 version checks and a runtime-default regression. Preserve dev13's published tag and behavior changes; rerun package and CI acceptance against dev14.

## 0.1.0.dev13

- Prefer complete sentences in initial chunking and truncation subdivision; fall back through line/clause/word boundaries for long sentences while preserving Unicode source spans, recognizable math and atomic table rows. Character sizes remain targets rather than hard limits.
- Default CLI format regeneration to one when not explicitly configured, preserving profile/CLI zero and Python's existing default. Keep strict JSON, task and evidence validation; never hide failed chunks as empty records or automatically subdivide repeated format failures.
- Persist format-failure feedback and regeneration reservations in checkpoint v2. Resume continues an unused regeneration, but cannot reset a used/interrupted allowance. Audit changes to total call budget and format allowance; preserve exact content identity. Dev12 checkpoints are incompatible with the new planner and require a new run.
- Preflight documented output-mode constraints for known DeepSeek/Qwen Chat endpoints, recording documentation evidence separately from unverified server enforcement. Keep unknown services explicitly configured; no automatic Schema promotion, downgrade or Responses adapter.
- Report regeneration, budget and final chunk counts, including failed/pending/reused chunks. Add synthetic recovery acceptance and installed-wheel checks; no new dependency, real endpoint call or semantic-accuracy claim.

## 0.1.0.dev12

- Plan CLI chunks using task/output complexity and original Unicode spans; retain atomic Markdown table rows. Keep Python's existing fixed execution unless `ExecutionOptions` is supplied.
- Subdivide only truncated chunks with finite depth and a total generation-call budget (default: three times initial planned chunks). Count format regeneration and resumed reservations in the same budget; HTTP retries remain separately bounded by the adapter.
- Save atomic local checkpoints and completed response caches. Resume into a new run, validate all caches before new calls, re-ground evidence and retain historical failure artifacts. Reject changed source/task/model/settings, damaged caches and writes into previous runs. Legacy runs without checkpoints are not resumable.
- Preserve failed completion metadata, source chunks and raw responses. Display splits/reuse/budget and distinguish cumulative observed usage from current invocation time; do not accept partial JSON or silently refund interrupted calls.
- Add synthetic failure/recovery tests and an installed-wheel recovery smoke check. No new dependency, real endpoint calls, semantic accuracy guarantee or result-schema change.

## 0.1.0.dev11

- Save default CLI runs in `outputs/<timestamp-id>/` beside the input. For files directly inside an `inputs/` directory (case-insensitive), use its sibling `outputs/`. Preserve explicit paths and previous run files.
- Add `--max-output-tokens provider` to omit the request output-limit parameter, overriding preset/profile caps while retaining other generation controls. Provider defaults and hard limits still apply; omitted CLI options keep existing defaults. No automatic truncation retry, chunk splitting or extra live calls.
- Add synthetic path/default/override tests and installed-wheel provider-default verification; preserve result and trace schemas.

## 0.1.0.dev10

- Make CLI `--output` optional: create a unique run directory beside the input with result, report and diagnostics. Explicit output paths retain opt-in file tracing. Add local-only `--check` and normal/quiet/verbose terminal progress with waiting heartbeat, chunk counts, tokens and review counts.
- Report JSON parser reasons, stripped/unfenced positions and original-response coordinates; send bounded syntax reasons on explicit format regeneration. Keep invalid responses intact and fail without guessing repairs. Clarify sibling field syntax in prompts.
- Raise preset output ceilings from 2,048 to 8,192 tokens for evidence-rich records and set temperature 0. Keep one HTTP attempt and zero format retries; additional calls still require explicit configuration.
- Use verified TLS 1.2 for the DeepSeek preset compatibility path; retain auto negotiation for Qwen and advanced profiles, configured certificate roots and proxy behavior. No failed POST is silently resent with another TLS version. Record safe transport exception types/codes; `certifi` is now an explicit direct dependency (already required by HTTPX).
- Preserve result schema 0.2 and trace schema 0.3 with additive event/diagnostic fields and a `checked` manifest state. No new live model calls or universal model-format/network guarantee.

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

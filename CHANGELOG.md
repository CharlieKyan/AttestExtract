# Changelog

## 0.1.0a1

- The framework previously published as AttestExtract is now **NiceExtract**. The distribution, CLI command and Python package all use `niceextract`. Scripts can catch `niceextract.errors.NiceExtractError`. The original `NICEExtractError` spelling refers to the same exception class.
- Custom connections now use `NICEEXTRACT_*` environment variables. Legacy-only configurations still work. The default route preference cache starts in `~/.niceextract/cache`; an explicitly configured legacy cache directory is still honored. Existing result files and supported checkpoints keep their original formats. [Connection settings](docs/BACKENDS.md)
- The English and Chinese homepages now walk through a synthetic example, from a reported measurement to its source quotation, and show how to extract your own fields into JSON.

- Field-list tasks can declare the kind of each field or prepare the remaining types once for several papers. Source-linked dataset tables retain the measured object, the filing subject, the original value and its reported meaning. A local checkpoint can review unresolved observations in a separate output. [Dataset guide](docs/dataset-assembly.md)

The development milestones below use their original identifiers. The public release candidate is built from the dev60 runtime.

## 0.1.0.dev60

- Field-list tasks may declare text, numeric, mixed and unknown fields. A fresh model-backed run prepares undeclared fields together. The `prepare-task` command produces a plan that can be shared across documents.
- Prose and tables carry the meaning of a particular reported value, separately from its spelling. Numeric observations can carry a scalar, a range or a one-sided bound. Source values, units, statistical meanings, uncertainties and filing subjects remain available.
- A conflict with a model-inferred type can remain visible for review. Explicit task requirements and source-backed exclusions are applied separately.

## 0.1.0.dev59

- On a source-v2 single-backend prose run, two complete replies with verified JSON syntax failures can trigger a smaller source block automatically. The original replies, completed work, split limits and spent calls remain in the execution record.
- Field policies can distinguish descriptions, standalone numbers and quantities. Explicit, cited field rejections are respected in annotated views. An observation excluded from a column remains in the detailed data with its original source and value.
- A supported verification binding to a registered row may resolve a first-pass uncertainty. Optional relation evidence in source-v2 accepts the model's quotation strings and displayed table references, with source positions checked on replay.

## 0.1.0.dev58

- New field-list tasks read adjacent source paragraphs by default, including the introduction to a routed table. Rows and value cells retain their original source positions and ownership.
- Direct observations can omit a filing relationship. A different requested row can carry the model's own explanation, its evidence and whether the field permits the reading. A damaged optional explanation is recorded on its observation.
- Saved prose responses rejected by the earlier relation contract can be recovered in a separate run with their original source ranges, response provenance and previously spent calls.
- An explicit syntax retry can split a prose block after two known JSON syntax failures. Subject queries distinguish a direct observation, an associated observation, a documented descendant and a pending filing proposal.

## 0.1.0.dev57

- Fresh field-list extractions carry the model's interpretation of each field, its reported use and local subject. For a configured table subject, the model can also supply a filing relationship.
- Main CSVs expose the interpretation and citation status beside each displayed value. Recoverable citations can be repaired from the original document. Unverified proposals remain visible with their evidence history.
- Frozen run directories can replay archived rejected relationships and accept a source-bound rereading. Subject queries, statistic columns and local review exports retain the individual observation references.

## 0.1.0.dev56

- Account separately for first-pass ownership and scheduled verification jobs. A fully processed run with isolated review items can return exit code 3 when both paths were used.
- Verification can cite an original source span by a reference supplied in its request. The console identifies the active verification job while waiting for a response.

## 0.1.0.dev55

- Verification requests have one place to report relations. A source can refer to a name in one observation or to a registered object. The former keeps its original scope and evidence.
- An invalid optional relation in an otherwise complete decision goes to review individually. Independent observation bindings retain their values, evidence and qualifiers. Historical analysis exports remain readable. Resume verification checkpoints with their matching package version.

## 0.1.0.dev54

- Clarify the distinction between a mention's name resolution and a sourced object-to-object relation in verification prompts. Enum retry feedback identifies the permitted choices for the field that failed.
- New verification runs use revision v7. Use the matching package for an older verification checkpoint. Historical exported observations remain available for offline assembly.

## 0.1.0.dev53

- Use the observation's cited assignment to resolve an owner in a longer sentence. Retain scoped registry identifiers and searchable designations on delivered observations.
- Group compatible properties from one source statement. A shared setting can appear once with its explicitly documented members and be retrieved through any member.
- Export reported lower and upper limits, their operator and any supported qualifier. Query the documented use of a value with `measurement_use`, including classification cutoffs and sensitivity inputs.

## 0.1.0.dev52

- Carry a source-linked observation's direct owner and qualified target into the same row and lookup. Reuse scoped binding evidence for shared-subject prose and explicit parameter/value pairs.
- Export the reported range endpoints, mean and unit in the main CSV, with per-cell provenance. Related measurements retain their direct object, statistical context and use.
- Preserve corroborating prose and table citations for a single model setting. Location evidence and simulation conditions retain their reported roles.

## 0.1.0.dev51

- Rebuild frozen observations with local evidence for ownership, row membership and field scope. Retain original checks, including resolved Markdown escapes and qualified table unit references.
- Report the direct owner and its evidence status in the observations view. Related and unresolved rows carry explicit qualifications in the main view.
- Add an opt-in first-pass row-relation protocol for simple observation tasks, with scoped source citations and per-observation alternatives. Prompt comparisons use separate execution fingerprints.

## 0.1.0.dev50

- Keep the extracted owner for direct observations and spend the existing verification
  budget on unresolved names, related rows, field scope and evidence conflicts.
- Resolve a name at an occurrence or within a cited scope, retaining source mentions,
  competing interpretations and measurement context in exports.
- Match bounded numeric ranges against original Unicode and interpret complete table
  labels when targeting missing cells. Older completed observation-first runs can be
  reassembled offline. Resume an unfinished dev49 verification with its original wheel.

## 0.1.0.dev49

- Review every pending dev48 observation with a local Intern-Decision-4B checkpoint. Save the resulting dataset separately, with grounded choices, per-observation receipts and a resumable local ledger.

## 0.1.0.dev48

- On the observation-first branch, preserve locally supported names and competing row subjects. Export confirmed and provisional rows with a final CSV status column and separate strict-query semantics.
- Check task-declared property qualifications and exclusions against source evidence. Reread clearly missing table values at most once per local region within the existing extraction budget.

## 0.1.0.dev42

- Assemble frozen observations into schema-ordered rows, searchable details and source lineage. Resolve scoped names, local table roles and supported associations offline. Review unresolved claims and coverage gaps with a separately budgeted, resumable supplement.

## 0.1.0.dev41

- Extend explicit `format_retries=1` to bounded verification: one fresh generation per job for a complete response with JSON syntax or structural-contract errors. It consumes the existing step and cumulative call limits; unknown transport outcomes, truncation, scope and evidence failures do not qualify.
- Add `--retry-analysis-job` / Python `retry_analysis_jobs` for explicit recovery of an eligible tail failure. Validate all saved work before new calls, reuse completed extraction and analysis, preserve original failures, and never renew a spent regeneration allowance. Ordinary resume does not reopen a failed job.
- Upgrade verification checkpoint storage to 0.5 while retaining v5 first-request compatibility and analysis result 0.4. Read compatible 0.4 checkpoints, recheck rejected response artifacts, bind their checksums on migration, and record each retry's parent step. Old runs remain unchanged.
- Keep strict duplicate-key rejection and show declared protocol keys with both occurrence counts/types instead of masking known keys. Regeneration receives bounded diagnostic facts, not speculative JSON repairs. Synthetic Python/CLI, recovery and installed-wheel acceptance remain separate from live quality evaluation.

## 0.1.0.dev40

- Replace speculative JSON shape corrections with observed parser positions, tokens and declared key names. A missing record closure cannot be diagnosed as a field that belongs in the preceding record.
- Project both new and historical checkpoint feedback before regeneration. Remove legacy shape illustrations and merge instructions without rewriting original answers, checkpoints or completed caches. First requests, protocols, evidence checks and the default zero format retries remain unchanged.
- For eligible fixed-configuration failures, show a complete optional recovery command with explicit one-regeneration policy and cumulative budget. Do not suggest an ineffective retry after the allowance is consumed; uncertain state requires inspection.
- Extend synthetic Python/CLI and installed-wheel acceptance for legacy feedback, three reused chunks, one budgeted regeneration, missing evidence rejection and unchanged history. No real model calls or measured live reliability improvement are claimed.

## 0.1.0.dev39

- Clarify that ownership status and target-field matching are independent. A supported binding must reference a known or locally declared object and source evidence; the framework assigns stable IDs. Retain legal unknown ownership without changing values or spreading group observations to members.
- Assess objects, relations and bindings independently after complete structural/scope checks. Preserve valid proposals, isolate invalid proposals and their explicit object-reference dependents, and collect all independently checkable issues. Do not rewrite rejected model answers into unknown judgments.
- Introduce analysis snapshot/checkpoint 0.4 and verification revision v5. Review groups retain the complete target inventory, applied/held observation partition, original response and per-proposal diagnostics. Replay reconstructs both accepted and held results without new calls; old analysis archives remain readable and extraction recovery stays compatible.
- Show retained versus held observations and preserve partial status even when only an object or relation was rejected. Existing exit code 3 remains restricted to fully processed analysis with review items; failed, pending, limited or incomplete extraction cannot use it.
- Validate mixed decisions, rejected-object dependencies, source integrity, original-value preservation and installed Python/CLI entries with synthetic data. No new generation retries, semantic auto-correction, provider switches or expert acceptance are introduced.

## 0.1.0.dev38

- Ask for continuous evidence sufficient to identify the claim, without a minimum quote length. Keep original quotations separate from bounded display context and its verification-step visibility; display context does not affect evidence or object identity.
- Distinguish missing, ambiguous, unseen and out-of-range evidence. A decision with only unresolved visible matches is retained as a whole review group, never applied or supplied as known objects; later jobs may continue within the existing budget. Hard errors anywhere in the decision still stop execution.
- Introduce analysis snapshot/checkpoint 0.3 and verification revision v4. Preserve quarantined responses, candidate positions and reservation counts, verify them again on resume, and keep the final run partial. Extraction compatibility and historical archive reading are unchanged.
- Add CLI exit code 3 only for complete extraction with all analysis jobs processed and solely isolated review groups remaining. Other incomplete states retain their stopping behavior. Report isolated observations and export review groups and display context alongside the existing tables.
- Validate public Python/CLI entries and installed packages using synthetic HTTP, including mixed hard errors, continuation, unchanged identities and zero-call completed replay. No automatic correction or extra retry is introduced; real-model quality remains a separate acceptance step.

## 0.1.0.dev37

- Separate each verification job's binding targets from read-only reference observations. Preserve reference identity and source context while making the target list explicit; keep the fixed search/read/decide response schema and strict local coverage checks.
- Save structured verification diagnostics for missing, duplicate and unexpected bindings, distinguish response-contract failures from internal state errors, and show safe counts and diagnostic locations in progress and reports. No automatic correction, filtering or additional retry is introduced.
- Advance bounded-verification compatibility to v3 because the request contract changed. Extraction caches remain reusable; old analysis requests and spent reservations are preserved rather than silently rewritten or retried.
- Extend synthetic scope, cross-paragraph, uncertainty, transaction, recovery and installed-entry acceptance. Object proposals and table rows remain model proposals requiring semantic review; successful execution is not scientific validation.

## 0.1.0.dev36

- Require an explicit single-JSON-object instruction in every bounded verification request before hashing, byte accounting or call reservation. This fixes JSON Object endpoints rejecting the first verification step; original extraction and output-mode selection are unchanged.
- Distinguish a provider's explicit JSON-prompt requirement from unsupported output modes, without echoing arbitrary provider text. Record complete HTTP errors separately from unknown transport outcomes, retain diagnostic references and spent reservations, and never resend or downgrade implicitly.
- Advance the verification compatibility revision to v2. Older bounded-analysis checkpoints are rejected before new generation; extraction caches remain reusable through the existing extraction-only recovery entry. Original archives and accounting are not rewritten.
- Exercise strict JSON Object prerequisites through public Python/CLI, route selection and installed-wheel search/read/decide acceptance, alongside JSON Schema and recovery. Real-provider and scientific-quality acceptance remain separate.

## 0.1.0.dev35

- Replace default whole-document discovery with bounded source-only search/read/decide verification of existing extraction candidates. Models describe missing evidence and choose lookups; original values remain immutable. New runs accept sources beyond 60,000 characters and cap each serialized analysis request instead of repeatedly sending the whole document.
- Record every generation reservation and local lookup, validate all completed cache steps before recovery, and never automatically resend failed or uncertain requests. New analysis/checkpoint version 0.2 preserves the old 0.1 recovery contract. Step and input limits are frozen; the cumulative call budget can be explicitly increased.
- Allow composed object display labels while requiring exact quotations from passages actually shown to the model. Reuse exactly identical object declarations without merging merely similar names. Retain target-field mismatch/unknown observations separately from the target table, with original values and blockers.
- Add matching Python/CLI limits, local lookup progress, long-source and recovery regressions, and installed-wheel Python/CLI acceptance with synthetic HTTP. No real model calls, initial extraction-protocol redesign, semantic-search completeness claim or expert/Jev acceptance is included.

## 0.1.0.dev34

- Add opt-in document-level identity and observation binding with `table_subject` in Python and `--table-subject` in CLI. Original values, units, conditions, extraction protocols and checkpoints are retained. The additional model stage has a separate explicit cumulative budget and checkpoint; unknown outcomes are never resent automatically.
- Export versioned object, observation, relationship and evidence tables as JSON, CSV and SQLite, plus an object-oriented wide view retaining multiple observations per cell. Equal names do not merge objects, and child observations never propagate to parents. Unbound observations and original review blockers remain visible.
- Preserve incomplete analysis separately from successful extraction, distinguish model proposals from expert confirmation, and expose the additional stage during offline checks. Initial scope: simple tasks and documents of at most 60,000 characters; no domain-specific assignment rules or automatic unit conversion.

## 0.1.0.dev33

- Subdivide known table length truncations by physical rows or cells, with disjoint observation ownership and unchanged full-table context. Retain raw failures and spent calls; enforce existing depth and cumulative call limits. Unknown transport outcomes remain stopped.
- Validate and migrate eligible dev31/dev32 single-attempt root checkpoints without changing generation settings or reissuing completed requests. Root requests and schemas must match; damaged caches, failure metadata and split trees fail before generation.
- Quarantine observations using out-of-window anchors or declared value sources, while permitting shared identity, units and conditions. Preserve child artifact/provenance IDs and distinct review IDs. Result schema 0.6 and observe-only semantics remain; no scientific field correction or Jev integration is added.

## 0.1.0.dev32

- Add concise annotation JSON and Markdown to managed outputs and offline review exports. Show all fields, conditions and quotations together per exact object label without changing, merging or selecting observations.
- Move technical provenance to a separate annotation source map; retain detailed review files, substantive warnings, quarantined declarations and explicit unverified status. No extraction prompts, model calls or recovery identities change.

## 0.1.0.dev31

- Align the table request schema with the local conditional `relation` requirement and include an isolated synthetic format example; never fill missing relationships or replace source references automatically.
- Distinguish processed chunks from accepted-format records in terminal output, with retained prose/table and quarantined record totals.
- Add explicit, budgeted `retry_tables` / `--retry-tables` recovery for supported fixed-backend checkpoints, preserving prior reservations and limiting each failed table to one additional generation across the recovery chain. Automatic multi-route checkpoints are not eligible for this opt-in operation.
- Save `review.json` and `review.md` with managed runs and provide offline `export-review` for existing results. Group simple observations only by exact object and condition text, preserve multiple values and source identifiers, and show quarantined records separately. Review rows are not merged entities or accepted database records.

## 0.1.0.dev30

- Add `output_dir` to Python and `--output-dir` to CLI: one new or empty run directory saves results, review report, diagnostics and checkpoints without overwriting history.
- Enable managed execution by default for ordinary Python model selection, aligning preset prose extraction with CLI budgets and adaptive chunking. Legacy backend injection remains available.
- Add optional Python console progress and owned trace completion/failure handling; preserve partial table results and explicit exceptions.
- Verify the installed package through ordinary Python and CLI outputs, no-overwrite checks, offline preview, completed-run reuse and a simulated read timeout. No paid model calls.

## 0.1.0.dev29

- Accept `prompt_description`, a complete `field_list`, and optional annotated `examples` through Python `extract()` and the existing CLI `--task` file. Compile observations offline before model configuration or credential prompts; examples never reduce the declared scope.
- Validate example structure and source quotations with explicit errors and conservative alignment warnings. Preserve original task inputs and compiler revision in snapshots and recovery identities; legacy TaskSpec serialization and no-example prompts remain compatible.
- Supply task examples to both prose and table routes without confusing their output contracts or fabricating table relationships. Preserve range/qualitative strings and explicitly reported statistics; no extra preparation or verification model calls are added.
- Mark blank required table values as missing while retaining the original records and evidence. Table routing compatibility changes to `dev29-table-a-2`; old results remain readable, but prior table checkpoints require a fresh run.
- Report every declared simple-task target, valued candidates and missing-value candidates, without equating those counts with scientific validity. Add synthetic installed-package acceptance and offline public-entry regressions; live scientific quality remains separately evaluated.

## 0.1.0.dev28

- Route recognized complete Markdown/HTML tables through A from the ordinary Python `extract()` and CLI `extract` entry points. Keep prose and table body ranges disjoint, retain full-document offsets and supplied MinerU structure, and share the existing call budget and checkpoint ledger.
- Deliver tables in the separate `table_results` collection under result schema `0.6`, including original responses, partial parses, unchanged candidates, evidence and diagnostics. Prose candidates retain their existing behavior; explicit `table_mode="text"` / `--tables text` supports historical prose-protocol fixtures and checkpoints.
- Make local position checks visibly distinct from scientific verification. Add an independent scope/member-evidence status; a located cumulative label does not establish a complete member list. No Jev calls or automatic field rewrites are introduced.
- Keep complete tables atomic. Table format failures, empty responses and truncation do not trigger format regeneration or subdivision; completed tables resume from checked caches, while incomplete table reservations are not silently resent. Partial record failures are saved and reported consistently, with CLI exit code 2.
- Reject unsupported table-result acceptance archives and decision bundles explicitly instead of silently exporting only prose. Add synthetic unified-entry installation acceptance and offline regression of saved real table answers; no new paid endpoint tests were performed.

## 0.1.0.dev27

- Replace automatic table-field correction with `observe_only` delivery. Preserve every legal parsed record, its fields, citations and ordering; diagnose unknown or missing task fields without deleting or inventing them.
- Resolve source locations separately from model declarations and check literal completeness, units and relationships without rewriting values or evidence. Duplicate facts and historical conflicts are annotations, not merges or replacements.
- Version results as `table-route-v3` and experiments as `table-a-0.3`. Remove the executable strict-result/correction view while preserving read-only imports of earlier saved A responses; ordinary extraction defaults and model prompts stay unchanged.
- Separate delivery fidelity, evidence checks and frozen-reference scoring. Add a task-scoped format-equivalence metric for outer whitespace and a single math-dollar wrapper, without changing original scores, references or evidence.
- Keep partial record isolation and whole-response failures. Release validation uses synthetic tests and saved real responses offline, with no new paid model calls or automatic Jev verification.

## 0.1.0.dev26

- Add a separate, A-only scientific-table experiment entry point with synthetic examples, immutable saved-response imports and source-linked reports. Ordinary extraction defaults remain unchanged.
- Deliver complete extraction results with separate location, literal-integrity and relationship checks; retain the original strict view for compatibility. Unconfirmed relationships do not trigger extra model calls or establish semantic acceptance.
- Permit omitted/null relationship metadata only for empty optional bindings, and isolate malformed records while preserving their original positions and errors. Damaged JSON, duplicate keys and truncation remain explicit failures.
- Preserve original historical requests, response metadata and source provenance in offline forks. Version experiments as `table-a-0.2` and result exports as `table-route-v2`; previous bundles remain read-only.
- Keep exact in-entry source repairs auditable, preserve group/condition and numeric-boundary risks, and expose complete model-visible table context. This release acceptance uses offline saved responses and synthetic tests, with no new paid model requests.

## 0.1.0.dev25

- Accept explicit API base URL, key and unregistered raw model names through Python `extract()` and the matching CLI. No profile registration or synthetic generation is required before extracting the first real source chunk.
- Select exact service/model defaults and bounded compatible routes. Unknown compatible Chat services start with Schema; only explicit parameter rejection permits output-mode or token-field changes. Add an explicitly chosen `ModelConnection` backup, retaining independent credentials and stopping on uncertain transport outcomes.
- Record each automatic generation separately, with a three-attempt chunk limit and default N+2 total reservations. Result 0.5/checkpoint v4 preserve route provenance, failed responses, immutable content identity and completed-chunk recovery; fixed legacy paths keep their existing contracts.
- Extend reports, archives and offline decision materials to verify automatic provenance. Cache only non-secret successful-configuration digests; no cached setting establishes server enforcement or scientific accuracy.
- Verify using synthetic HTTP and isolated installations. New-provider live acceptance remains a separate user-run test; this release work sends no paid provider calls.

## 0.1.0.dev24

- Default built-in HTTP adapters, provider presets and profiles to `tls12_fallback`: a credential-free TLS 1.2 probe followed, only for recognized TLS compatibility failures, by one automatic-negotiation probe. Retain certificate verification and all explicitly configured older TLS policies.
- Unify omitted `max_attempts` to one. Uncertain generation outcomes are never automatically resent or switched to another TLS policy; finite phase timeouts and v3 transport-only recovery remain available.
- Add synthetic HTTP, local TLS 1.2/1.3 server and isolated-installation acceptance. Preserve extraction behavior, historical experiments and their runtime checks; no real supplier calls are part of this release acceptance.

## 0.1.0.dev23

- Attach complete, evidence-bound stage-C semantic reviews offline and derive stage-D configurations without rewriting strict scores, model answers or call reservations. Invalid or altered reviews fail before model calls.
- Read narrowly compatible dev22 experiment bundles, preserving the original 108-slot budget; continue only D with a verified attachment. Online network identity checks remain strict, while offline inspection reports network differences.
- Refuse unqualified D execution before recording skipped trials. Add synthetic review-to-selection regression and independent installed-package acceptance.

## 0.1.0.dev22

- Include all provider experiment entry points and helpers in the source distribution. Preserve the published dev21 tag; extraction and experiment behavior are unchanged.

## 0.1.0.dev21

- Observe actual origin TLS/HTTP versions and bounded phase timing through HTTPX trace/hooks; separate probes from model POSTs and preserve unknown values without logging trace secrets.
- Add offline preparation, integrity checking, at-most-once scheduling and layered scoring for a frozen 108-slot Qwen/DeepSeek experiment. Fresh-paper trials use the existing extraction pipeline; unknown reservations cannot be replayed.
- Add synthetic cross-provider installation acceptance and distinguish transport, format, task, content and evidence results. Paid endpoint experiments require separate approval; mock checks are not live reliability or accuracy claims.
- Keep provider defaults, timeout/retry policies, prompts, flat-v1 and result schema 0.4 unchanged.

## 0.1.0.dev20

- Add Qwen's verified TLS compatibility preflight: anonymous, bounded `/models` probes may select TLS 1.2 only after recognized bad-record-MAC failures. Never replay an ambiguous generation POST to change TLS; certificate failures remain failures.
- Default connect/read/write/pool timeouts to 15/180/30/15 seconds; preserve explicit uniform timeouts and add `read_timeout` / `--read-timeout` and `--tls-mode`. Read/write failures that may have reached the server stop even with multiple HTTP attempts configured.
- Checkpoint v3 separates transport settings from extraction identity, records transport changes, and narrowly migrates validated dev19/v2 builtin runs. Offline `--check --resume` validates every cached response; failed runs show a shell-quoted recovery command. Preserve flat-v1 and dev19 result schema 0.4.
- No new provider SDK, streaming, JSON repair or paid model calls. TLS probes, model POST attempts and generation reservations are separate counts; a preflight-only failure conservatively retains its generation reservation.

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

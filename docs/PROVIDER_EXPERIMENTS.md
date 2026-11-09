# Bounded provider experiments

dev24 changes the ordinary connection defaults. Existing frozen experiments and
review attachments remain bound to their original runtimes; retain their reports
and use the frozen version for re-analysis. Do not edit a historical manifest or
transfer unexecuted trial allowances to a new run. New live acceptance requires a
new frozen bundle and an explicitly bounded call authorization.

dev23 adds [verified semantic review attachments](semantic-review.md) for stage-D
selection. The original strict scores and call ledger remain available.

This developer tool prepares a private, reproducible comparison of the existing
Qwen and DeepSeek adapters. It does not automatically tune ordinary extraction,
save learned connection preferences, or make additional repair calls.

The current experiment is a descriptive pilot on two endpoints, one network and
one 16-chunk paper. It cannot establish a universal TLS default or model ranking.
All public examples and automated tests use synthetic sources and mock HTTP.

## Fixed design

| Stage | Material and comparison | Maximum model requests |
| --- | --- | ---: |
| A | Fresh Qwen paper, fixed TLS 1.2, Chat JSON Object | 16 |
| B | Two providers × three TLS policies × short/long load × three rounds | 36 |
| C | Four protocol paths × three D1 task types × two rounds | 24 |
| D | Fresh paper for each eligible provider configuration | 32 |
| Total | No replacement calls or budget transfer | 108 |

Stage B compares `auto`, fixed `1.2`, and `auto_fallback`. The long request is the
complete frozen request for paper chunk 12, including its unchanged task and
prompt. The short request is the synthetic D1 conditional observation. TLS order
rotates ABC/BCA/CAB; provider order reverses in round 2. Each short/long pair shares
one backend instance, with no reuse of generated output. Execution is serial, with
two seconds between trials. Round 3 cannot start until 30 minutes after round 1
started. The runner returns without requests when that window is not due.

Stage C tests Qwen Chat Object, DeepSeek Chat Object, DeepSeek Responses Object and
DeepSeek Responses Schema. Each provider uses the connection selected by B.
Qwen's selected model is not documented for Schema and is not forced past the
existing capability guard. Comparing Responses Object with Responses Schema
isolates output mode; Chat versus Responses also changes the API envelope.

All trials use `flat-v1`, non-thinking settings, temperature 0, one HTTP attempt,
zero format regeneration and zero truncation subdivision. Requested temperature
does not establish deterministic service output. Short samples allow 2,048 output
tokens; paper blocks allow 8,192. The aggregate requested output allowance is
626,688 tokens, not a bill estimate. Input tokens and unknown failed-request costs
are separate. Phase timeouts are connect 15/read 180/write 30/pool 15 seconds.

## Offline preparation

Run from a checkout with the package installed. The input paper must produce
exactly 16 chunks under the frozen planner. A different count requires a revised
experiment; it does not silently increase the budget.

```powershell
python scripts/provider_experiment.py prepare `
  --input 'C:\private\paper.md' `
  --task 'C:\private\task.json' `
  --env-file 'C:\private\.env' `
  --output 'C:\private\provider-pilot'

python scripts/provider_experiment.py check --bundle 'C:\private\provider-pilot'
```

These are templates: replace the private paths before execution. The task-specific
delivery supplies complete commands using the actual prepared directory.
Preparation and checking never connect to providers, including `/models`.

The bundle freezes original source, task, 16 chunk positions, 19 deduplicated
requests, synthetic gold, 12 possible backend configurations, 108 numbered trial
slots and wire-payload hashes. C/D alternatives are frozen before selection.
Original source and task bytes have separate checksums. Package/experiment code,
dependency versions, proxy and certificate identity are checked before execution.
Key values are never saved or hashed; only credential variable names are bound.
Endpoint addresses and full prompts are private. Output inside the public checkout
is rejected, and existing experiment directories are not overwritten.

`paper-checklist.json` freezes per-chunk source anchors for headings, conditions,
values and units. A default checklist is **not reviewed**, not empty gold. An
independently reviewed checklist can be supplied with `--checklist PATH`. Anchors
must point exactly into the original source. It is never included in model input.

## Explicit live execution

Review the frozen material and obtain paid-call approval first. The `--live`
switch prevents accidental execution; it is not a substitute for authorization.

```powershell
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage A --live
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage B --round 1 --live
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage B --round 2 --live
# Run round 3 only when the recorded 30-minute window is due.
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage B --round 3 --live
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage C --live
python scripts/provider_experiment.py run --bundle 'C:\private\provider-pilot' --env-file 'C:\private\.env' --stage D --live
python scripts/provider_experiment.py report --bundle 'C:\private\provider-pilot'
```

Each trial writes an exclusive, flushed reservation before invoking a backend.
An OS lock prevents concurrent schedulers sharing the ledger. A reservation without
an outcome is unknown and cannot be sent again. The process may continue other
pre-numbered trials; it never creates a replacement for a failed or unknown trial.
An interrupted B instance group is not reconstructed as if its connection cache
had survived. Fresh-paper runs never resume and stop at their first failed block.

Authentication, balance, permission, model/route and certificate errors stop the
provider. Invalid request errors stop the affected path without claiming every
HTTP 400 means unsupported Schema. HTTP 429 pauses that provider's window for at
least 30 minutes. Three consecutive unknown transport outcomes stop the provider,
counted in its own request order. Unexecuted slots remain visible with reasons;
their allowance is not reassigned.

## Interpretation and candidate selection

Diagnostics separate TLS probes from model POST attempts. The actual POST TLS/HTTP
version comes from the origin connection, not the probe or a proxy CONNECT tunnel.
Missing information and custom transports report `unknown`. Headers, credentials,
proxy addresses, raw trace parameters and exception strings are not added to the
connection observations. A header response followed by body failure is not complete
HTTP delivery. Starting a POST attempt is not proof of sending or service processing.

Results report planned slots, reservations, attempts, confirmed responses, unknown
outcomes, unexecuted reasons, token reporting coverage and elapsed median/range.
No response means JSON/content cannot be evaluated. Complete HTTP, valid JSON,
contract, task fields, content and evidence are scored separately. Trial reports
are derived; reservations and outcomes are immutable and integrity checked.

Connection selection requires six of six complete successful HTTP responses for
each B policy. If multiple policies qualify, keep the existing provider preset.
If it does not qualify, the declared fallback order is `auto`, `1.2`,
`auto_fallback`. No eligible policy means no later trials for that provider.

Protocol selection requires six of six successful samples, eight matching gold
records, no extra records and passing evidence checks. The synthetic attribute gold
has one record; its separate D1 review demonstration deliberately has two candidates
and is not the online target. Conditional observations must retain 18/36-degree
differences. Correct shorter quotations are allowed; a temperature quotation from
the other observation is not. Priority is DeepSeek Chat Object, Responses Object,
then Responses Schema; Qwen has one eligible protocol path.

Stage D still needs an actual successful full extraction outcome and 16/16 fresh
blocks. Sixteen individually parsed responses alone cannot certify the full pipeline.
The source checklist, every empty block and every unflagged candidate require
content review. Matching an original span is not semantic or expert acceptance.
No whole-paper precision/recall is claimed without exhaustive expert gold.

## Offline installation acceptance

Copy all `scripts/provider_experiment*.py` together outside the checkout, then run
`provider_experiment_smoke.py --output-dir NEW_PRIVATE_DIRECTORY` using the clean
environment's Python. It exercises all 108 slots using mock HTTP and synthetic
sources, including repeated invocation without new calls. Actual TLS remains
unknown in this mock. Live handshake observations and reliability require the
separately approved experiment above.

# Semantic review and experiment selection

The provider experiment can attach a stage-C AI semantic review before selecting
the stage-D configurations. Strict scores, raw responses, frozen requests and call
reservations remain unchanged. A review is an additional evaluation, not expert
gold or an accepted extraction record.

## Offline attachment

Run from the checkout (or use the scripts included in the source distribution):

```powershell
python scripts/provider_experiment.py attach-review --bundle '../private/pilot' --review '../private/review.json'
python scripts/provider_experiment.py check --bundle '../private/pilot'
```

These commands never contact the providers. The output separates original strict
selection from reviewed selection, shows the reserved and remaining calls, and
reports whether the current process network configuration matches the frozen one.
Offline inspection can run from another process; online execution still requires
the frozen endpoint, proxy and certificate settings.

Only after inspecting the selected paths, execute the already planned D trials:

```powershell
python scripts/provider_experiment.py run --bundle '../private/pilot' --env-file '.env' --stage D --live
```

Without a qualified path, D now fails before reserving or marking any D trial as
skipped. There is no force flag, new budget, retry or reconstruction of failed
requests. Existing stop rules and the 108-trial limit continue to apply.

## What the attachment proves

The review must cover all 24 stage-C trials exactly once and bind to the bundle,
reference checksums, original result envelopes and raw model responses. The
validator recomputes the original strict scores and checks the stated record and
quote counts. It derives eligible paths in the existing preference order; it does
not trust a recommended model name supplied in a review document.

The initial format permits explicitly reviewed differences in string identity
fields. It does not install a global synonym rule or remove name prefixes. Other
values, required fields, output format, original-source quotes and condition
alignment must still satisfy the fixture checks. A review cannot turn an unknown
HTTP outcome, malformed response, wrong temperature or invented quote into a
passing trial. Reject or uncertain findings prevent the path from qualifying.

Each eligible path needs all six reviewed trials to pass, with the expected eight
records. Qwen uses Chat Object; DeepSeek prefers Chat Object, then Responses
Object, then Responses Schema. Connection choice still comes from the B results.

`results/semantic-review.json` contains the review snapshot, its checksum, the
derived selection and the importing runtime identity. Identical attachment is
idempotent; replacement is rejected. Once D has any recorded outcome, a new
review cannot change its selection. Every later check or run revalidates the
attachment and referenced artifacts. Hashes detect changes; they do not prove
reviewer identity or establish that a semantic judgment is correct.

## Continuing dev22 experiments

dev23 recognizes only the fixed dev22 engine and experiment sources, with the
same Python, dependencies and engine implementation apart from version metadata.
The frozen inputs, reconstructed prompts, wire payloads and ledger are still
validated. Unknown historical versions or changed engine files are rejected.

This compatibility path permits only stage D after a verified review attachment;
it cannot replay A/B/C. The attachment pins the new runtime so later code changes
are also detected. No original manifest is rewritten and no network preference
is learned or changed by attaching a review.

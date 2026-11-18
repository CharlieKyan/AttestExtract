# Object-linked research tables and bounded verification

Ordinary extraction returns evidence-linked candidates. Adding `table_subject`
starts an **additional model stage** that checks object identity, direct
observation ownership and agreement with the requested field definition. The
same successful extraction route is used; no second account is required.

New dev35 runs use a source-only `search` / `read` / `decide` loop. The existing
`flat-v1` extraction protocol is unchanged. Its candidate names, values and
quotations supply hints; this release does not yet make the first extraction
response jointly declare objects and observation references. Mixed `entity`
strings are unverified hints, not an authoritative object inventory.

## Python and CLI

This synthetic example makes real model calls when run with valid credentials:

```python
from attestextract import extract

result = extract(
    text="Batch P contains specimen A. Specimen A has a mass of 17 g.",
    prompt_description="Extract reported specimen properties without inferring missing values.",
    field_list={"mass": "Reported mass", "length": "Reported length"},
    model="deepseek:deepseek-flash",
    table_subject="Individual specimens, distinguished by batch and experiment",
    output_dir="output/new-run",
    max_analysis_calls=12,
    analysis_max_steps=4,
    analysis_input_budget=48000,
)
print(result.analysis["status"])
print(result.analysis["budget"])
```

Use environment credentials, or existing explicit `env_file`, `base_url`,
`api_key_env` or `api_key` options. Never put a key in the task file. The analysis
options shown above are defaults; users can omit them. The table subject says
what a target-table row represents, not which object every measurement must
belong to. A sample measurement stays attached to the sample when supported,
even if the sample's relationship to a larger object is known.

For the equivalent CLI example, save the same sentence in `input/paper.md` and
this simple task in `task.json`:

```json
{
  "prompt_description": "Extract reported specimen properties without inferring missing values.",
  "field_list": {
    "mass": "Reported mass",
    "length": "Reported length"
  }
}
```

```powershell
python -m attestextract extract `
  --input input/paper.md --task task.json `
  --model-id deepseek:deepseek-flash `
  --table-subject "Individual specimens, distinguished by batch and experiment" `
  --max-analysis-calls 12 --analysis-max-steps 4 --analysis-input-budget 48000 `
  --output-dir output/new-run
```

Add `--check` for offline configuration, extraction planning and analysis-policy
checks. It does not send network or model requests. Actual verification jobs
depend on the extracted observations; `--check` cannot predict semantic answers
or exact calls for a new run.

## What the loop does

1. Keep the full original source locally and index passages with Unicode offsets.
2. Group existing observations into bounded jobs. Give the model nearby original
   passages, candidate hints and a bounded set of already proposed objects.
3. Let the model either submit a decision or explain its unresolved question and
   request a search/read operation. The unresolved question is free text, not a
   domain-specific enumeration.
4. Execute the lookup locally and return original source spans. Each next model
   response spends another analysis-call reservation.
5. Save supported or unknown attribution and target-field matching. Values,
   units and original conditions are not rewritten.

Each job has an explicit `target_scope` generated from its current observations:
an ordered list of allowed observation IDs and their count. Only these targets
receive bindings on `decide`, exactly once each. Reference observations remain
available under `extraction_hints`, but use `reference_observation_id` to retain
their original identity without presenting them as additional tasks. They are
read-only context, not evidence by themselves. The model may use that context
to locate original passages and understand relationships across paragraphs.
The fixed `search` / `read` / `decide` response schema is unchanged; this is not
a new task format or a per-provider dynamic-schema requirement.

Ownership `status` and `target_match` answer different questions. A `supported`
binding requires a nonempty subject ID referencing a known or locally declared
object, plus nonempty source evidence. An `unknown` binding requires a null
subject; its target-field match can independently be supported. When the source
identifies a new object, the model can declare a local ID and reference it in
the same answer. The framework assigns stable IDs. Lack of an existing ID is
not evidence that the owner is unknowable. Field descriptions and neutral
examples explain these rules; this does not make a JSON Object service enforce
cross-field rules, and no new conditional-Schema capability is assumed.

New object and relation proposals should explain the current targets. The
framework does not silently delete extra declarations or require every object
to have a directly bound value: an object's identity can be clear while a
measurement's owner remains unknown. This release adds no global identity
merge or semantic relevance rule. Original proposals and diagnostic counts
remain available for review; extra objects are not proof of better coverage.

`search` performs case-insensitive literal phrase matching in this document,
with paginated results. It does not search the web or arbitrary files. The model
can try alternate phrases; this is not embedding-based semantic retrieval and
does not guarantee finding every alias, implicit relationship or paraphrase.
`read` loads selected passage IDs and optional neighbors. The source index,
shown passages, lookup actions and model responses are saved for inspection.

There is no 60,000-character source cap for new analysis runs. The request
builder selects bounded context rather than repeatedly sending the entire
source and object graph. Batches may be divided when required content does not
fit. Optional context omitted for the budget is identified, not represented as
having been read. A task or single observation too large for the configured
request budget remains unresolved with an explicit context-limit reason.

## Evidence and interpretation

An object `name` is a display label and may combine source-supported identity
and scope. It need not occur as one literal phrase. Supporting mentions and
decision quotations must be continuous exact source text in passages actually
shown to the model. Distinct passages cannot be concatenated into a fabricated
quotation. Quotes should contain enough continuous information to identify the
claim and its context; there is no minimum length or requirement to copy an
entire paragraph. A short unambiguous name can be appropriate, while a relation
or attribution may need a clause or multiple separately quoted passages.

An explicit `occurrence` is zero-based within the complete named passage. With
no occurrence, one actually visible exact match can be resolved; multiple
visible matches are retained as alternatives. They are not combined into an
assertion that the model cited all positions.

Dev39 assesses each typed proposal independently after validating global source
integrity, unambiguous object declarations and complete target coverage. Missing,
unseen, ambiguous or out-of-range quotes, undeclared references and contradictory
binding states reject the affected proposal. Rejected objects also block relations
and bindings explicitly referencing them. Independent valid proposals remain
available; the original observation values and rejected answers are retained.
The validator collects independently checkable problems, not all scientific
errors. Unparseable/ill-typed responses, ambiguous identifiers, coverage errors
or corrupted source/checkpoints still stop without committing that decision.

This dependency rule follows explicit object references only. Bindings cannot
reference relation IDs in the current protocol, so the program does not infer
semantic dependence on a rejected relationship. A surviving reference or exact
quote is not proof that the underlying ownership inference is scientifically
correct. Rejected objects never enter later `known_objects`; legitimate new
objects are retained even when a separate binding to them is rejected.

The invocation can continue with later jobs while status remains `partial`.
Rejected proposals do not become model-declared `unknown` answers. No automatic
retry, ID fabrication or answer correction occurs. Historical dev38 (analysis
0.3) archives retain their whole-group isolation semantics when displayed.

`evidence_contexts` separately records bounded display text, its source span,
verification `artifact_id` and `model_visible` flag. True means the whole context
was supplied in that verification job by the named decision step; false means
some display text is program-added and must not be assumed to have been seen.
The original quote stays unchanged. Display context is excluded from evidence
and object identity hashes and is not fed back as an accepted model quotation.

The model may reuse supplied object IDs or propose source-supported missing
objects. Identical names alone do not establish identity; different sites,
experiments, samples and model scenarios must remain distinguishable. A measured
property is not its owner. Relationships never automatically propagate a
member's value to its parent or the parent's value to every member.

`binding_status` records direct ownership. Separately, `target_match` is
`supported`, `unsupported` or `unknown` relative to the task's field definition.
For example, a total mass measurement does not automatically satisfy a requested
dry-mass field. `property_name` and `statistic` preserve the model's interpretation
alongside the immutable original values. All judgments remain `model_proposed`;
an exact quotation proves location, not the correctness of semantic reasoning.
Original mechanical blockers and quarantined records remain available.

An unknown attribution keeps `subject_id` null. A model must not turn an
uncertain attribution into `supported` simply to satisfy the response contract.
Similarly, a program-complete run can contain legitimate unknown decisions.
Review attribution and target-field status separately from execution status.

## Outputs

`result.json` and its extraction schema remain unchanged. Python exposes the
additional product as `result.analysis`. New analysis products and checkpoints
use version `0.4`; older `0.1`, `0.2` and `0.3` analysis products remain readable/exportable.
Managed run directories contain:

| File under `analysis/` | Purpose |
| --- | --- |
| `analysis.md` | Object groups, unknown/mismatched observations and job stop reasons |
| `objects.json` | Target/related object views and separate unbound/unmatched observations |
| `object_table.csv` | Proposed target-object rows; multi-value cells remain JSON arrays |
| `objects.csv` | Object IDs, display names, kinds and scopes |
| `observations.csv` | All unchanged observations, binding status and target-field matching |
| `related_observations.csv` | Matching observations directly owned by non-target objects |
| `unbound_observations.csv` | Observations without supported object ownership |
| `unmatched_observations.csv` | Supported ownership but unsupported/unknown target-field matching |
| `relations.csv` | Proposed object relationships and evidence references |
| `evidence.csv` | Exact quotations and original Unicode locations |
| `analysis.sqlite` | Relational copies of all observations for joins and filtering |
| `analysis.json` | Complete analysis state, review items, policies and call accounting |
| `review_groups.json` / `.csv` | Isolated job IDs, affected observations/fields, original response text and ambiguity diagnostics |
| `evidence_contexts.json` / `.csv` | Display-only original context, source spans and per-step visibility |

For versions `0.2`, `0.3` and `0.4`, a target-field cell receives a record only when ownership and
target matching are both model-supported. Existing extraction warnings still
apply. Excluded observations remain in the complete observation table; they are
not deleted or silently reassigned to a different field. Version `0.1` keeps its
historical projection behavior because it did not have `target_match`.

Versions `0.3` and `0.4` keep held observations in `observations.csv` with
`binding_status=review_required`, null subjects and no applied binding evidence.
They cannot fill the object table. SQLite includes corresponding `review_groups`
and `evidence_contexts` tables. These companion exports do not require reading
diagnostic files: Python calls without `output_dir` retain the response and
diagnostic in memory and can export them later. The report lists all affected
fields; omission from the object table never means absence from the source.
Version `0.4` review groups add `target_observation_ids` and
`applied_observation_ids`; `observation_ids` lists only held observations.
Each diagnostic retains per-proposal issues, raw declarations and explicit
dependencies. An object/relation-only rejection can have zero held observations
and still keeps the run partial. Counts of retained, unknown, held and pending
observations must accompany table coverage assessments; completion alone is
not quality acceptance.

Metadata columns cannot overwrite task field columns. CSV formula-like values
receive presentation escaping; JSON and SQLite retain literal values. There is
no unit harmonization, averaging, range-to-scalar conversion, cross-document
identity merge, expert acceptance UI or Jev integration. A searchable table is
not automatically a scientifically validated quantitative dataset.

## Budgets and failure

| Setting | Default | Meaning |
| --- | --- | --- |
| `max_analysis_calls` / `--max-analysis-calls` | 12 | Cumulative generation budget across all verification jobs, including prior calls on resume |
| `analysis_max_steps` / `--analysis-max-steps` | 4 | Maximum model responses within each job, including search/read planning and final decisions |
| `analysis_input_budget` / `--analysis-input-budget` | 48000 | Maximum serialized framework-request UTF-8 bytes |

The byte ceiling includes framework request instructions, data and schema. It is
**not** a token count, an exact HTTP-body size, or a probe of the provider's
context-window capacity. It does not guarantee that every service accepts the
request. Steps may range from 1 to 12 and the byte ceiling from 12,000 to 256,000.
The original extraction budget remains independent; the combined generation
upper bound is extraction's limit plus analysis's limit.

Local lookup execution has no generation charge, but the model response asking
for it does consume a reservation. Each analysis generation has one HTTP attempt
and finite timeouts. The route is fixed; there is no hidden provider switch or
automatic resend after an unknown outcome. Format regeneration is off by default;
dev41 permits one explicitly enabled regeneration as described below. Incomplete or
structurally unreadable responses are retained and stop when no eligible regeneration
remains. They are never repaired into accepted decisions. Readable per-proposal validation failures are
instead held with their explicit dependents while independent valid proposals
are retained. This is controlled partial processing, not error correction.

Verification protocol failures save structured details under
`diagnostics/analysis/validation/<job-step>.errors.json`. Coverage diagnostics
distinguish missing, duplicate and unexpected IDs, and identify unexpected IDs
that came from the request's read-only references. The terminal shows bounded
counts and the diagnostic location, not arbitrary model text or identifier
content. Response-contract errors and internal source/index/state failures are
separate categories. Neither triggers automatic correction; only the complete-response
JSON/structural cases below permit an explicitly enabled new generation.
The original response is retained, and a rejected decision applies no objects,
relations or bindings from that response.

### Verification format recovery (dev41)

`format_retries=1` / `--format-retries 1` permits **one regeneration per verification
job**, using the same source context, targets and response contract plus bounded
diagnostic facts. It counts against both `analysis_max_steps` and the cumulative
`max_analysis_calls`. The default stays zero. JSON syntax errors (including duplicate
object keys) and structural-contract errors qualify only after a complete model
response. Coverage/scope, action semantics, evidence, internal-state, transport,
refusal and truncation failures do not qualify. A legal `unknown` decision remains
legal; the program never manufactures one to replace a bad response.

To reopen an **already failed** job, explicitly select it on resume:

```powershell
python -m attestextract extract `
  --input input/paper.md --task task.json --model-id deepseek:deepseek-flash `
  --resume output/old-run --output-dir output/recovered-run `
  --table-subject "Individual specimens, distinguished by batch and experiment" `
  --max-analysis-calls 12 --format-retries 1 --retry-analysis-job verify-0002
```

Use the original inputs, model, subject and sufficient cumulative budgets. Python
uses `resume_from=...`, `format_retries=1`, `retry_analysis_jobs=["verify-0002"]`.
Append `--check` to validate the plan offline without requests. Ordinary resume
keeps a failed job failed and may process pending jobs; it does not reopen it merely
because format retries are enabled. Explicit recovery requires a tail failure:
no later job may have sent a request, because a changed decision could change those
later requests' known-object context. Exhausted retry/step/call budgets, oversized
retry feedback and damaged artifacts are refused before sending the retry.

Completed extraction and earlier verification jobs are checked and reused. A retry
gets a new step number, its own raw response and `retry_of` link; the old answer and
reservation remain. The bounded diagnostic facts actually sent are frozen in
`retry_feedback`; later credential redaction of a copied raw answer cannot change
the identity of an already completed retry request. Raising the total budget cannot renew a used format allowance.
Failures with unknown delivery outcomes still stop without resending. A successful
retry undergoes the same reference, scope and evidence checks as the first response.

New verification checkpoints use storage version **0.5**; analysis result snapshots
remain **0.4**, and the initial-request compatibility revision stays **v5**. Compatible
0.4 checkpoints are validated and copied to a new run, including strict re-parsing
of failed-response text and metadata. Their failed-response checksum is first bound
during migration, not retroactively verified against a checksum that never existed.
The old directory is unchanged. Do not edit checkpoints or discard failed responses.

The framework explicitly requests one valid JSON object in its verification
system message. This instruction is present before request hashing and byte
accounting, and checked locally before reserving a generation. Users do not
need to put technical JSON instructions into their task or source text. Output
mode selection remains fixed during verification; a missing prompt prerequisite
does not authorize a mode downgrade or a second request.

An explicit JSON-prompt requirement from the service is reported as
`json_prompt_required`, with a fixed safe explanation and the private diagnostic
path. Arbitrary provider text is not echoed to the terminal because it may
contain source content. A complete HTTP error is recorded as
`request_outcome=http_error_received`; a transport failure with unknown delivery
remains `unknown`. Neither releases a reservation, proves zero billing, or grants
permission to resend.

`complete` means all planned jobs submitted valid decisions. A decision may
explicitly report unknown ownership or field meaning, so complete does not mean
all facts are correct or ready for quantitative analysis. Exhausted step/input
limits or unfinished work at the call budget produce `partial`; a failed model
request/response produces `failed`. Isolated review groups also keep analysis
`partial`. CLI preserves successful extraction and distinguishes these cases:

| Exit code | Meaning | Batch handling |
| --- | --- | --- |
| `0` | Extraction and analysis processing complete; scientific correctness remains unconfirmed | Continue |
| `3` | Extraction complete; all analysis jobs processed; only rejected proposals remain for review | A caller may explicitly continue, recording partial status |
| Other nonzero | Failure, incomplete extraction, pending jobs or exhausted analysis limits | Stop and inspect diagnostics |

Code `3` is not a general allowance for incomplete runs. In particular, an
ambiguous group followed by unprocessed jobs at the call limit does not qualify.
The report and manifest retain the distinction (`analysis_review_required`).
Review counts and affected fields must accompany any table-quality assessment;
the table excludes held observations and may therefore be biased toward easy
cases. Later independent jobs can propose their own supported objects, but
cannot see rejected objects as `known_objects`.

## Recovery and historical runs

Extraction is saved before analysis starts. `diagnostics/analysis/` records
`checkpoint.json`, the source index, per-step requests, original responses,
validated caches and local tool results. Resume validates and replays completed
steps offline before spending a new generation reservation. Damaged caches or
changed source, task, model identity, table subject or lookup policy fail before
new requests.

Resume a **new-policy** run into a new output directory:

```powershell
python -m attestextract extract `
  --input input/paper.md --task task.json `
  --model-id deepseek:deepseek-flash `
  --table-subject "Individual specimens, distinguished by batch and experiment" `
  --resume output/new-run/diagnostics/execution/checkpoint.json `
  --max-analysis-calls 20 --analysis-max-steps 4 --analysis-input-budget 48000 `
  --output-dir output/resumed-run
```

The adjacent analysis checkpoint is detected automatically. An explicit
`--analysis-resume` requires the matching `--resume`. Add `--check` to inspect
compatibility offline first. Python uses the same original inputs and
`resume_from="output/new-run/diagnostics/execution/checkpoint.json"`, optionally
`analysis_resume_from`, with a new `output_dir` and unchanged
`analysis_max_steps` / `analysis_input_budget`.

Increasing an exhausted total analysis budget permits pending steps/jobs to
continue; spent calls are not refunded. It does not resend failed/unknown steps
or reopen jobs that reached their step/context limit. The step limit and input
byte budget are bound into checkpoint identity and cannot be changed on resume.
In particular, ordinary resume can process later pending jobs while retaining
an earlier failed job; it cannot make that run complete by correcting the
failure. Review-required jobs are likewise replayed and preserved, not retried
or promoted to valid decisions. Their original responses, diagnostic hashes,
recomputed proposal/position issues and retained/held partition must agree before any new request. Inspect
the reported cause before spending further calls.

Historical version `0.1` checkpoints continue the legacy whole-document contract,
including its 60,000-character restriction and existing failure behavior. They
are not automatically migrated to the new loop; new lookup controls cannot
retrofit an old failed discovery. Use a fresh run, without `--resume` from that
old analysis, when evaluating the new policy. Historical files are not rewritten.

Dev36 used compatibility revision `bounded-source-verification-v2` for its
corrected JSON instruction. Dev37 used `bounded-source-verification-v3` for the
explicit target/reference contract. Dev38 uses `bounded-source-verification-v4`
and analysis/checkpoint version `0.3` for quote guidance, review isolation and
context provenance. Dev39 uses `bounded-source-verification-v5` and version
`0.4` for per-proposal assessment and the corrected ownership contract. Earlier
bounded-analysis checkpoints cannot resume under v5; their `0.1`/`0.2`/`0.3`
result archives remain readable. A mismatch is reported
before any new model request; do not edit hashes or delete old analysis files
to bypass this check. The extraction compatibility revision is unchanged.

To reuse extraction while explicitly starting a new analysis branch, the
existing public CLI can first restore extraction **without** `--table-subject`,
then resume from that new extraction-only directory with a table subject. For
example, assuming the original run has two completed extraction calls and one
failed analysis call within an eight-call analysis budget:

```powershell
python -m attestextract extract `
  --input input/paper.md --task task.json --model-id deepseek:deepseek-flash `
  --resume output/old-run --max-model-calls 2 --output-dir output/extraction-reused
if ($LASTEXITCODE -ne 0) { throw "Extraction replay failed; stop before analysis." }

python -m attestextract extract `
  --input input/paper.md --task task.json --model-id deepseek:deepseek-flash `
  --resume output/extraction-reused --max-model-calls 2 `
  --table-subject "Individual specimens, distinguished by batch and experiment" `
  --max-analysis-calls 7 --output-dir output/analysis-restarted
```

The extraction cap prevents new extraction calls. The second command can make
new analysis calls. The new branch does not automatically import the old
analysis ledger: its limit of seven plus the one historical reservation respects
the original total of eight. Preserve both runs when reporting cumulative usage.
The same inputs, model and task must still pass ordinary recovery validation;
there is no force-resume or restart-analysis bypass. Fresh runs need neither
step. This is recovery across a prompt revision, not an ordinary user setup step.

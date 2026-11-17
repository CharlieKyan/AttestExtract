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
quotation. Repeated quotations require the correct occurrence within the named
passage; positions are computed and checked by the program.

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
use version `0.2`; older `0.1` analysis products remain readable/exportable.
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

For version `0.2`, a target-field cell receives a record only when ownership and
target matching are both model-supported. Existing extraction warnings still
apply. Excluded observations remain in the complete observation table; they are
not deleted or silently reassigned to a different field. Version `0.1` keeps its
historical projection behavior because it did not have `target_match`.

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
and finite timeouts. The route is fixed; there is no hidden provider switch,
format regeneration or automatic resend after an unknown outcome. Invalid or
incomplete responses are retained and stop that invocation rather than being
repaired into accepted decisions.

Verification protocol failures save structured details under
`diagnostics/analysis/validation/<job-step>.errors.json`. Coverage diagnostics
distinguish missing, duplicate and unexpected IDs, and identify unexpected IDs
that came from the request's read-only references. The terminal shows bounded
counts and the diagnostic location, not arbitrary model text or identifier
content. Response-contract errors and internal source/index/state failures are
separate categories. Neither triggers an automatic correction or a new request.
The original response is retained, and a rejected decision applies no objects,
relations or bindings from that response.

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
request/response produces `failed`. CLI returns nonzero for incomplete analysis
and preserves the successful extraction.

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
failure. Inspect the reported cause before spending further calls.

Historical version `0.1` checkpoints continue the legacy whole-document contract,
including its 60,000-character restriction and existing failure behavior. They
are not automatically migrated to the new loop; new lookup controls cannot
retrofit an old failed discovery. Use a fresh run, without `--resume` from that
old analysis, when evaluating the new policy. Historical files are not rewritten.

Dev36 used compatibility revision `bounded-source-verification-v2` for its
corrected JSON instruction. Dev37 uses `bounded-source-verification-v3` for the
explicit target/reference contract. These prompt changes alter request
identity: bounded v1/v2 analysis checkpoints cannot resume under v3, even
though all use analysis schema `0.2`. A mismatch is reported before any new
model request; do not edit hashes or delete old analysis files to bypass this
check. The extraction compatibility revision is unchanged.

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

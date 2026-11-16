# Object-linked research tables

Ordinary extraction returns evidence-linked candidates. Adding a `table_subject`
starts an **additional model stage** that proposes document objects, their
relationships, and the direct subject of each retained observation. The same
provider configuration is used; no second account is required.

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
)
print(result.analysis["status"])
if result.analysis["status"] != "complete":
    raise RuntimeError("Extraction saved, but document analysis did not complete; inspect its checkpoint.")
```

Use an environment variable for credentials, or pass the existing explicit
`env_file`, `base_url`, `api_key_env` or `api_key` options. They retain their
existing meaning. With a simple task JSON, CLI uses the same pipeline:

```powershell
python -m attestextract extract --input input/paper.md --task task.json --model-id deepseek:deepseek-flash --table-subject "Individual specimens, distinguished by batch and experiment" --output-dir output/new-run
```

Add `--check` for local validation and the two budget limits, with no network use.
The table subject describes what a row represents. It is not a list of names or
a rule that forces every observation onto that row type. A measurement of a
component stays attached to that component, even when a relationship to a larger
object is known.

## Outputs

`result.json` and its extraction schema remain unchanged. Python exposes the
additional, separately versioned product in `result.analysis`. Managed run
directories also contain:

| File under `analysis/` | Purpose |
| --- | --- |
| `analysis.md` | Status, objects, observations and unresolved items for review |
| `objects.json` | Object-oriented records, retaining every direct observation |
| `object_table.csv` | One row per proposed target object; multi-value cells are JSON arrays |
| `objects.csv` | Object IDs, names, kinds and scopes |
| `observations.csv` | One unchanged observation per row, subject ID and binding status |
| `relations.csv` | Explicit proposed object relationships with evidence references |
| `evidence.csv` | Exact source quotations and Unicode locations |
| `analysis.sqlite` | Relational copies for SQL joins and filtering |
| `analysis.json` | Complete versioned analysis product and call accounting |

Field columns in the wide view are distinct from metadata columns. CSV formula
cells are escaped for spreadsheet safety; JSON and SQLite preserve literal
values. No conversion of ranges to scalar values, unit harmonization, averaging
or cross-document identity merge is performed. Use the observation table for
conditions and repeated measurements. Numeric statistics require an explicit
scientific normalization policy; a searchable table is not automatically a
validated quantitative dataset.

## Evidence and semantics

The discovery request reads the whole source. Binding requests receive the same
source, object inventory and batches of up to 12 observations. This lets a distant
definition or relationship support a binding without pretending the original
chunk model saw it. Model quotations are located in the original text; program
checks cannot establish scientific correctness. All semantic results remain
`model_proposed`, with original mechanical warnings and source record pointers.

Identical names alone never merge objects. Objects in different experiments,
sites, samples or simulations must remain distinct when their scopes differ.
Relationships do not imply that a value belongs to every ancestor. Multiple
conditions and values remain separate; unknown bindings are retained rather than
assigned to the most frequent or nearest name. Quarantined source declarations
remain separate and are not promoted into normal observations.

This is an initial bounded implementation: simple tasks only, at most 60,000
source characters. Larger sources fail before extraction starts. There is no
silent clipping, global retrieval completeness guarantee, iterative missing-fact
extraction, human acceptance UI, or Jev integration in this release.

## Calls, failure and recovery

Extraction retains its existing `max_model_calls` budget. Analysis has a separate
cumulative `max_analysis_calls` budget (default 12), covering one discovery
request and one per 12 retained observations. The combined upper bound is the sum
of these two limits, not just the extraction budget. Each analysis generation has
one HTTP attempt, a finite timeout and no format regeneration. Analysis freezes
one selected route; it does not perform a hidden provider or protocol switch.

Original extraction output is checkpointed before analysis starts. Analysis
responses and reservations are stored under `diagnostics/analysis/`, including
`checkpoint.json`. An interrupted or failed reserved job is never silently sent
again. Completed responses can be validated and reused; pending work can continue
within the cumulative budget. A damaged snapshot or changed source, task, table
subject or model identity is rejected.

Resume into a **new** output directory using the original extraction checkpoint:

```powershell
python -m attestextract extract --input input/paper.md --task task.json --model-id deepseek:deepseek-flash --table-subject "Individual specimens, distinguished by batch and experiment" --resume output/old-run/diagnostics/execution/checkpoint.json --output-dir output/resumed-run --max-analysis-calls 12
```

The adjacent analysis checkpoint is used automatically when present. An explicit
`--analysis-resume` requires the matching `--resume`. Increasing an exhausted
budget authorizes pending jobs only; it does not reissue failed or unknown jobs.
CLI returns a nonzero status for incomplete analysis, while preserving extraction
results. `complete` means the planned analysis jobs finished, not that every
binding is known or an expert has accepted the data.

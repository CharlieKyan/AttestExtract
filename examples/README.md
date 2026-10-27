# Synthetic examples

The runnable data is packaged under [`src/attestextract/examples`](../src/attestextract/examples), so it remains available after a wheel installation outside this checkout:

| Example | Source | Task | Fixed completion | Purpose |
| --- | --- | --- | --- | --- |
| `ccus` | `ccus.txt` | `ccus.task.json` | `ccus.response.json` | Two equal pressures at different temperatures, Chinese text and an emoji |
| `procurement` | `procurement.md` | `procurement.task.json` | `procurement.response.json` | Purchase lines in prose and a simple pipe table |

All source material and completion responses were written for this demonstration. They are not experimental findings, literature extracts, real purchase requests, or model-generated evaluation results.

Run `attestextract demo --example all`. This explicitly replays fixed completions through the normal pipeline; it performs no model inference. For live extraction, configure an endpoint and run:

```bash
attestextract extract --input src/attestextract/examples/procurement.md --task src/attestextract/examples/procurement.task.json --output extraction-results.json
```

The live result is produced by the configured model and is not expected to match the replay exactly. Review its candidates and evidence before relying on them.

## D1 manual expected examples

Three additional cases live under [`src/attestextract/examples/d1`](../src/attestextract/examples/d1):

| Case | Intended distinction | Candidate / accepted record count |
| --- | --- | --- |
| `attribute` | One product rating and a deliberately wrong candidate whose quotation is absent | 2 / 1 |
| `observation` | One sample with equal flow values under two different temperatures | 2 / 2 |
| `relation` | An explicitly stated subject, relation verb, and object | 1 / 1 |

Each directory contains `source.txt`, `task.json`, `response.json`, `reviews.json`, and `expected.json`. Every source, value, and label is synthetic. These examples exercise patterns relevant to source review without publishing or reproducing original papers or expert annotations. Their labels use `origin: "synthetic_fixture"`; they are not expert gold or model accuracy results.

`response.json` is a fixed extraction response, not a final answer. `reviews.json` manually labels candidates using zero-based `candidate_index`; duplicate or out-of-range indices fail. `expected.json` is the independent manual semantic target: case type, counts, review states, and accepted record fields with values, quotations, and half-open Unicode character offsets. It excludes run timing and generated identities. Offsets count the exact source string, including newlines.

```bash
attestextract d1 --example all --output d1-archives.json
attestextract d1 --example relation
```

This command replays the fixed extraction response, applies the explicit fixture labels, and returns validated archives with exact documents, candidates, review states, and accepted records. Missing review labels never cause automatic acceptance. The relation example is a minimal three-string record type, not a graph inference or event-processing engine. Use `load_d1_example(name)` from `attestextract.d1_examples` for the Python equivalent.

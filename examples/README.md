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

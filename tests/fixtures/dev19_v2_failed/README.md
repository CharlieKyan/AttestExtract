# Frozen dev19 recovery fixture

Generated using the unmodified source at `fd2f7013c89555f3676b82ad2a87c249d09a016d`
(`0.1.0.dev19`), imported from an isolated local archive before implementing v3.
All input, responses, hostnames and configuration are synthetic. No network was used.

The three paragraph chunks return two valid empty record lists, followed by a
mock `ReadTimeout`. The CLI's exact dev19 backend identity serialization was
supplied to the Python extraction entry point. The frozen v2 checkpoint has
three reserved calls, two completed caches and one failed chunk. `inputs.json`
records the original task, source, configuration and planning inputs.

`python-checkpoint.json` and `python-cache/` are a second frozen run from the
same baseline, without the CLI identity override. They exercise the distinct
legacy Python identity serialization using otherwise identical requests.

Tests must reconstruct the old identity from this fixed artifact; do not
regenerate the expected checkpoint using the current implementation.

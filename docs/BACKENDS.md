# D2: model adapters and acceptance

Dev8 adds named TOML profiles and an explicit registry shared by Python and CLI.
No provider SDK or LangExtract dependency is needed. Existing backend injection,
legacy CLI environment configuration, result schema 0.2 (including reading 0.1),
evidence rules, and opt-in format regeneration remain available.

## Configuration and calls

Use `examples/backends.toml` for two independent official-service profiles.
Credentials belong in environment variables or an explicitly selected `.env`,
never in TOML. No configuration file is auto-discovered.

```powershell
attestextract extract --config examples/backends.toml --profile deepseek --env-file .env --input source.txt --task task.json --output result.json --report review.md --log-dir ../research/attestextract/runs/my-new-run/diagnostics
```

Switching compatible models needs only `--profile qwen` or a new profile. Full
profiles contain `base_url` and `model`; incomplete or unknown fields fail before
any model request. Explicit CLI values override the selected profile; omitted
values retain profile values, then defaults. Profile mode ignores old
`ATTESTEXTRACT_BASE_URL` / `ATTESTEXTRACT_MODEL` variables. Without a profile,
explicit CLI > process environment > explicitly loaded `.env` remains unchanged.
Credentials use the selected `api_key_env`, with process environment > `.env`.
An existing empty environment value does not fall back to the file.

| Setting | Default / behavior |
| --- | --- |
| `adapter` | `openai-compatible`; must be registered by application code |
| `base_url`, `model` | Required; compatible adapter appends `/chat/completions` |
| `api_key_env` | `ATTESTEXTRACT_API_KEY`; local unauthenticated services remain possible |
| `timeout` | 60 seconds; HTTPX per-operation timeout, not a total wall-clock deadline |
| `max_attempts` | 3 HTTP attempts per generation; use 1 for bounded acceptance |
| `structured_output` | `off`, `json_object`, or `json_schema`; default `off` |
| `format_retries` | 0; explicitly setting 1 can add one generation per chunk |
| `generation.temperature` | Optional number from 0 through 2; otherwise omitted |
| `generation.max_output_tokens` | Optional positive integer; otherwise omitted |
| `output_token_parameter` | `max_tokens`; may explicitly select `max_completion_tokens` |
| `provider_options` | Finite JSON-compatible values added at the request body's top level |

`--max-attempts`, `--temperature`, `--max-output-tokens`, `--structured-output`,
`--format-retries`, `--timeout`, `--adapter`, `--base-url`, `--model`, and
`--api-key-env` override individual profile settings. Token field mapping and
provider-specific options are configured in TOML or Python.

Provider options cannot override model/messages/output format, generation controls,
streaming, tools, number of completions, or connection/credential fields. They are
never silently dropped. A service rejection preserves its bounded diagnostic;
HTTP 400/422 fails without fallback. An HTTP 200 alone cannot establish whether
the server honored an optional parameter. `json_object` asks for valid JSON, not
server-enforced task Schema. All modes still undergo local validation.

The supplied examples disable thinking with `thinking = { type = "disabled" }`
for DeepSeek and `enable_thinking = false` for Qwen, and request JSON object output.
They are configurations to test, not a capability guarantee for other versions.
The official sources consulted on 2026-10-30 are
[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
and [Qwen compatible API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions).
Qwen's Beijing public address remains supported; use your Beijing workspace
address in a local profile if preferred. Aliases can change, so save observed
response metadata rather than treating a configured model alias as a snapshot.

## Python and registration

```python
from attestextract import (
    BackendRegistry, default_registry, resolve_backend_config, extract, load_task,
)
from attestextract.envconfig import load_env_file

environment = load_env_file(".env")
config = resolve_backend_config(
    config_path="examples/backends.toml", profile="deepseek",
    overrides={}, environment=environment,
)
backend = default_registry().create(
    config.adapter, config=config, api_key=environment.get(config.api_key_env),
)
result = extract(
    "Lamp A uses 8 W.", load_task("task.json"), backend=backend,
    generation=config.generation, format_retries=config.format_retries,
)
```

For another protocol, implement `generate(ExtractionRequest) -> BackendResponse`.
Map unified messages, Schema and generation controls into the native request;
map native text, usage and completion state back, raising explicit framework
errors for unusable responses. Unsupported explicit controls must fail instead
of being ignored. `prepare_payload(request)`, when provided for diagnostics,
must be pure: no network, response consumption or request mutation. The traced
path calls it for preview and the adapter may call it again when sending.

```python
from attestextract import default_registry
from attestextract.cli import main

registry = default_registry()
registry.register("my-native-adapter", my_factory)
# my_factory(*, config, api_key=None, trace=None) returns an ExtractionBackend.
# The selected TOML profile specifies adapter = "my-native-adapter".
raise SystemExit(main(registry=registry))
```

There is no dynamic Python import from TOML, plugin auto-discovery or global
mutable registry. Duplicate and unknown names fail explicitly. Each application
bootstrap registers its adapters and invokes the same CLI. The standard
`attestextract` command includes only the compatible production adapter.

`adapter_examples.SyntheticEnvelopeBackend` is a deliberately simulated second
protocol. It maps `deployment` / `input` and `output.text` / `metrics` / `status`
through an HTTPX mock transport. It cannot contact a real endpoint. Tests register
it by name and run the same `extract` and CLI with three synthetic D1 tasks.
It declares `mode="replay"`; ordinary injected backends retain default `live`.
This mode is a backend declaration, not independent proof of network activity.

## Response and error contracts

`BackendResponse` retains `text`, `model`, `usage`, `attempts`, `elapsed_seconds`.
New optional metadata is `reported_model` (only what the service reports),
`finish_reason`, and `request_id` (allowlisted response header, sanitized/bounded).
Missing metadata stays `None`; `model` keeps its configured-name fallback for
existing callers. Request IDs are not invented from response completion IDs.
Usage retains nonnegative integer provider counts; absence is not zero usage.

`BackendError` retains its original attributes and adds `code` and `retryable`.
Codes include `timeout`, `transport_error`, `authentication_error`,
`permission_denied`, `not_found`, `rate_limited`, `server_error`, `invalid_request`,
`http_error`, and `backend_error`. `InvalidResponseError` uses `invalid_response`,
`empty_response`, `truncated_response`, `refusal`, or `unsupported_response`.
Provider error codes are separate from framework codes; a generic 400 does not
prove a parameter is unsupported. `retryable` describes the cause, not permission
to exceed the configured attempt limit. Envelope/empty/refusal/truncation errors
do not trigger local format regeneration.

Trace paths and `trace_schema_version: "0.3"` remain unchanged; response metadata
and errors gain additive fields. A replay/unified-request artifact also gains
`generation`. Result schema stays 0.2. These logs may contain private source/model
text and belong outside the public repository.

## Reproducible acceptance

From the checkout, run the synthetic heterogeneous protocol acceptance:

```powershell
python scripts/d2_acceptance.py --mode mock --output-dir ../research/attestextract/runs/d2-mock-new
```

After configuring both official keys and explicitly authorizing model charges:

```powershell
python scripts/d2_acceptance.py --mode live --config examples/backends.toml --env-file .env --profiles deepseek qwen --output-dir ../research/attestextract/runs/d2-live-new
```

The runner uses exactly the three small synthetic D1 sources/tasks per selected
provider, one HTTP attempt and no format regeneration, capped at 2,048 output
tokens each. It preflights all credentials/configuration before calls and refuses
an existing output directory. Expected answers are used only for local checking;
they are never sent as input. The deliberately incorrect 80 W D1 candidate is
not a target: the accepted manual attribute expectation is 8 W.

Inspect `summary.json` and each case's result/report/diagnostics. Assess transport,
format, expected values and exact source evidence separately. Six small calls
cannot establish a general extraction accuracy rate or parameter enforcement.
They also do not validate strict Schema mode, forced live failures, or Jev.
Offline/mock acceptance and live acceptance must always be reported separately.

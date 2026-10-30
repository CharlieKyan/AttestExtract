# D2: model adapters and acceptance

Dev9 adds provider/model selection on top of the dev8 profiles and registry.
Configure credentials once, then change one model identifier in Python or CLI.
No provider SDK or LangExtract dependency is needed. Existing backend injection,
profiles, legacy CLI environment configuration, result schema 0.2 (including
reading 0.1), evidence rules, and opt-in format regeneration remain available.
The [D2 acceptance checklist](D2_ACCEPTANCE.md) records the verification scope.

## Select a model with minimal configuration

Set the keys for the providers you use in the process environment or an explicit
`.env` file:

```dotenv
DEEPSEEK_API_KEY=your-deepseek-key
DASHSCOPE_API_KEY=your-qwen-beijing-key
```

```powershell
attestextract models
attestextract extract --model-id deepseek:deepseek-flash --env-file .env --input source.txt --task task.json --output result.json
```

Change `--model-id` to `qwen:qwen3.8-27b` to use Qwen with the same task and input.
`models` lists local presets without loading credentials or contacting services;
it is not a live model-discovery command. In Python:

```python
from attestextract import extract, load_task

result = extract(
    "Lamp A uses 8 W.", load_task("task.json"),
    model="deepseek:deepseek-flash", env_file=".env",
)
# For Qwen, use model="qwen:qwen3.8-27b" with the same task and source.
```

| Model identifier | Default endpoint | Default provider option |
| --- | --- | --- |
| `deepseek:deepseek-flash` | DeepSeek official | `thinking = { type = "disabled" }` |
| `qwen:qwen3.8-27b` | Qwen Beijing public endpoint | `enable_thinking = false` |
| `qwen:qwen-plus` | Qwen Beijing public endpoint | `enable_thinking = false` |

All three presets default to `json_object`, `max_tokens: 2048`, timeout 60 seconds,
one HTTP attempt and zero format regeneration. The token cap applies per chunk
generation, not to the whole document. Explicit options override these defaults.
Unknown provider/model identifiers fail before a model request; use a complete
profile to specify another model's endpoint and parameters. No model-family
guessing or automatic output-format downgrade is performed.

`--region beijing` is optional for Qwen. Other Qwen regions need a complete
profile with the appropriate endpoint and credential. DeepSeek presets do not
accept a region setting. The existing `--model` option remains a raw model name,
including any literal colon; the new shortcut is `--model-id`. It cannot be
combined with `--model`, `--adapter`, `--config` or `--profile`.

### Credential and address selection

The merged environment follows process environment > explicitly loaded `.env`.
Loading a file never changes the process environment. The model selector always
determines the model: legacy `ATTESTEXTRACT_*` values and dedicated `*_MODEL`
variables do not influence the shortcut.

| Provider/key selection | Associated address variable |
| --- | --- |
| DeepSeek: `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |
| Qwen first choice: `QWEN_API_KEY` | `QWEN_BASE_URL` |
| Qwen second choice: `DASHSCOPE_API_KEY` | `QWEN_BASE_URL` |
| Qwen third choice: `VLM_API_KEY` | `VLM_BASE_URL` |

Qwen selects the first **present** variable in that order. An empty selected key
fails explicitly; it does not fall back to another account. A selected VLM key
uses only its VLM address variable, so stale VLM settings cannot redirect a Qwen
or DashScope credential. Missing address variables use the official default;
an explicitly empty address fails. Automatic addresses must be the selected
provider's official endpoint, with Qwen restricted to Beijing. These routes
include a Beijing workspace URL in `QWEN_BASE_URL` or `VLM_BASE_URL`.

Use `--api-key-env NAME` to choose another credential variable. Python also
accepts `api_key_env="NAME"` or an explicit `api_key=...`; explicit key values
take precedence and empty values fail. The CLI accepts a variable name, never a
key value. Keep real credentials out of TOML and public commits.

Explicit `base_url` / `--base-url` can select a custom compatible service, but
must be paired with an explicit `api_key_env` / `--api-key-env`; default official
credentials are not automatically sent to a custom address. An explicit region
and a custom endpoint cannot be combined. Existing advanced profiles can express
other authentication or endpoint arrangements, including unauthenticated local
services.

### Options and clear failures

Python accepts `generation=GenerationOptions(...)` and
`model_options={"structured_output": "off", "provider_options": {}}` for explicit
changes. A supplied `provider_options` object replaces the preset object, rather
than inheriting its old thinking settings. `model_options` cannot change
`model`, `adapter` or contain `api_key`; use the selector, an injected backend or
the dedicated credential argument. Supply `format_retries` directly to
`extract`, not inside `model_options`. CLI generation controls and timeout remain
available; provider options and token-field mapping use an advanced profile.

Missing credentials fail before HTTP. Authentication, billing, unavailable-model
and request-parameter failures retain the framework error code and bounded
provider diagnostic. CLI hints describe the next configuration check; they do
not silently change models, remove parameters or repeat a failed request.

The retained dev8 live evidence covers `deepseek-flash` and `qwen3.8-27b`: six
HTTP/format successes and 32 exact source spans, with strict content comparison
passing four of six cases. Both observation cases had a sample-name
category-prefix difference; expected values and raw outputs were not rewritten.
Field-level response details remain in the local assessment. This is not a
dev9 live run or live acceptance of `qwen-plus`. Dev9
checks the new selection path offline; engineering acceptance and content
agreement remain separate.

## Advanced profile configuration and calls

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

| Setting | Advanced profile default / behavior |
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
Codes include `timeout`, `transport_error`, `authentication_error`, `billing_error`,
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

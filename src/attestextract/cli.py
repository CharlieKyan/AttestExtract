"""Command line extraction and explicitly labelled offline replay examples."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from . import __version__, extract, load_task
from .backends import ReplayBackend
from .config import resolve_backend_config
from .d1_examples import d1_example_names, load_d1_example
from .diagnostics import RunTrace, redact_text
from .envconfig import load_env_file
from .errors import AttestExtractError, ConfigurationError
from .execution import check_resume_paths, digest, prepare_execution
from .models import ExecutionOptions, TaskSpec
from .providers import available_models, resolve_model_config, resolve_model_credentials
from .progress import ConsoleProgress
from .reporting import render_markdown_report
from .registry import BackendRegistry, default_registry


def _output_token_limit(value: str) -> int | str:
    if value == "provider":
        return value
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use a positive integer or 'provider' for the service default.") from None
    if limit <= 0:
        raise argparse.ArgumentTypeError("Use a positive integer or 'provider' for the service default.")
    return limit


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attestextract",
        description="Schema-driven extraction with source-linked evidence (early prototype).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="List built-in model selectors without network access or credentials")

    demo = commands.add_parser("demo", help="Replay bundled synthetic responses without a model or API key")
    demo.add_argument("--example", choices=("ccus", "procurement", "all"), default="all")
    demo.add_argument("--output", type=Path, help="Write JSON here instead of printing it")

    d1 = commands.add_parser(
        "d1", help="Replay synthetic manual expected examples; no real expert validation",
    )
    d1.add_argument("--example", choices=(*d1_example_names(), "all"), default="all")
    d1.add_argument("--output", type=Path, help="Write archive JSON here instead of printing it")

    live = commands.add_parser("extract", help="Extract UTF-8 text or Markdown using a configured model")
    live.add_argument("--input", type=Path, required=True, help="UTF-8 .txt or .md input")
    live.add_argument("--task", type=Path, required=True, help="JSON task specification")
    live.add_argument("--output", type=Path, help="Destination JSON; omitted: new outputs/<run>/ beside input (a sibling of inputs/ when applicable), with report and diagnostics")
    live.add_argument("--report", type=Path, help="Also write a readable Markdown review of candidates and evidence")
    live.add_argument("--base-url", help="OpenAI-compatible API base URL, or ATTESTEXTRACT_BASE_URL")
    live.add_argument("--model", help="Model name, or ATTESTEXTRACT_MODEL")
    live.add_argument("--model-id", help="Select a built-in provider:model preset; see 'attestextract models'")
    live.add_argument("--region", help="Region for --model-id (Qwen: beijing; other regions use a profile)")
    live.add_argument("--config", type=Path, help="TOML file containing named backend profiles")
    live.add_argument("--profile", help="Profile name in --config; explicit CLI values override it")
    live.add_argument("--adapter", help="Registered backend name (default: openai-compatible)")
    live.add_argument("--api-key-env", help="Environment variable containing the API key (default: ATTESTEXTRACT_API_KEY)")
    live.add_argument("--timeout", type=float, help="HTTP timeout in seconds (default: 60)")
    live.add_argument("--max-attempts", type=int, help="Maximum HTTP attempts per generation (presets: 1; profiles/legacy: 3)")
    live.add_argument("--temperature", type=float, help="Sampling temperature (presets: 0; profiles/legacy: provider default)")
    live.add_argument("--max-output-tokens", type=_output_token_limit, metavar="N|provider",
                      help="Positive output token ceiling, or 'provider' to omit it from the request (not unlimited). Default: presets 8192; profiles/legacy provider default")
    live.add_argument("--env-file", type=Path, help="Explicit KEY=value configuration file; existing environment takes precedence")
    live.add_argument("--log-dir", type=Path, help="Empty directory for source, prompts, responses and diagnostic events")
    live.add_argument(
        "--structured-output", choices=("off", "json_object", "json_schema"),
        help="Output mode (presets: json_object; profiles/legacy: off; no automatic fallback)",
    )
    live.add_argument(
        "--format-retries", type=int, choices=(0, 1),
        help="Regenerate a chunk once after a local output-format failure; adds model calls (default: 0)",
    )
    live.add_argument("--progress", choices=("normal", "quiet", "verbose"), default="normal",
                      help="Terminal detail level (default: normal); raw text stays in diagnostic files")
    live.add_argument("--check", action="store_true", help="Validate local configuration/task and show the chunk plan; no model/network calls")
    live.add_argument("--chunking", choices=("adaptive", "fixed"), default="adaptive",
                      help="Output-complexity planning (default: adaptive); fixed retains the character target")
    live.add_argument("--max-chunk-chars", type=int, default=6000, help="Initial chunk character target (default: 6000; atomic table rows may exceed it)")
    live.add_argument("--max-model-calls", type=int, help="Total generation-call budget including retries and prior resumed calls (default: 3 x initial planned chunks)")
    live.add_argument("--max-split-depth", type=int, default=3, help="Maximum truncation subdivision depth (default: 3; 0 disables subdivision)")
    live.add_argument("--resume", type=Path, help="Previous run directory, diagnostics directory or execution/checkpoint.json; saves a new run")
    return parser


def _demo(example: str) -> dict:
    resources = files("attestextract").joinpath("examples")
    task = TaskSpec.model_validate_json(resources.joinpath(f"{example}.task.json").read_text(encoding="utf-8"))
    extension = "txt" if example == "ccus" else "md"
    source_name = f"{example}.{extension}"
    text = resources.joinpath(source_name).read_bytes().decode("utf-8")
    response = json.loads(resources.joinpath(f"{example}.response.json").read_text(encoding="utf-8"))
    result = extract(text, task, backend=ReplayBackend([response]), source_name=source_name)
    return result.model_dump(mode="json")


def _write(payload: dict, output: Path | None, *, announce: bool = True) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output is None:
        print(serialized, end="")
    else:
        output.write_bytes(serialized.encode("utf-8"))
        if announce:
            print(f"Wrote {output}", file=sys.stderr)


def _default_outputs(args: argparse.Namespace) -> Path | None:
    if args.output is not None:
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    source_directory = args.input.resolve().parent
    root = source_directory.parent if source_directory.name.casefold() == "inputs" else source_directory
    directory = root / "outputs" / stamp
    args.output = directory / "result.json"
    if args.report is None:
        args.report = directory / "report.md"
    if args.log_dir is None:
        args.log_dir = directory / "diagnostics"
    return directory


def _check_output_paths(args: argparse.Namespace) -> None:
    """Reject accidental input overwrites and unwritable layouts before model calls."""
    outputs = [args.output.resolve()]
    if args.report is not None:
        outputs.append(args.report.resolve())
    if len(set(outputs)) != len(outputs):
        raise ConfigurationError("--output and --report must use different files.")
    protected = {args.input.resolve(), args.task.resolve()}
    if args.env_file is not None:
        protected.add(args.env_file.resolve())
    if args.config is not None:
        protected.add(args.config.resolve())
    for target in outputs:
        if target in protected:
            raise ConfigurationError("Output files must differ from the input, task, environment and configuration files.")
        if args.log_dir is not None and target.is_relative_to(args.log_dir.resolve()):
            label = "Output" if target == args.output.resolve() else "Report"
            raise ConfigurationError(f"{label} must be outside --log-dir; diagnostics writes its own result.json.")
        if args.log_dir is not None and args.log_dir.resolve().is_relative_to(target):
            raise ConfigurationError("Output files cannot be parent directories of --log-dir.")
        if target.is_dir() or not target.parent.is_dir():
            raise ConfigurationError("Output files need an existing parent directory and cannot be directories.")


def main(argv: list[str] | None = None, *, registry: BackendRegistry | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = None
    trace = None
    progress = None
    try:
        if args.command == "models":
            print("Built-in model presets (offline list; not a live availability check):")
            for selector in available_models():
                print(selector)
            return 0

        if args.command == "d1":
            print(
                "Synthetic manual expected examples: fixed replay and explicit fixture labels; "
                "no model inference or real expert validation.",
                file=sys.stderr,
            )
            if args.example == "all":
                payload = {"examples": {
                    name: load_d1_example(name).model_dump(mode="json")
                    for name in d1_example_names()
                }}
            else:
                payload = load_d1_example(args.example).model_dump(mode="json")
            _write(payload, args.output)
            return 0

        if args.command == "demo":
            print("Offline replay of fixed synthetic responses; no model inference is performed.", file=sys.stderr)
            if args.example == "all":
                payload = {"examples": {name: _demo(name) for name in ("ccus", "procurement")}}
            else:
                payload = _demo(args.example)
            _write(payload, args.output)
            return 0

        default_directory = _default_outputs(args)
        if args.resume is not None:
            check_resume_paths(args.resume, [path for path in (args.output, args.report, args.log_dir) if path is not None])
        if default_directory is not None:
            default_directory.mkdir(parents=True, exist_ok=False)
        _check_output_paths(args)
        if args.max_chunk_chars < 1:
            raise ConfigurationError("--max-chunk-chars must be positive.")
        if args.resume is not None and args.log_dir is None:
            raise ConfigurationError("Resume requires persistent diagnostics: omit --output or supply a new --log-dir.")
        execution = ExecutionOptions(adaptive_chunking=args.chunking == "adaptive",
                                     max_model_calls=args.max_model_calls, max_split_depth=args.max_split_depth)
        progress = ConsoleProgress(args.progress)
        api_key = os.environ.get(args.api_key_env or "ATTESTEXTRACT_API_KEY")
        trace = RunTrace(args.log_dir, secrets=[api_key] if api_key else [], on_event=progress)
        trace.event("configuration", "started")
        environment = load_env_file(args.env_file) if args.env_file else os.environ
        overrides = {name: getattr(args, name) for name in (
            "adapter", "base_url", "model", "api_key_env", "timeout", "max_attempts",
            "structured_output", "format_retries", "temperature", "max_output_tokens",
        ) if getattr(args, name) is not None}
        if overrides.get("max_output_tokens") == "provider":
            # A nested explicit null clears preset/profile limits. A top-level
            # None would mean "no override" to the existing config resolver.
            overrides.pop("max_output_tokens")
            overrides["generation"] = {"max_output_tokens": None}
        if args.model_id is not None:
            if any(value is not None for value in (args.config, args.profile, args.model, args.adapter)):
                raise ConfigurationError("--model-id cannot be combined with --config, --profile, --model or --adapter.")
            config = resolve_model_config(args.model_id, environment=environment,
                                          overrides=overrides, region=args.region)
            api_key = resolve_model_credentials(config, environment)
        else:
            if args.region is not None:
                raise ConfigurationError("--region requires --model-id; advanced profiles declare their full endpoint.")
            config = resolve_backend_config(overrides=overrides, environment=environment,
                                            config_path=args.config, profile=args.profile)
            api_key = environment.get(config.api_key_env)
        if trace is not None and api_key:
            trace.add_secrets(api_key)
        if args.input.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Input must be a UTF-8 .txt or .md file; convert PDFs externally first")
        if trace is not None:
            trace.event("configuration", "task_validation_started")
        task = load_task(args.task)
        if trace is not None:
            trace.event("document", "started")
        text = args.input.read_bytes().decode("utf-8")
        if trace is not None:
            trace.event("configuration", "backend_validation_started")
        backend = (registry if registry is not None else default_registry()).create(
            config.adapter, config=config, api_key=api_key, trace=trace,
        )
        backend_identity = digest(config.model_dump(mode="json", exclude={"api_key_env"}))
        if trace is not None:
            trace.write_json("backend.json", {
                "backend": backend.name, "model": config.model, "timeout": config.timeout,
                "max_attempts": config.max_attempts, "api_key_configured": bool(api_key),
                "env_file_used": args.env_file is not None,
                "profile": args.profile, "adapter": config.adapter,
                "model_id": args.model_id,
                "selection_mode": "preset" if args.model_id is not None else "profile" if args.profile else "legacy",
                "structured_output": config.structured_output,
                "format_retries": config.format_retries,
                "generation": config.generation.model_dump(mode="json"),
                "output_token_parameter": config.output_token_parameter,
                "tls_mode": config.tls_mode,
            })
            trace.event("configuration", "completed", model=config.model,
                        structured_output=config.structured_output,
                        max_output_tokens=config.generation.max_output_tokens,
                        max_attempts=config.max_attempts, format_retries=config.format_retries)
        if args.check:
            session = prepare_execution(text, task, backend, config.generation, execution,
                                        max_chars=args.max_chunk_chars, format_retries=config.format_retries,
                                        source_name=args.input.name, backend_identity=backend_identity,
                                        resume_from=args.resume)
            plan = {"mode": "local_check", "network_calls": 0, "model": config.model,
                    "character_count": len(text), "chunk_count": len(session.leaves()),
                    "execution": session.summary(), "execution_options": execution.model_dump(mode="json"),
                    "max_http_attempts_upper_bound": (session.state.max_model_calls * backend.max_attempts
                                                      if hasattr(backend, "max_attempts") else None),
                    "structured_output": config.structured_output, "tls_mode": config.tls_mode,
                    "timeout": config.timeout, "max_attempts": config.max_attempts,
                    "format_retries": config.format_retries,
                    "generation": config.generation.model_dump(mode="json"),
                    "output": str(args.output.resolve()), "report": str(args.report.resolve()) if args.report else None,
                    "diagnostics": str(trace.directory) if trace.directory else None,
                    "notice": "Local validation only; credentials, endpoint availability and model output are not verified."}
            trace.checked(plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        result = extract(text, task, backend=backend, source_name=args.input.name, trace=trace,
                         format_retries=config.format_retries, generation=config.generation,
                         execution=execution, resume_from=args.resume, backend_identity=backend_identity,
                         max_chunk_chars=args.max_chunk_chars)
        if trace is not None:
            trace.event("output", "started")
        payload = result.model_dump(mode="json")
        _write(payload, args.output, announce=args.progress != "quiet")
        if args.report is not None:
            report = render_markdown_report(result, secrets=[api_key] if api_key else [])
            args.report.write_bytes(report.encode("utf-8"))
            if args.progress != "quiet":
                print(f"Wrote {args.report}", file=sys.stderr)
        if trace is not None:
            trace.finish(payload)
            if trace.directory is not None and args.progress != "quiet":
                print(f"Run diagnostics: {trace.directory}", file=sys.stderr)
                print(f"Resume checkpoint: {trace.directory / 'execution/checkpoint.json'}", file=sys.stderr)
        return 0
    except KeyboardInterrupt as exc:
        if trace is not None:
            trace.fail(exc)
        print("attestextract: interrupted; any in-flight call keeps its budget reservation.", file=sys.stderr)
        if trace is not None and trace.directory is not None and (trace.directory / "execution/checkpoint.json").is_file():
            print(f"Resume checkpoint: {trace.directory / 'execution/checkpoint.json'}", file=sys.stderr)
        else:
            print("No execution checkpoint was saved yet.", file=sys.stderr)
        return 130
    except (AttestExtractError, OSError, ValueError) as exc:
        if trace is not None:
            trace.fail(exc)
        message = redact_text(str(exc), [api_key] if api_key else [])
        print(f"attestextract: {message}", file=sys.stderr)
        if trace is not None and trace.directory is not None:
            print(f"Run diagnostics: {trace.directory}", file=sys.stderr)
            if (trace.directory / "execution/checkpoint.json").is_file():
                print(f"Resume checkpoint: {trace.directory / 'execution/checkpoint.json'}", file=sys.stderr)
            if getattr(exc, "http_details", None):
                print(f"HTTP diagnostics: {trace.directory / exc.http_details}", file=sys.stderr)
            artifact = getattr(exc, "validation_artifact", None)
            if artifact:
                stem = Path(artifact).name.removesuffix(".errors.json")
                print(f"Original model output: {trace.directory / 'responses' / (stem + '.txt')}", file=sys.stderr)
                print(f"Validation details: {trace.directory / artifact}", file=sys.stderr)
        return 1
    finally:
        if progress is not None:
            progress.close()

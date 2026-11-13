"""Command line extraction and explicitly labelled offline replay examples."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import warnings
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from . import __version__, extract, load_task
from .backends import OpenAICompatibleBackend, ReplayBackend
from .responses_backend import DeepSeekResponsesBackend
from .capabilities import check_structured_output
from .config import resolve_backend_config
from .d1_examples import d1_example_names, load_d1_example
from .diagnostics import RunTrace, redact_text
from .envconfig import load_env_file
from .errors import AttestExtractError, ConfigurationError
from .execution import check_resume_paths, digest, prepare_execution
from .models import ContextOptions, ExecutionOptions, ExtractionResult, TaskSpec
from .documents import Document
from .structure import build_structure
from .contextual_chunking import coverage_report
from .storage import _reject_constant, _unique_object
from .providers import available_models, resolve_model_config, resolve_model_credentials
from .progress import ConsoleProgress
from .reporting import render_markdown_report
from .run_output import prepare_run_directory, save_run_result
from .registry import BackendRegistry, default_registry
from .automatic import AutomaticBackend, ModelConnection, build_automatic_backend
from .task_input import task_summary


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

    review = commands.add_parser("export-review", help="Export compact expert review rows from an existing result; no model calls")
    review.add_argument("--input", type=Path, required=True, help="Saved result.json or its run directory")
    review.add_argument("--output-dir", type=Path, help="New empty directory outside the original run; default: a new sibling")

    prepare = commands.add_parser("prepare-decisions", help="Prepare offline decision materials from a successful checkpointed run")
    prepare.add_argument("--run", type=Path, required=True, help="Successful run directory or its diagnostics directory")
    prepare.add_argument("--output", type=Path, help="New output directory; default: a new sibling of the run")
    check = commands.add_parser("check-decisions", help="Validate simulated decision suggestions without applying them")
    check.add_argument("--bundle", type=Path, required=True, help="decision-input.json prepared offline")
    check.add_argument("--responses", type=Path, required=True, help="JSON envelope with bundle_id and results")
    check.add_argument("--output", type=Path, help="New output directory; default: a new sibling of the bundle directory")

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
    live.add_argument("--task", type=Path, required=True,
                      help="JSON with prompt_description, field_list and optional examples, or a legacy TaskSpec")
    live.add_argument("--output", type=Path, help="Destination JSON; omitted: new outputs/<run>/ beside input (a sibling of inputs/ when applicable), with report and diagnostics")
    live.add_argument("--output-dir", type=Path,
                      help="Exact new or empty run directory for result.json, report.md and diagnostics; cannot combine with --output, --report or --log-dir")
    live.add_argument("--report", type=Path, help="Also write a readable Markdown review of candidates and evidence")
    live.add_argument("--base-url", help="OpenAI-compatible API base URL, or ATTESTEXTRACT_BASE_URL")
    live.add_argument("--model", help="Model name, or ATTESTEXTRACT_MODEL")
    live.add_argument("--model-id", help="Select a built-in provider:model preset; see 'attestextract models'")
    live.add_argument("--region", help="Region for --model-id (Qwen: beijing; other regions use a profile)")
    live.add_argument("--config", type=Path, help="TOML file containing named backend profiles")
    live.add_argument("--profile", help="Profile name in --config; explicit CLI values override it")
    live.add_argument("--adapter", help="Registered backend name (default: openai-compatible)")
    live.add_argument("--api-key-env", help="Environment variable containing the API key (default: ATTESTEXTRACT_API_KEY)")
    live.add_argument("--fallback-base-url", help="Optional explicitly chosen backup API address")
    live.add_argument("--fallback-model", help="Optional backup model's raw name")
    live.add_argument("--fallback-api-key-env", help="Environment variable holding the backup's own key")
    live.add_argument("--timeout", type=float, help="Override all HTTP phase timeouts, 0 < seconds <= 600 (defaults: connect 15, read 180, write 30, pool 15)")
    live.add_argument("--read-timeout", type=float, help="Override only the HTTP read timeout, 0 < seconds <= 600")
    live.add_argument("--tls-mode", choices=("auto", "1.2", "auto_fallback", "tls12_fallback"), help="Connection policy (default: tls12_fallback, TLS 1.2 first with a bounded automatic negotiation fallback)")
    live.add_argument("--max-attempts", type=int, help="Maximum HTTP attempts per generation (default: 1; uncertain request outcomes are never automatically retried)")
    live.add_argument("--temperature", type=float, help="Sampling temperature (presets: 0; profiles/legacy: provider default)")
    live.add_argument("--max-output-tokens", type=_output_token_limit, metavar="N|provider",
                      help="Positive output token ceiling, or 'provider' to omit it from the request (not unlimited). Default: presets 8192; profiles/legacy provider default")
    live.add_argument("--env-file", type=Path, help="Explicit KEY=value configuration file; existing environment takes precedence")
    live.add_argument("--log-dir", type=Path, help="Empty directory for source, prompts, responses and diagnostic events")
    live.add_argument(
        "--structured-output", choices=("auto", "off", "json_object", "json_schema"),
        help="Output mode (explicit address/model: automatic; presets/profiles retain fixed settings)",
    )
    live.add_argument(
        "--format-retries", type=int, choices=(0, 1),
        help="Regenerate a chunk once after a local output-format failure; adds model calls (default: 0; opt in with 1 in CLI or profile)",
    )
    live.add_argument("--progress", choices=("normal", "quiet", "verbose"), default="normal",
                      help="Terminal detail level (default: normal); raw text stays in diagnostic files")
    live.add_argument("--check", action="store_true", help="Validate local configuration/task and show the chunk plan; no model/network calls")
    live.add_argument("--chunking", choices=("adaptive", "fixed"), default="adaptive",
                      help="Output-complexity planning (default: adaptive); fixed retains the character target")
    live.add_argument("--max-chunk-chars", type=int, default=6000, help="Initial chunk character target (default: 6000; atomic table rows may exceed it)")
    live.add_argument("--context", choices=("none", "structure", "neighbors"), default="none",
                      help="Source context policy; neighbors includes structure (default: none until quality validation)")
    live.add_argument("--tables", choices=("auto", "text"), default="auto",
                      help="Recognized complete tables use route A by default; text explicitly retains the older prose protocol")
    live.add_argument("--neighbor-chars", type=int, default=500, help="Maximum characters per neighboring paragraph side (default: 500)")
    live.add_argument("--mineru-content-list", type=Path, help="Optional MinerU content_list JSON mapped to the unchanged input")
    live.add_argument("--structure-hints", type=Path, help="Optional JSON table header_rows/row_header_columns hints")
    live.add_argument("--max-model-calls", type=int, help="Total generation-call budget including retries and prior resumed calls (default: 3 x initial planned chunks)")
    live.add_argument("--max-split-depth", type=int, default=3, help="Maximum truncation subdivision depth (default: 3; 0 disables subdivision)")
    live.add_argument("--resume", type=Path, help="Previous run directory, diagnostics directory or execution/checkpoint.json; saves a new run")
    live.add_argument("--retry-tables", action="store_true", help="With --resume, explicitly retry quarantined table responses once within the cumulative budget; never retry unknown outcomes")
    return parser


def _export_review(args: argparse.Namespace) -> int:
    from .expert_review import save_expert_review

    source = args.input.resolve()
    if source.is_dir():
        source = source / "result.json"
    try:
        payload = json.loads(source.read_bytes().decode("utf-8-sig"),
                             object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        result = ExtractionResult.model_validate(payload)
        if result.run.routes:
            from .automatic_provenance import validate_automatic_provenance
            validate_automatic_provenance(result)
    except (OSError, ValueError, TypeError):
        raise ConfigurationError("Could not read a supported, valid extraction result; no review was exported.") from None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    target = (args.output_dir or source.parent.with_name(source.parent.name + "-review-" + stamp)).resolve()
    if target.is_relative_to(source.parent) or source.parent.is_relative_to(target):
        raise ConfigurationError("Review output must be outside the original run; choose a new sibling directory.")
    directory = prepare_run_directory(target)
    save_expert_review(result, directory, source_result=str(source))
    print(f"Wrote {directory / 'review.json'}")
    print(f"Wrote {directory / 'review.md'}")
    print(f"Start with {directory / 'annotation.md'} or {directory / 'annotation.json'}")
    print("Offline display only; observations were not merged, corrected or accepted.")
    return 0


def _demo(example: str) -> dict:
    resources = files("attestextract").joinpath("examples")
    task = TaskSpec.model_validate_json(resources.joinpath(f"{example}.task.json").read_text(encoding="utf-8"))
    extension = "txt" if example == "ccus" else "md"
    source_name = f"{example}.{extension}"
    text = resources.joinpath(source_name).read_bytes().decode("utf-8")
    response = json.loads(resources.joinpath(f"{example}.response.json").read_text(encoding="utf-8"))
    # These published demo responses use the historical prose protocol.
    result = extract(text, task, backend=ReplayBackend([response]), source_name=source_name, table_mode="text")
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
    if args.output_dir is not None:
        if any(value is not None for value in (args.output, args.report, args.log_dir)):
            raise ConfigurationError("--output-dir cannot be combined with --output, --report or --log-dir.")
        directory = args.output_dir.resolve()
        args.output = directory / "result.json"
        args.report = directory / "report.md"
        args.log_dir = directory / "diagnostics"
        return directory
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


def _check_output_paths(args: argparse.Namespace, *, require_parents: bool = True) -> None:
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
    for name in ("mineru_content_list", "structure_hints"):
        path = getattr(args, name, None)
        if path is not None:
            protected.add(path.resolve())
    for target in outputs:
        if target in protected:
            raise ConfigurationError("Output files must differ from the input, task, environment and configuration files.")
        if args.log_dir is not None and target.is_relative_to(args.log_dir.resolve()):
            label = "Output" if target == args.output.resolve() else "Report"
            raise ConfigurationError(f"{label} must be outside --log-dir; diagnostics writes its own result.json.")
        if args.log_dir is not None and args.log_dir.resolve().is_relative_to(target):
            raise ConfigurationError("Output files cannot be parent directories of --log-dir.")
        if target.is_dir() or (require_parents and not target.parent.is_dir()):
            raise ConfigurationError("Output files need an existing parent directory and cannot be directories.")


def _resume_command(args: argparse.Namespace, checkpoint: Path) -> str:
    """Quote local arguments for the host shell; never embed credential values."""
    import shlex

    command = [str(Path(sys.executable).resolve()), "-m", "attestextract", "extract"]
    omitted = {"command", "output", "output_dir", "report", "log_dir", "resume", "check"}
    for name, value in vars(args).items():
        if name in omitted or value is None:
            continue
        if isinstance(value, bool):
            if value:
                command.append("--" + name.replace("_", "-"))
            continue
        if isinstance(value, Path):
            value = value.resolve()
        command.extend(["--" + name.replace("_", "-"), str(value)])
    command.extend(["--resume", str(checkpoint.resolve())])
    if args.output_dir is not None:
        directory = args.output_dir.resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        destination = directory.with_name(f"{directory.name}-resume-{stamp}")
        command.extend(["--output-dir", str(destination)])
    if any(any(ord(ch) < 32 for ch in part) for part in command):
        return "Resume command unavailable: an argument contains control characters; use the saved checkpoint path."
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def _show_resume(args: argparse.Namespace, trace: RunTrace | None) -> None:
    if trace is None or trace.directory is None:
        return
    checkpoint = trace.directory / "execution/checkpoint.json"
    if checkpoint.is_file():
        print(f"Resume checkpoint: {checkpoint}", file=sys.stderr)
        print("Resume in a new run (may make additional model calls):", file=sys.stderr)
        print(trace._mask_text(_resume_command(args, checkpoint)), file=sys.stderr)


def _decision_output(output: Path | None, sibling: Path, suffix: str, protected: list[Path]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    target = (output if output is not None else sibling.with_name(f"{sibling.name}-{suffix}-{stamp}")).resolve()
    for source in protected:
        source = source.resolve()
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("Decision output must be separate from all input files and run directories.")
    if target.exists():
        raise ValueError("Decision output directory already exists; use a new directory.")
    target.mkdir(parents=True, exist_ok=False)
    return target


def _offline_decisions(args: argparse.Namespace) -> int:
    # Import only on these local paths; no model construction or credentials.
    from .decision_bundle import (decision_bundle_summary, load_decision_bundle,
                                  prepare_decision_bundle, save_decision_bundle)
    from .decision_ledger import (check_decisions, decision_ledger_summary,
                                  load_decision_responses, save_decision_ledger)
    from .decision_reporting import render_decision_bundle, render_decision_ledger

    if args.command == "prepare-decisions":
        bundle = prepare_decision_bundle(args.run)
        source = args.run.resolve()
        if source.name == "diagnostics":
            source = source.parent
        directory = _decision_output(args.output, source, "decisions", [source])
        save_decision_bundle(bundle, directory / "decision-input.json")
        (directory / "decision-report.md").write_text(render_decision_bundle(bundle), encoding="utf-8")
        summary = decision_bundle_summary(bundle)
        code = 0
    else:
        bundle = load_decision_bundle(args.bundle)
        ledger = check_decisions(bundle, load_decision_responses(args.responses))
        directory = _decision_output(args.output, args.bundle.resolve().parent, "checked",
                                     [args.bundle.resolve().parent, args.responses])
        save_decision_ledger(ledger, directory / "decision-ledger.json")
        (directory / "decision-report.md").write_text(render_decision_ledger(ledger), encoding="utf-8")
        summary = decision_ledger_summary(ledger)
        code = 0 if summary["status"] == "valid" else 2
    print(json.dumps({**summary, "output_directory": str(directory)}, ensure_ascii=False, indent=2))
    return code


def main(argv: list[str] | None = None, *, registry: BackendRegistry | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = None
    trace = None
    progress = None
    try:
        if args.command == "export-review":
            return _export_review(args)
        if args.command in {"prepare-decisions", "check-decisions"}:
            return _offline_decisions(args)
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
            if args.output_dir is not None:
                _check_output_paths(args, require_parents=False)
                prepare_run_directory(default_directory, resume_from=args.resume)
            else:
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
        trace.event("configuration", "task_validation_started")
        task = load_task(args.task)
        trace.write_json("task.json", task.model_dump(mode="json"))
        environment = load_env_file(args.env_file) if args.env_file else os.environ
        overrides = {name: getattr(args, name) for name in (
            "adapter", "base_url", "model", "api_key_env", "timeout", "read_timeout", "tls_mode", "max_attempts",
            "structured_output", "format_retries", "temperature", "max_output_tokens",
        ) if getattr(args, name) is not None}
        if overrides.get("max_output_tokens") == "provider":
            # A nested explicit null clears preset/profile limits. A top-level
            # None would mean "no override" to the existing config resolver.
            overrides.pop("max_output_tokens")
            overrides["generation"] = {"max_output_tokens": None}
        automatic_backend = None
        automatic = (registry is None and args.base_url is not None and args.model is not None
                     and args.config is None and args.adapter is None and args.model_id is None
                     and args.structured_output in (None, "auto") and args.max_attempts in (None, 1))
        backup_values = (args.fallback_base_url, args.fallback_model, args.fallback_api_key_env)
        if any(value is not None for value in backup_values) and (not automatic or not all(backup_values)):
            raise ConfigurationError("A backup requires all three fallback options and the automatic address/model entry point.")
        if args.structured_output == "auto" and not automatic:
            raise ConfigurationError("Automatic output selection requires --base-url and --model, without a fixed profile, adapter or repeated HTTP attempts.")
        if automatic:
            if args.region is not None:
                raise ConfigurationError("Use the explicit API address for a region on the automatic entry point.")
            key_name = args.api_key_env or "ATTESTEXTRACT_API_KEY"
            api_key = environment.get(key_name)
            if api_key is None and not args.check and sys.stdin.isatty():
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        api_key = getpass.getpass("API key (hidden): ")
                except (EOFError, getpass.GetPassWarning):
                    raise ConfigurationError(f"No credential supplied; set {key_name} or enter a key in an interactive terminal.") from None
            primary = ModelConnection(base_url=args.base_url, model=args.model,
                                      api_key=api_key if api_key is not None else "offline-check" if args.check else None,
                                      api_key_env=key_name)
            fallback = None
            if all(backup_values):
                fallback_key = environment.get(args.fallback_api_key_env)
                fallback = ModelConnection(base_url=args.fallback_base_url, model=args.fallback_model,
                                           api_key=fallback_key if fallback_key is not None else "offline-check" if args.check else None,
                                           api_key_env=args.fallback_api_key_env)
            automatic_options = {key: value for key, value in overrides.items()
                                 if key not in {"base_url", "model", "api_key_env", "format_retries", "structured_output",
                                                "temperature", "max_output_tokens"}}
            generation = dict(automatic_options.pop("generation", {}))
            for name in ("temperature", "max_output_tokens"):
                if name in overrides:
                    generation[name] = overrides[name]
            if generation:
                automatic_options["generation"] = generation
            automatic_backend = build_automatic_backend(primary, fallback=fallback, trace=trace,
                                                         environment=environment, overrides=automatic_options)
            config = automatic_backend.routes[automatic_backend.preferred_route_id].config.model_copy(
                update={"format_retries": args.format_retries or 0})
        elif args.model_id is not None:
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
        capability = check_structured_output(
            config.base_url, config.model, config.structured_output,
            provider_options=config.provider_options, adapter=config.adapter,
        )
        if trace is not None and api_key:
            trace.add_secrets(api_key)
        if args.input.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Input must be a UTF-8 .txt or .md file; convert PDFs externally first")
        if trace is not None:
            trace.event("document", "started")
        text = args.input.read_bytes().decode("utf-8")
        context = ContextOptions(mode=args.context, neighbor_chars=args.neighbor_chars)
        sidecars = {}
        for name in ("mineru_content_list", "structure_hints"):
            path = getattr(args, name)
            sidecars[name] = (json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object,
                                         parse_constant=_reject_constant) if path is not None else None)
        structure = None
        if context.mode != "none" or any(value is not None for value in sidecars.values()):
            structure = build_structure(text, mineru_content=sidecars["mineru_content_list"],
                                        hints=sidecars["structure_hints"])
        document = Document.from_text(text, source_name=args.input.name,
                                      representation="text/markdown" if args.input.suffix.lower() == ".md" else "text/plain",
                                      structure=structure)
        if trace is not None:
            trace.event("configuration", "backend_validation_started")
        backend = automatic_backend or (registry if registry is not None else default_registry()).create(
            config.adapter, config=config, api_key=api_key, trace=trace,
        )
        if isinstance(backend, AutomaticBackend):
            backend_identity = None
        elif type(backend) in (OpenAICompatibleBackend, DeepSeekResponsesBackend):
            backend_identity = None
            # Only for verifying a dev19 CLI fingerprint; never persisted.
            backend._legacy_cli_config = config.model_dump(
                mode="json", exclude={"api_key_env", "format_retries", "read_timeout"})
        else:
            backend_identity = digest(config.model_dump(mode="json", exclude={
                "api_key_env", "format_retries", "timeout", "read_timeout", "tls_mode", "max_attempts"}))
        if trace is not None:
            trace.write_json("backend.json", {
                "backend": backend.name, "model": config.model, "timeout": config.timeout,
                "read_timeout": config.read_timeout,
                "max_attempts": config.max_attempts, "api_key_configured": bool(api_key),
                "env_file_used": args.env_file is not None,
                "profile": args.profile, "adapter": config.adapter,
                "model_id": args.model_id,
                "selection_mode": "automatic" if automatic else "preset" if args.model_id is not None else "profile" if args.profile else "legacy",
                "structured_output": config.structured_output,
                "output_protocol": "flat-v1",
                "structured_output_capability": capability,
                "format_retries": config.format_retries,
                "generation": config.generation.model_dump(mode="json"),
                "output_token_parameter": config.output_token_parameter,
                "tls_mode": config.tls_mode,
            })
            trace.event("configuration", "completed", model=config.model,
                        output_protocol="flat-v1",
                        structured_output=config.structured_output,
                        max_output_tokens=config.generation.max_output_tokens,
                        max_attempts=config.max_attempts, format_retries=config.format_retries,
                        read_timeout=config.read_timeout or config.timeout or 180,
                        tls_mode=config.tls_mode)
        if args.check:
            trace.event("configuration", "task_ready", **task_summary(task))
            prepare = prepare_execution
            if automatic:
                from .automatic_execution import prepare_automatic_execution
                prepare = prepare_automatic_execution
            session = prepare(text, task, backend, config.generation, execution,
                                        max_chars=args.max_chunk_chars, format_retries=config.format_retries,
                                        source_name=args.input.name, backend_identity=backend_identity,
                                        resume_from=args.resume, context=context, structure=structure, table_mode=args.tables,
                                        retry_tables=args.retry_tables)
            from .pipeline import _validate_cached_responses
            from .document_routing import table_input_for_chunk
            table_inputs = {key: value for key, chunk in session.chunks.items()
                            if (value := table_input_for_chunk(text, chunk, session.structure)) is not None}
            _validate_cached_responses(task, session, table_inputs)
            plan = {"mode": "local_check", "network_calls": 0, "model": config.model,
                    "task": task_summary(task),
                    "character_count": len(text), "chunk_count": len(session.leaves()),
                    "context": context.model_dump(),
                    "table_routing": {"mode": args.tables,
                                      "table_chunks": [key for key in session.leaves() if key in table_inputs],
                                      "table_count": len({value["table_id"] for value in table_inputs.values()}),
                                      "processing_policy": "observe_only",
                                      "notice": "Known table length truncation may split row/cell ownership within the call budget and depth limit; unknown transport outcomes are not resent."},
                    "coverage": coverage_report(text, [session.chunks[key] for key in session.leaves()], max_chars=args.max_chunk_chars),
                    "structure_diagnostics": structure.diagnostics if structure is not None else [],
                    "execution": session.summary(), "execution_options": execution.model_dump(mode="json"),
                    "resume": {"requested": args.resume is not None, "reusable_chunks": len(session.cached),
                               "remaining_chunks": len(session.leaves()) - len(session.cached),
                               "remaining_call_budget": session.state.max_model_calls - session.state.calls_used},
                    "max_http_attempts_upper_bound": (session.state.request_attempts +
                                                      (session.state.max_model_calls - session.state.calls_used) * backend.max_attempts
                                                      if hasattr(backend, "max_attempts") else None),
                    "http_bound_basis": "Observed prior model POST attempts plus remaining reservations times current max_attempts; excludes TLS probes and unobserved interrupted work.",
                    "structured_output": config.structured_output, "output_protocol": "flat-v1", "tls_mode": config.tls_mode,
                    "structured_output_capability": capability,
                    "timeout": config.timeout, "read_timeout": config.read_timeout,
                    "effective_timeouts": {"connect": config.timeout or 15,
                                           "read": config.read_timeout or config.timeout or 180,
                                           "write": config.timeout or 30, "pool": config.timeout or 15},
                    "max_attempts": config.max_attempts,
                    "format_retries": config.format_retries,
                    "generation": config.generation.model_dump(mode="json"),
                    "output": str(args.output.resolve()), "report": str(args.report.resolve()) if args.report else None,
                    "diagnostics": str(trace.directory) if trace.directory else None,
                    "notice": "Local validation only; credentials, endpoint availability and model output are not verified."}
            trace.checked(plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        result = extract(document, task, backend=backend, source_name=args.input.name, trace=trace,
                         format_retries=config.format_retries, generation=config.generation,
                         execution=execution, resume_from=args.resume, backend_identity=backend_identity,
                         max_chunk_chars=args.max_chunk_chars, context=context, table_mode=args.tables,
                         retry_tables=args.retry_tables)
        payload = result.model_dump(mode="json")
        if (default_directory is not None
                and args.report.resolve() == (default_directory / "report.md").resolve()):
            save_run_result(result, default_directory.resolve(), trace)
            if args.progress != "quiet":
                print(f"Wrote {args.output}", file=sys.stderr)
                print(f"Wrote {args.report}", file=sys.stderr)
        else:
            if trace is not None:
                trace.event("output", "started")
            _write(payload, args.output, announce=args.progress != "quiet")
            if args.report is not None:
                report = render_markdown_report(result, secrets=[api_key] if api_key else [])
                args.report.write_bytes(report.encode("utf-8"))
                if args.progress != "quiet":
                    print(f"Wrote {args.report}", file=sys.stderr)
            if default_directory is not None:
                from .expert_review import save_expert_review
                save_expert_review(result, default_directory.resolve(), secrets=trace._secrets)
            if trace is not None:
                trace.finish(payload)
        if trace is not None:
            if trace.directory is not None and args.progress != "quiet":
                print(f"Run diagnostics: {trace.directory}", file=sys.stderr)
                print(f"Resume checkpoint: {trace.directory / 'execution/checkpoint.json'}", file=sys.stderr)
        if result.format_status != "complete":
            print("attestextract: " + result.format_status + "; legal records saved; inspect table_results.rejections.", file=sys.stderr)
            return 2
        return 0
    except KeyboardInterrupt as exc:
        if trace is not None:
            trace.fail(exc)
        print("attestextract: interrupted; any in-flight call keeps its budget reservation.", file=sys.stderr)
        if trace is not None and trace.directory is not None and (trace.directory / "execution/checkpoint.json").is_file():
            _show_resume(args, trace)
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
                _show_resume(args, trace)
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

"""Command line extraction and explicitly labelled offline replay examples."""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from . import __version__, extract, load_task
from .backends import OpenAICompatibleBackend, ReplayBackend
from .d1_examples import d1_example_names, load_d1_example
from .diagnostics import RunTrace, redact_text
from .envconfig import load_env_file
from .errors import AttestExtractError, ConfigurationError
from .models import TaskSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attestextract",
        description="Schema-driven extraction with source-linked evidence (early prototype).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

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
    live.add_argument("--output", type=Path, required=True, help="Destination JSON file")
    live.add_argument("--base-url", help="OpenAI-compatible API base URL, or ATTESTEXTRACT_BASE_URL")
    live.add_argument("--model", help="Model name, or ATTESTEXTRACT_MODEL")
    live.add_argument("--api-key-env", default="ATTESTEXTRACT_API_KEY", help="Environment variable containing the API key")
    live.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds (default: 60)")
    live.add_argument("--env-file", type=Path, help="Explicit KEY=value configuration file; existing environment takes precedence")
    live.add_argument("--log-dir", type=Path, help="Empty directory for source, prompts, responses and diagnostic events")
    live.add_argument(
        "--structured-output", choices=("off", "json_schema"), default="off",
        help="Opt in to a provider's strict JSON Schema response format (default: off)",
    )
    live.add_argument(
        "--format-retries", type=int, choices=(0, 1), default=0,
        help="Regenerate a chunk once after a local output-format failure; adds model calls (default: 0)",
    )
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


def _write(payload: dict, output: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output is None:
        print(serialized, end="")
    else:
        output.write_bytes(serialized.encode("utf-8"))
        print(f"Wrote {output}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = None
    trace = None
    try:
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

        api_key = os.environ.get(args.api_key_env)
        if args.log_dir is not None:
            if args.output.resolve().is_relative_to(args.log_dir.resolve()):
                raise ConfigurationError(
                    "Output must be outside --log-dir; diagnostics writes its own result.json."
                )
            trace = RunTrace(
                args.log_dir, secrets=[api_key] if api_key else [],
                progress=lambda message: print(message, file=sys.stderr),
            )
            trace.event("configuration", "started")
        environment = load_env_file(args.env_file) if args.env_file else os.environ
        api_key = environment.get(args.api_key_env)
        if trace is not None and api_key:
            trace.add_secrets(api_key)
        if args.input.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Input must be a UTF-8 .txt or .md file; convert PDFs externally first")
        base_url = args.base_url or environment.get("ATTESTEXTRACT_BASE_URL")
        model = args.model or environment.get("ATTESTEXTRACT_MODEL")
        if not base_url or not model:
            raise ValueError("Configure --base-url and --model, or ATTESTEXTRACT_BASE_URL and ATTESTEXTRACT_MODEL")
        if trace is not None:
            trace.event("configuration", "task_validation_started")
        task = load_task(args.task)
        if trace is not None:
            trace.event("document", "started")
        text = args.input.read_bytes().decode("utf-8")
        if trace is not None:
            trace.event("configuration", "backend_validation_started")
        backend = OpenAICompatibleBackend(
            base_url=base_url, model=model, api_key=api_key, timeout=args.timeout, trace=trace,
            structured_output=args.structured_output,
        )
        if trace is not None:
            trace.write_json("backend.json", {
                "backend": backend.name, "model": model, "timeout": args.timeout,
                "max_attempts": 3, "api_key_configured": bool(api_key),
                "env_file_used": args.env_file is not None,
                "structured_output": args.structured_output,
                "format_retries": args.format_retries,
            })
            trace.event("configuration", "completed")
        result = extract(text, task, backend=backend, source_name=args.input.name, trace=trace,
                         format_retries=args.format_retries)
        if trace is not None:
            trace.event("output", "started")
        payload = result.model_dump(mode="json")
        _write(payload, args.output)
        if trace is not None:
            trace.finish(payload)
            print(f"Run diagnostics: {trace.directory}", file=sys.stderr)
        return 0
    except (AttestExtractError, OSError, ValueError) as exc:
        if trace is not None:
            trace.fail(exc)
        message = redact_text(str(exc), [api_key] if api_key else [])
        print(f"attestextract: {message}", file=sys.stderr)
        if trace is not None and getattr(exc, "http_details", None):
            print(f"HTTP diagnostics: {trace.directory / exc.http_details}", file=sys.stderr)
        return 1

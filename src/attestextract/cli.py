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
from .errors import AttestExtractError
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

    live = commands.add_parser("extract", help="Extract UTF-8 text or Markdown using a configured model")
    live.add_argument("--input", type=Path, required=True, help="UTF-8 .txt or .md input")
    live.add_argument("--task", type=Path, required=True, help="JSON task specification")
    live.add_argument("--output", type=Path, required=True, help="Destination JSON file")
    live.add_argument("--base-url", help="OpenAI-compatible API base URL, or ATTESTEXTRACT_BASE_URL")
    live.add_argument("--model", help="Model name, or ATTESTEXTRACT_MODEL")
    live.add_argument("--api-key-env", default="ATTESTEXTRACT_API_KEY", help="Environment variable containing the API key")
    live.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds (default: 60)")
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
        output.write_text(serialized, encoding="utf-8")
        print(f"Wrote {output}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = None
    try:
        if args.command == "demo":
            print("Offline replay of fixed synthetic responses; no model inference is performed.", file=sys.stderr)
            if args.example == "all":
                payload = {"examples": {name: _demo(name) for name in ("ccus", "procurement")}}
            else:
                payload = _demo(args.example)
            _write(payload, args.output)
            return 0

        if args.input.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Input must be a UTF-8 .txt or .md file; convert PDFs externally first")
        base_url = args.base_url or os.environ.get("ATTESTEXTRACT_BASE_URL")
        model = args.model or os.environ.get("ATTESTEXTRACT_MODEL")
        if not base_url or not model:
            raise ValueError("Configure --base-url and --model, or ATTESTEXTRACT_BASE_URL and ATTESTEXTRACT_MODEL")
        api_key = os.environ.get(args.api_key_env)
        task = load_task(args.task)
        text = args.input.read_bytes().decode("utf-8")
        backend = OpenAICompatibleBackend(
            base_url=base_url, model=model, api_key=api_key, timeout=args.timeout
        )
        result = extract(text, task, backend=backend, source_name=args.input.name)
        _write(result.model_dump(mode="json"), args.output)
        return 0
    except (AttestExtractError, OSError, ValueError) as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        print(f"attestextract: {message}", file=sys.stderr)
        return 1

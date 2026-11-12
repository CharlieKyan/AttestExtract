"""A new run directory shared by the ordinary Python and CLI entry points."""

from __future__ import annotations

import json
from pathlib import Path

from .diagnostics import RunTrace
from .errors import ConfigurationError
from .models import ExtractionResult
from .reporting import render_markdown_report


def prepare_run_directory(directory: str | Path, *, resume_from: str | Path | None = None) -> Path:
    """Use an exact, empty destination; never write into a previous run."""
    if not isinstance(directory, (str, Path)):
        raise ConfigurationError("output_dir must be a directory path.")
    target = Path(directory).resolve()
    if resume_from is not None:
        from .execution import check_resume_paths

        check_resume_paths(resume_from, [target])
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ConfigurationError("Output directory must be empty; choose a new directory for each run.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_run_result(result: ExtractionResult, directory: Path, trace: RunTrace) -> None:
    """Save the result and report before marking diagnostics complete."""
    trace.event("output", "started")
    payload = result.model_dump(mode="json")
    serialized = json.dumps(trace._mask(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    report = render_markdown_report(result, secrets=trace._secrets)
    # Exclusive files also protect against a user placing files here mid-run.
    with (directory / "result.json").open("x", encoding="utf-8", newline="") as stream:
        stream.write(serialized)
    with (directory / "report.md").open("x", encoding="utf-8", newline="") as stream:
        stream.write(report)
    trace.finish(payload)

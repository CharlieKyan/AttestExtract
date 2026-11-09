"""Prepare, check, run and report a bounded, private provider experiment.

Preparation/check/report are offline. Run requires an explicit --live switch;
obtain approval for the frozen paid request list before using it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from attestextract.envconfig import load_env_file
from attestextract.errors import AttestExtractError

from provider_experiment_bundle import prepare_bundle, load_bundle
from provider_experiment_runner import run_stage, write_report, load_results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Freeze the 108-slot experiment without network access")
    prepare.add_argument("--input", required=True, type=Path)
    prepare.add_argument("--task", required=True, type=Path)
    prepare.add_argument("--env-file", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--checklist", type=Path, help="Optional independently reviewed source anchors to freeze")
    for name in ("check", "report", "run"):
        command = sub.add_parser(name)
        command.add_argument("--bundle", required=True, type=Path)
        if name == "run":
            command.add_argument("--env-file", required=True, type=Path)
            command.add_argument("--stage", choices=("A", "B", "C", "D"), required=True)
            command.add_argument("--round", type=int, choices=(1, 2, 3))
            command.add_argument("--live", action="store_true", help="Explicitly execute the approved numbered requests")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            checklist = json.loads(args.checklist.read_text(encoding="utf-8")) if args.checklist else None
            prepare_bundle(args.output, args.input, args.task, load_env_file(args.env_file), paper_checklist=checklist)
            print(json.dumps({"prepared": str(args.output.resolve()), "network_calls": 0,
                              "planned_calls": 108, "manifest": "manifest.json"}, ensure_ascii=False))
        elif args.command == "run":
            if not args.live:
                parser.error("run needs --live after approval of the frozen request list")
            summary = run_stage(args.bundle, load_env_file(args.env_file), args.stage, args.round)
            print(json.dumps(summary["counts"], ensure_ascii=False))
        else:
            bundle = load_bundle(args.bundle)
            root = args.bundle.resolve() / "results"
            if args.command == "report":
                summary = write_report(root, bundle)
                print(json.dumps({"report": str(root / "report.md"), "counts": summary["counts"],
                                  "network_calls": 0}, ensure_ascii=False))
            else:
                results = load_results(root, bundle["rows"])
                print(json.dumps({"checked": True, "planned_calls": len(bundle["rows"]),
                                  "existing_outcomes": len(results), "network_calls": 0}, ensure_ascii=False))
        return 0
    except AttestExtractError as error:
        print(f"Provider experiment: {error}")
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("Provider experiment could not read, validate or save its artifacts; no exception values are printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

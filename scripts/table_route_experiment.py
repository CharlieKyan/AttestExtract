"""Prepare, inspect, replay or explicitly run visible A-only table experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from attestextract.envconfig import load_env_file
from attestextract.errors import AttestExtractError
from table_route_runner import (fork_replay, inspect_bundle, prepare_bundle, read_json,
                                run_bundle, summarize_bundle, write_report)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Freeze 1–4 cases and 1 or 2 rounds, without network access")
    prepare.add_argument("--cases", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--backend-config", type=Path)
    prepare.add_argument("--rounds", type=int, choices=(1, 2), default=1,
                         help="Rounds per case: 1 (default) for at most 4 requests; 2 for at most 8")
    fork = sub.add_parser("fork-replay", help="Import saved A responses into a new offline bundle, optionally with overrides")
    fork.add_argument("--bundle", required=True, type=Path)
    fork.add_argument("--overrides", type=Path)
    fork.add_argument("--output", required=True, type=Path)
    for name in ("check", "summarize", "replay", "run", "report"):
        command = sub.add_parser(name)
        command.add_argument("--bundle", required=True, type=Path)
        if name == "run":
            command.add_argument("--live", action="store_true")
            command.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = prepare_bundle(args.output, read_json(args.cases),
                                      read_json(args.backend_config) if args.backend_config else None,
                                      rounds=args.rounds)
            result = {"network_calls": 0, "planned_slots": manifest["max_calls"], "bundle": str(args.output.resolve())}
        elif args.command == "fork-replay":
            manifest = fork_replay(args.bundle, args.output, read_json(args.overrides) if args.overrides else {})
            result = {"network_calls": 0, "planned_slots": manifest["max_calls"], "bundle": str(args.output.resolve()),
                      "replay_only": True}
        elif args.command == "check":
            result = inspect_bundle(args.bundle)
        elif args.command == "summarize":
            result = summarize_bundle(args.bundle)
        elif args.command == "report":
            result = {"network_calls": 0, "report": str(write_report(args.bundle))}
        else:
            if args.command == "run" and not args.live:
                parser.error("run requires --live and approval of the frozen paid request list")
            result = run_bundle(args.bundle, mode="live" if args.command == "run" else "replay",
                                environment=load_env_file(args.env_file) if args.command == "run" else None)
            result["report"] = str(write_report(args.bundle))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except AttestExtractError as error:
        print(f"Table experiment: {error}")
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("Could not validate or save table experiment artifacts; private values are omitted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Private, frozen A-only table experiments with visible steps and no retries.

Replay is a synthetic/offline demonstration, never a real endpoint result.
The ledger reserves a numbered slot before generation and never resends it.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time

import attestextract
from attestextract.config import BackendConfig
from attestextract.diagnostics import RunTrace, redact_text
from attestextract.errors import BackendError, ConfigurationError
from attestextract.models import BackendResponse, ExtractionRequest, TaskSpec
from attestextract.registry import default_registry
from attestextract.storage import _reject_constant, _unique_object


DEFAULT_BACKEND = {
    "adapter": "deepseek-responses", "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY",
    "max_attempts": 1, "format_retries": 0, "structured_output": "json_object",
    "tls_mode": "1.2", "output_token_parameter": "max_output_tokens",
    "generation": {"temperature": 0.0, "max_output_tokens": 4096},
    "provider_options": {"reasoning": {"effort": "none"}},
}
ARTIFACTS = ("cases.json", "config.json", "slots.json", "table-inputs.json", "requests.json", "replay-overrides.json")
SCHEMA_VERSION = "table-a-0.3"
OBSERVED_PREVIOUS_SCHEMA_VERSION = "table-a-0.2"
PREVIOUS_A_SCHEMA_VERSION = "table-a-0.1"
LEGACY_SCHEMA_VERSION = "table-routes-0.1"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def runtime_identity() -> dict:
    package = Path(attestextract.__file__).resolve().parent
    scripts = Path(__file__).resolve().parent
    return {"package_version": attestextract.__version__, "python": platform.python_version(),
            "package_files": {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(package.rglob("*.py"))},
            "scripts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(scripts.glob("table_route*.py"))}}


def _private_root(output: Path) -> Path:
    target = output.resolve()
    repo = Path(__file__).resolve().parents[1]
    package = Path(attestextract.__file__).resolve().parent
    forbidden_roots = [package]
    for candidate in (repo, package.parent.parent):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/attestextract").is_dir():
            forbidden_roots.append(candidate)
    for forbidden in forbidden_roots:
        if target == forbidden or target.is_relative_to(forbidden):
            raise ConfigurationError("Table experiment output must be outside the public checkout and package.")
    return target


def _config(value: dict | None) -> dict:
    try:
        config = BackendConfig.model_validate(DEFAULT_BACKEND if value is None else value)
        if (config.max_attempts != 1 or config.format_retries != 0
                or config.generation.max_output_tokens != 4096 or config.generation.temperature != 0.0):
            raise ValueError()
        # Validate endpoint and adapter options without invoking generate or /models.
        default_registry().create(config.adapter, config=config)
    except (ValueError, TypeError, ConfigurationError):
        raise ConfigurationError("Use a valid backend with 1 attempt, 0 format retries, temperature 0 and 4096 output tokens.") from None
    return config.model_dump(mode="json")


def _slot_schedule(cases: list[dict], rounds: int) -> list[dict]:
    if type(rounds) is not int or rounds not in (1, 2):
        raise ConfigurationError("Choose 1 or 2 rounds; four cases allocate at most 4 or 8 requests.")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        raise ConfigurationError("Use 1 to 4 cases; each round allocates at most 4 requests.")
    identifiers = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}", case["id"]):
            raise ConfigurationError("Cases need unique safe identifiers.")
        if case["id"] in identifiers:
            raise ConfigurationError("Cases need unique identifiers.")
        identifiers.append(case["id"])
    slots = []
    for round_number in range(1, rounds + 1):
        for case_id in identifiers:
            slots.append({"slot_id": f"r{round_number}-{case_id}-A", "case_id": case_id,
                          "round": round_number, "stage": "A", "depends_on": None})
    return slots


def _legacy_slot_schedule(cases: list[dict], rounds: int) -> list[dict]:
    """Describe historic slots solely to verify read-only imports, never execute them."""
    slots = []
    for slot in _slot_schedule(cases, rounds):
        prefix = f"r{slot['round']}-{slot['case_id']}"
        stages = ("A", "B1", "B2") if slot["round"] == 1 else ("B1", "B2", "A")
        for stage in stages:
            slots.append({**slot, "slot_id": f"{prefix}-{stage}", "stage": stage,
                          "depends_on": f"{prefix}-B1" if stage == "B2" else None})
    return slots


def prepare_bundle(output: Path, cases: list[dict], backend_config: dict | None = None,
                   *, replay_overrides: dict | None = None, replay_only: bool = False,
                   rounds: int = 1, replay_provenance: dict | None = None,
                   frozen_table_inputs: dict | None = None) -> dict:
    from attestextract.table_routes import build_route_request, build_table_input
    root = _private_root(output)
    if root.exists():
        raise ConfigurationError("Choose a new experiment directory; existing bundles are never overwritten.")
    slots = _slot_schedule(cases, rounds)
    if frozen_table_inputs is not None and set(frozen_table_inputs) != {c["id"] for c in cases}:
        raise ConfigurationError("Imported table inputs must match the frozen cases.")
    inputs = {}
    for case in cases:
        for field in ("replays", "expected_diagnostics"):
            if not isinstance(case.get(field, {}), dict) or set(case.get(field, {})) - {"A"}:
                raise ConfigurationError("New table-a bundles accept only A replays and diagnostics; use fork-replay for legacy data.")
        if not isinstance(case.get("source"), str):
            raise ConfigurationError("Cases need source strings.")
        TaskSpec.model_validate(case["task"])
        if frozen_table_inputs is None:
            inputs[case["id"]] = build_table_input(case["source"], table_id=case.get("table_id"),
                                                  mineru_content=case.get("mineru_content"))
        else:
            inputs[case["id"]] = deepcopy(frozen_table_inputs[case["id"]])
    config = _config(backend_config)
    requests = {}
    by_id = {case["id"]: case for case in cases}
    for slot in slots:
        case = by_id[slot["case_id"]]
        requests[slot["slot_id"]] = build_route_request(
            "A", case["task"], inputs[case["id"]],
            chunk_id=slot["slot_id"]).model_dump(mode="json")
    overrides = replay_overrides or {}
    if (not isinstance(overrides, dict) or set(overrides) - {s["slot_id"] for s in slots}
            or any(not isinstance(value, (dict, str)) for value in overrides.values())):
        raise ConfigurationError("Replay overrides must map known slot IDs to JSON objects or raw strings.")
    artifacts = dict(zip(ARTIFACTS, (cases, config, slots, inputs, requests, overrides)))
    provenance = replay_provenance or {}
    if set(provenance) - {s["slot_id"] for s in slots} or any(
            set(item) != {"request.json", "response.json", "origin.json"}
            for item in provenance.values()):
        raise ConfigurationError("Replay provenance must describe known A slots and original request/response artifacts.")
    manifest = {"schema_version": SCHEMA_VERSION, "processing_policy": "observe_only",
                "max_calls": len(slots), "rounds": rounds,
                "max_output_tokens": 4096, "created_at": datetime.now(timezone.utc).isoformat(),
                "runtime": runtime_identity(), "artifacts": {key: digest(value) for key, value in artifacts.items()},
                "replay_only": replay_only or bool(overrides) or bool(provenance),
                "replay_provenance": {slot: {name: digest(value) for name, value in files.items()}
                                      for slot, files in provenance.items()},
                "notice": "Only A is executable. Gold and replay outputs are hidden from models."}
    root.mkdir(parents=True)
    for name, value in artifacts.items():
        write_new(root / name, value)
    for slot, files in provenance.items():
        for name, value in files.items():
            write_new(root / "replay-provenance" / slot / name, value)
    write_new(root / "manifest.json", manifest)
    return manifest


def load_bundle(root: Path, *, check_runtime: bool = True) -> dict:
    return _load_bundle(root, check_runtime=check_runtime, allow_legacy=False)


def _load_bundle(root: Path, *, check_runtime: bool, allow_legacy: bool) -> dict:
    root = _private_root(root)
    manifest = read_json(root / "manifest.json")
    legacy = manifest.get("schema_version") == LEGACY_SCHEMA_VERSION
    if manifest.get("schema_version") != SCHEMA_VERSION and not allow_legacy:
        raise ConfigurationError("Previous bundles are read-only. View the saved REPORT.html; use fork-replay to import saved A responses into a new offline bundle.")
    if (manifest.get("schema_version") not in {SCHEMA_VERSION, OBSERVED_PREVIOUS_SCHEMA_VERSION,
                                              PREVIOUS_A_SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}
            or set(manifest.get("artifacts", {})) != set(ARTIFACTS)):
        raise ConfigurationError("Invalid table experiment manifest.")
    values = {}
    for name in ARTIFACTS:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ConfigurationError("Frozen experiment artifact escaped its directory.")
        value = read_json(path)
        if digest(value) != manifest["artifacts"][name]:
            raise ConfigurationError("Frozen experiment data changed; prepare a new bundle.")
        values[name] = value
    if check_runtime and manifest["runtime"] != runtime_identity():
        raise ConfigurationError("Experiment code or Python changed; prepare a new bundle before running.")
    slots = values["slots.json"]
    schedule = _legacy_slot_schedule if legacy else _slot_schedule
    expected_slots = schedule(values["cases.json"], manifest.get("rounds"))
    if (slots != expected_slots or type(manifest.get("max_calls")) is not int
            or manifest["max_calls"] != len(expected_slots)):
        raise ConfigurationError("Invalid bounded slot schedule.")
    if not legacy:
        known_slots = {slot["slot_id"] for slot in slots}
        if (set(values["requests.json"]) != known_slots
                or set(values["replay-overrides.json"]) - known_slots):
            raise ConfigurationError("Frozen requests and overrides must refer only to planned A slots.")
        for case in values["cases.json"]:
            for field in ("replays", "expected_diagnostics"):
                if not isinstance(case.get(field, {}), dict) or set(case.get(field, {})) - {"A"}:
                    raise ConfigurationError("Only A replays and diagnostics are supported in table-a bundles.")
    _config(values["config.json"])
    provenance = {}
    for slot, hashes in manifest.get("replay_provenance", {}).items():
        if (slot not in {s["slot_id"] for s in slots if s["stage"] == "A"}
                or set(hashes) != {"request.json", "response.json", "origin.json"}
                or not manifest.get("replay_only")):
            raise ConfigurationError("Invalid replay provenance manifest.")
        files = {}
        for name, expected in hashes.items():
            path = root / "replay-provenance" / slot / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ConfigurationError("Replay provenance escaped its directory.")
            value = read_json(path)
            if digest(value) != expected:
                raise ConfigurationError("Replay provenance changed.")
            files[name] = value
        if (not files["origin.json"].get("overridden")
                and values["replay-overrides.json"].get(slot) != files["response.json"].get("text")):
            raise ConfigurationError("Imported replay text differs from original response.")
        provenance[slot] = files
    return {"manifest": manifest, "cases": values["cases.json"], "config": values["config.json"],
            "slots": slots, "table_inputs": values["table-inputs.json"], "requests": values["requests.json"],
            "replay_overrides": values["replay-overrides.json"], "replay_provenance": provenance}


def fork_replay(root: Path, output: Path, overrides: dict) -> dict:
    """Import saved A answers into a new offline bundle; legacy evidence stays intact."""
    root = _private_root(root)
    bundle = _load_bundle(root, check_runtime=False, allow_legacy=True)
    outcomes = load_outcomes(root, bundle["slots"])
    a_slots = {slot["slot_id"] for slot in bundle["slots"] if slot["stage"] == "A"}
    if (not isinstance(overrides, dict) or set(overrides) - a_slots
            or any(not isinstance(value, (dict, str)) for value in overrides.values())):
        raise ConfigurationError("Replay overrides must map known A slot IDs to JSON objects or raw strings; B overrides are not supported.")
    frozen = {}
    provenance = {}
    for slot in bundle["slots"]:
        if slot["stage"] == "A" and slot["slot_id"] in outcomes:
            response = _trial(root, slot["slot_id"]) / "response.json"
            if response.exists():
                # Only ledger-hashed responses are evidence; an orphan file is not.
                if "response.json" not in outcomes[slot["slot_id"]].get("artifacts", {}):
                    raise ConfigurationError("Saved A response has no completed evidence hash.")
                response_text = read_json(response).get("text")
                if not isinstance(response_text, str):
                    raise ConfigurationError("Saved A response must contain raw model text.")
                frozen[slot["slot_id"]] = response_text
                slot_id = slot["slot_id"]
                if slot_id in bundle["replay_provenance"]:
                    provenance[slot_id] = deepcopy(bundle["replay_provenance"][slot_id])
                else:
                    outcome = outcomes[slot_id]
                    if "request.json" not in outcome.get("artifacts", {}):
                        raise ConfigurationError("Saved A request has no completed evidence hash.")
                    provenance[slot_id] = {
                        "request.json": read_json(response.parent / "request.json"),
                        "response.json": read_json(response),
                        "origin.json": {"bundle": str(root), "slot_id": slot_id,
                                        "mode": outcome["mode"], "overridden": False,
                                        "manifest_sha256": digest(bundle["manifest"]),
                                        "outcome_sha256": outcome["sha256"]}}
                if slot_id in overrides:
                    provenance[slot_id]["origin.json"]["overridden"] = True
                actual_request = provenance[slot_id]["request.json"]
                try:
                    user_message = actual_request["messages"][1]
                    material = json.loads(user_message["content"], object_pairs_hook=_unique_object,
                                          parse_constant=_reject_constant)
                    original_case = next(case for case in bundle["cases"] if case["id"] == slot["case_id"])
                    if (user_message["role"] != "user"
                            or material["table"] != bundle["table_inputs"][slot["case_id"]]
                            or material["task"] != original_case["task"]):
                        raise ValueError()
                except (KeyError, IndexError, TypeError, ValueError):
                    raise ConfigurationError("Historical request does not match the frozen task and model-visible table input.") from None
    frozen.update(overrides)
    if a_slots - set(frozen):
        raise ConfigurationError("Missing saved A responses; provide explicit offline overrides for those A slots.")
    cases = deepcopy(bundle["cases"])
    for case in cases:
        for field in ("replays", "expected_diagnostics"):
            if field in case:
                case[field] = {"A": case[field]["A"]} if "A" in case[field] else {}
    return prepare_bundle(output, cases, bundle["config"],
                          replay_overrides=frozen, replay_only=True, rounds=bundle["manifest"]["rounds"],
                          replay_provenance=provenance, frozen_table_inputs=bundle["table_inputs"])


@contextmanager
def exclusive_run(root: Path):
    with (root / "runner.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConfigurationError("Another process owns this experiment ledger.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _trial(root: Path, slot_id: str) -> Path:
    # B identifiers are accepted only for checking historic ledgers during import.
    if not re.fullmatch(r"r[12]-[A-Za-z0-9_-]{1,64}-(A|B1|B2)", slot_id):
        raise ConfigurationError("Unsafe trial identifier.")
    target = root / "trials" / slot_id
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise ConfigurationError("Trial directory escaped the experiment bundle.")
    return target


def _finish(directory: Path, outcome: dict) -> None:
    artifacts = {p.name: digest(read_json(p)) for p in sorted(directory.glob("*.json"))
                 if p.name != "outcome.json"}
    value = {**outcome, "artifacts": artifacts}
    write_new(directory / "outcome.json", {**value, "sha256": digest(value)})


def load_outcomes(root: Path, slots: list[dict]) -> dict:
    outcomes = {}
    known = {slot["slot_id"] for slot in slots}
    trials = root / "trials"
    if trials.exists() and any(p.name not in known for p in trials.iterdir()):
        raise ConfigurationError("Unknown trial directory in the experiment ledger.")
    for slot in slots:
        directory = _trial(root, slot["slot_id"])
        reserved = directory / "reservation.json"
        completed = directory / "outcome.json"
        if completed.exists():
            outcome = read_json(completed)
            if outcome.get("sha256") != digest({k: v for k, v in outcome.items() if k != "sha256"}):
                raise ConfigurationError("Saved outcome changed.")
            if any(outcome.get(key) != value for key, value in slot.items()):
                raise ConfigurationError("Outcome refers to a different trial.")
            for name, expected in outcome.get("artifacts", {}).items():
                if (Path(name).name != name or (directory / name).is_symlink()
                        or digest(read_json(directory / name)) != expected):
                    raise ConfigurationError("Saved trial evidence changed.")
            if outcome["status"] != "skipped" and not reserved.exists():
                raise ConfigurationError("Outcome has no generation reservation.")
            outcomes[slot["slot_id"]] = outcome
        if reserved.exists():
            reservation = read_json(reserved)
            if (reserved.is_symlink() or reservation.get("slot_sha256") != digest(slot)
                    or reservation.get("mode") not in {"live", "replay"}
                    or reservation.get("request_sha256") != digest(read_json(directory / "request.json"))):
                raise ConfigurationError("Reservation refers to a different trial.")
            if slot["slot_id"] not in outcomes:
                outcomes[slot["slot_id"]] = {**slot, "status": "reserved_unknown", "mode": reservation["mode"],
                                              "reason": "Reserved before interruption; never resent."}
    return outcomes


def inspect_bundle(root: Path) -> dict:
    from attestextract.table_reporting import summarize_table_bundle
    bundle = load_bundle(root)
    outcomes = load_outcomes(root, bundle["slots"])
    return {"network_calls": 0, "planned_slots": len(bundle["slots"]), "outcomes": len(outcomes),
            "remaining_slots": len(bundle["slots"]) - len(outcomes),
            "statuses": {status: sum(o["status"] == status for o in outcomes.values())
                         for status in sorted({o["status"] for o in outcomes.values()})},
            **summarize_table_bundle(root)}


def summarize_bundle(root: Path) -> dict:
    """Inspect saved A outputs without new calls or requiring old executable code.

    Frozen inputs and recorded trial evidence must still pass their hashes. This
    does not re-evaluate them with new core logic or overwrite the old report.
    """
    from attestextract.table_reporting import summarize_table_bundle
    bundle = load_bundle(root, check_runtime=False)
    load_outcomes(root, bundle["slots"])
    return {"network_calls": 0, **summarize_table_bundle(root)}


def _safe_error(error: Exception, phase: str) -> dict:
    # Exception values and validation inputs may contain paper text or credentials.
    data = {"phase": phase, "type": type(error).__name__}
    if isinstance(error, BackendError):
        data.update(code=error.code, status_code=error.status_code, attempts=error.attempts)
    if hasattr(error, "errors"):
        data["details"] = [{"loc": list(item["loc"]), "type": item["type"]}
                           for item in error.errors(include_input=False, include_url=False)]
    if isinstance(error, json.JSONDecodeError):
        data.update(line=error.lineno, column=error.colno, position=error.pos)
    return data


def _redact_payload(value, secret: str):
    if isinstance(value, str):
        return redact_text(value, [secret])
    if isinstance(value, dict):
        return {key: _redact_payload(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, secret) for item in value]
    return value


def run_bundle(root: Path, *, mode: str = "replay", environment: dict | None = None,
               backend_factory=None) -> dict:
    from attestextract.table_routes import evaluate_route, parse_route_response_partial, reconcile_results
    if mode not in {"replay", "live"}:
        raise ConfigurationError("Experiment mode must be replay or live.")
    root = root.resolve()
    bundle = load_bundle(root)
    if mode == "live" and bundle["manifest"].get("replay_only"):
        raise ConfigurationError("A replay fork is offline-only and cannot make model calls.")
    cases = {case["id"]: case for case in bundle["cases"]}
    config = BackendConfig.model_validate(bundle["config"])
    api_key = (environment or {}).get(config.api_key_env)
    if mode == "live" and not api_key and backend_factory is None:
        raise ConfigurationError("The frozen credential variable is missing; no request was sent.")
    with exclusive_run(root):
        outcomes = load_outcomes(root, bundle["slots"])
        if any(outcome["mode"] != mode for outcome in outcomes.values()):
            raise ConfigurationError("Replay and live results cannot share a bundle; prepare a new directory.")
        stop_reason = next((o["stop_reason"] for o in outcomes.values() if o.get("stop_reason")), None)
        transport_failures = 0
        for prior in bundle["slots"]:
            saved = outcomes.get(prior["slot_id"])
            if not saved or saved["status"] == "skipped":
                continue
            error_path = _trial(root, prior["slot_id"]) / "error.json"
            error = read_json(error_path) if error_path.exists() else {}
            if saved["status"] in {"backend_error", "reserved_unknown"} and error.get("status_code") is None:
                transport_failures += 1
            else:
                transport_failures = 0
        if transport_failures >= 3:
            stop_reason = "Three consecutive unknown transport outcomes; provider stopped."
        invocations = 0
        for slot in bundle["slots"]:
            slot_id = slot["slot_id"]
            if slot_id in outcomes:
                continue
            directory = _trial(root, slot_id)
            case = cases[slot["case_id"]]
            table_input = bundle["table_inputs"][case["id"]]
            stage = slot["stage"]
            if stop_reason:
                _finish(directory, {**slot, "status": "skipped", "mode": mode,
                                    "reason": stop_reason, "usage": None})
                outcomes[slot_id] = read_json(directory / "outcome.json")
                continue
            request = ExtractionRequest.model_validate(bundle["requests"][slot_id])
            if request.generation.max_output_tokens != 4096 or request.generation.temperature != 0.0:
                raise ConfigurationError("Frozen request generation bounds changed.")
            write_new(directory / "request.json", request.model_dump(mode="json"))
            write_new(directory / "reservation.json", {"slot_sha256": digest(slot), "mode": mode,
                                                       "request_sha256": digest(request.model_dump(mode="json")),
                                                       "reserved_at": datetime.now(timezone.utc).isoformat()})
            started = time.monotonic()
            phase = "generation"
            response = None
            trace = None
            status = "success"
            error_code = None
            try:
                if mode == "replay":
                    replay = bundle["replay_overrides"].get(slot_id, case.get("replays", {}).get(stage))
                    if replay is None:
                        raise ValueError("Missing frozen replay response")
                    text = replay if isinstance(replay, str) else json.dumps(replay, ensure_ascii=False)
                    original = bundle["replay_provenance"].get(slot_id, {})
                    finish_reason = None
                    if original and not original["origin.json"].get("overridden"):
                        finish_reason = original["response.json"].get("finish_reason")
                    response = BackendResponse(text=text, model="offline-replay", finish_reason=finish_reason)
                else:
                    trace = RunTrace(directory / "transport", secrets=[api_key] if api_key else [])
                    backend = (backend_factory(config=config, api_key=api_key, trace=trace) if backend_factory
                               else default_registry().create(config.adapter, config=config, api_key=api_key, trace=trace))
                    invocations += 1
                    response = backend.generate(request)
                transport_failures = 0
                payload = response.model_dump(mode="json")
                # Preserve model text, except any credential echoed by an endpoint.
                if api_key:
                    payload = _redact_payload(payload, api_key)
                write_new(directory / "response.json", payload)
                phase = "parse"
                if response.finish_reason in {"length", "max_tokens", "max_output_tokens", "incomplete"}:
                    error_code = "output_truncated"
                    raise ValueError("Truncated response")
                parsed = parse_route_response_partial(stage, payload["text"])
                write_new(directory / "parsed.json", parsed)
                write_new(directory / "parse-rejections.json", parsed["rejections"])
                if parsed["rejections"]:
                    status = "partial_format_error" if parsed["records"] else "format_error"
                phase = "evaluate"
                evaluation = evaluate_route(stage, parsed, table_input, case["task"])
                write_new(directory / "evaluation.json", evaluation)
                phase = "reconcile"
                reconciliation = reconcile_results(case.get("previous", []), evaluation, case["task"],
                                                   table_input=table_input)
                write_new(directory / "reconciliation.json", reconciliation)
                write_new(directory / "results.json", {
                    "schema_version": "table-route-v3", "processing_policy": "observe_only",
                    "candidates": reconciliation["candidates"],
                    "evidence": reconciliation["evidence"], "rejections": parsed["rejections"],
                    "semantic_verification": "not_run"})
                transport_failures = 0
            except Exception as error:
                status = {"generation": "backend_error", "parse": "format_error"}.get(phase, "evaluation_error")
                error_data = _safe_error(error, phase)
                if error_code:
                    error_data["code"] = error_code
                write_new(directory / "error.json", error_data)
                if mode == "live" and isinstance(error, BackendError):
                    if error.code in {"authentication_error", "billing_error", "permission_denied", "not_found",
                                      "invalid_request", "rate_limited", "certificate_error"}:
                        stop_reason = "Provider stopped after " + error.code + "; no extra requests or retries."
                    transport_failures = transport_failures + 1 if error.status_code is None else 0
                    if transport_failures >= 3:
                        stop_reason = "Three consecutive unknown transport outcomes; provider stopped."
            outcome = {**slot, "status": status, "mode": mode,
                       "elapsed_seconds": round(time.monotonic() - started, 6),
                       "usage": response.usage if response else None,
                       "model": (redact_text(response.model, [api_key]) if api_key and response and response.model
                                 else response.model if response else None),
                       "stop_reason": stop_reason,
                       "notice": "Stage completion is not semantic correctness."}
            if trace is not None:
                trace.checked({"generation_returned": response is not None, "stage_status": status,
                               "semantic_acceptance": "not_expert_reviewed"})
            _finish(directory, outcome)
            outcomes[slot_id] = outcome
    summary = inspect_bundle(root)
    summary.update(mode=mode, live_backend_invocations_this_run=invocations)
    if mode == "live":
        summary.pop("network_calls", None)
        summary["notice"] = ("Execution completion and content quality are reported separately. "
                             "Inspect execution.saved_responses and quality_summary; success is not correctness. "
                             "Do not resend paid requests just to inspect their results.")
    return summary


def write_report(root: Path) -> Path:
    from attestextract.table_reporting import render_table_report, summarize_table_bundle
    bundle = load_bundle(root, check_runtime=False)
    load_outcomes(root, bundle["slots"])
    target = root.resolve() / "REPORT.html"
    target.write_text(render_table_report(root.resolve()), encoding="utf-8")
    (root.resolve() / "SUMMARY.json").write_text(
        json.dumps(summarize_table_bundle(root), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    return target

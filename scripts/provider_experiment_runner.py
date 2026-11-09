"""Serial, at-most-once execution of a frozen provider experiment.

This is an experiment scheduler, not a retry policy for ordinary extraction.
Only explicitly numbered, unreserved trials may be sent. Private artifacts stay
inside the prepared bundle; an interrupted reservation is never sent again.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable

from attestextract import Document, extract
from attestextract.config import BackendConfig
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError
from attestextract.models import ContextOptions, ExecutionOptions, ExtractionRequest, GenerationOptions
from attestextract.registry import default_registry
from attestextract.reporting import render_markdown_report

from provider_experiment_bundle import _read, load_bundle, runtime_identity
from provider_experiment_reporting import (score_response, select_connections,
                                         select_protocols, summarize, render_report)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def save_record(path: Path, value: dict) -> None:
    write_new(path, {"result": value, "sha256": digest(value)})


def read_record(path: Path) -> dict:
    try:
        value = _read(path)
        if (isinstance(value, dict) and set(value) == {"result", "sha256"}
                and isinstance(value["result"], dict)
                and digest(value["result"]) == value["sha256"]):
            return value["result"]
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    raise ConfigurationError("Experiment ledger integrity check failed.")


@contextmanager
def exclusive_run(root: Path):
    """OS-owned lock is released even when the experiment process crashes."""
    root.mkdir(parents=True, exist_ok=True)
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


def trial_root(root: Path, row: dict) -> Path:
    identifier = row["id"]
    if not isinstance(identifier, str) or not identifier or Path(identifier).name != identifier:
        raise ConfigurationError("Invalid experiment trial identifier.")
    target = (root / "trials" / identifier).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ConfigurationError("Trial path escaped the experiment directory.")
    return target


def load_results(root: Path, rows: list[dict]) -> dict[str, dict]:
    results = {}
    known = {row["id"] for row in rows}
    trials = root / "trials"
    if trials.exists() and any(p.name not in known or p.is_symlink() for p in trials.iterdir()):
        raise ConfigurationError("Experiment contains an unknown trial directory.")
    for row in rows:
        directory = trial_root(root, row)
        reserved = directory / "reserved.json"
        finished = directory / "result.json"
        if finished.exists():
            result = read_record(finished)
            if result.get("id") != row["id"] or result.get("row_sha256") != digest(row):
                raise ConfigurationError("Experiment result refers to a different frozen trial.")
            if result["status"] != "not_run" and not reserved.exists():
                raise ConfigurationError("Experiment result has no prior call reservation.")
            results[row["id"]] = result
        if reserved.exists():
            reservation = read_record(reserved)
            if reservation.get("id") != row["id"] or reservation.get("row_sha256") != digest(row):
                raise ConfigurationError("Experiment reservation does not match its frozen trial.")
            if row["id"] not in results:
                results[row["id"]] = {
                    **reservation, "status": "reserved_unknown", "sent": None,
                    "http_complete": False, "usage": None, "scores": None,
                    "transport_unknown": True, "billing_unknown": True,
                    "reason": "Interrupted reservation; never automatically resent.",
                }
    return results


def _review_attachment(root: Path, bundle: dict, results: dict) -> dict | None:
    """Revalidate the immutable review and its original evidence before use."""
    path = root / "semantic-review.json"
    if not path.exists():
        bundle.pop("reviewed_protocol_selection", None)
        bundle.pop("semantic_review", None)
        return None
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("Semantic review attachment escaped the experiment directory.")
    record = read_record(path)
    if (record.get("attachment_version") != "0.1"
            or record.get("bundle_id") != bundle["manifest"]["bundle_id"]
            or record.get("runtime") != runtime_identity()
            or record.get("review_sha256") != digest(record.get("review"))):
        raise ConfigurationError("Semantic review attachment identity changed; no request was sent.")
    from provider_experiment_review import validate_review
    verified = validate_review(record["review"], bundle, root, results)
    if record.get("verified") != verified:
        raise ConfigurationError("Semantic review selection no longer matches its evidence.")
    bundle["reviewed_protocol_selection"] = verified["protocol_selection"]
    bundle["semantic_review"] = {"review_sha256": record["review_sha256"],
                               "artifact": "semantic-review.json", "origin": verified["review_origin"]}
    return record


def _inspection(bundle: dict, results: dict) -> dict:
    review_selection = bundle.get("reviewed_protocol_selection")
    strict = select_protocols(bundle["rows"], results)
    reserved = sum(item.get("status") != "not_run" for item in results.values())
    d_pending = sum(row["stage"] == "D" and row["id"] not in results for row in bundle["rows"])
    return {"checked": True, "network_calls": 0, "planned_calls": len(bundle["rows"]),
            "existing_outcomes": len(results), "reserved_calls": reserved,
            "remaining_call_budget": bundle["manifest"]["max_model_calls"] - reserved,
            "pending_D_trials": d_pending,
            "network_identity_matches": bundle.get("network_identity_matches", True),
            "runtime_compatibility": bundle.get("runtime_compatibility"),
            "connection_selection": select_connections(bundle["rows"], results),
            "strict_protocol_selection": strict,
            "protocol_selection": review_selection if review_selection is not None else strict,
            "selection_basis": "semantic_review" if review_selection is not None else "strict_scores"}


def inspect_experiment(bundle_path: Path) -> dict:
    """Offline inspection does not require the caller's network settings to match."""
    bundle = load_bundle(bundle_path, check_network=False)
    root = bundle_path.resolve() / "results"
    results = load_results(root, bundle["rows"])
    _review_attachment(root, bundle, results)
    return _inspection(bundle, results)


def attach_review(bundle_path: Path, review_path: Path) -> dict:
    """Attach a complete review without changing scores, reservations or requests."""
    from provider_experiment_review import validate_review
    bundle = load_bundle(bundle_path, check_network=False)
    root = bundle_path.resolve() / "results"
    review = _read(review_path)
    with exclusive_run(root):
        results = load_results(root, bundle["rows"])
        if any(row["id"] not in results for row in bundle["rows"] if row["stage"] != "D"):
            raise ConfigurationError("Finish A, B and C before attaching a semantic review.")
        existing = _review_attachment(root, bundle, results)
        if existing is not None:
            if existing["review_sha256"] != digest(review):
                raise ConfigurationError("A different semantic review is already attached; it cannot be replaced.")
            return _inspection(bundle, results)
        if any(row["id"] in results for row in bundle["rows"] if row["stage"] == "D"):
            raise ConfigurationError("D already has recorded outcomes; selection cannot be changed.")
        verified = validate_review(review, bundle, root, results)
        record = {"attachment_version": "0.1", "bundle_id": bundle["manifest"]["bundle_id"],
                  "runtime": runtime_identity(), "review_sha256": digest(review), "review": review,
                  "verified": verified, "reserved_calls_at_attachment":
                  sum(item.get("status") != "not_run" for item in results.values())}
        save_record(root / "semantic-review.json", record)
        _review_attachment(root, bundle, results)
        write_report(root, bundle)
        return _inspection(bundle, results)


def certificate_error(error: BackendError) -> bool:
    causes = (error.http_diagnostics or {}).get("causes", [])
    return any(item.get("type") == "SSLCertVerificationError" or
               item.get("reason") == "CERTIFICATE_VERIFY_FAILED" for item in causes)


def stop_reason(provider: str, results: dict, now: float) -> str | None:
    consecutive = 0
    ordered = sorted((item for item in results.values() if item.get("provider") == provider),
                     key=lambda item: item.get("started_epoch", 0))
    for item in ordered:
        if item.get("provider_stop"):
            return item["provider_stop"]
        if item.get("window_paused_until", 0) > now:
            return "rate_limit_window_paused"
        if item.get("sent") is False:
            continue
        if item.get("transport_unknown"):
            consecutive += 1
            if consecutive >= 3:
                return "three_consecutive_unknown_transport_outcomes"
        elif item.get("sent"):
            consecutive = 0
    return None


def _selected(selection: dict, provider: str):
    item = selection.get(provider)
    return item.get("selected") if isinstance(item, dict) else item


def select_config(row: dict, bundle: dict, results: dict) -> str | None:
    if row.get("config_key"):
        return row["config_key"]
    mode = _selected(select_connections(bundle["rows"], results), row["provider"])
    if mode is None:
        return None
    path = row["path_id"]
    if row["stage"] == "D":
        selection = bundle.get("reviewed_protocol_selection")
        path = _selected(selection if selection is not None else select_protocols(bundle["rows"], results), row["provider"])
        if path is None:
            return None
    key = f"{path}--tls-{mode}"
    if key not in row["config_alternatives"]:
        raise ConfigurationError("Selected configuration is not a frozen alternative.")
    return key


class ExperimentRunner:
    def __init__(self, bundle: dict, root: Path, environment: dict, *,
                 backend_factory: Callable | None = None, clock=time.time,
                 sleep=time.sleep, progress=print):
        self.bundle, self.root, self.environment = bundle, root, environment
        self.factory = backend_factory or default_registry().create
        self.clock, self.sleep, self.progress = clock, sleep, progress
        self.results = load_results(root, bundle["rows"])
        self.backends = {}

    def check_credentials(self):
        """Validate all credentials locally before reserving any new call."""
        for raw in self.bundle["configs"].values():
            config = BackendConfig.model_validate(raw)
            key = self.environment.get(config.api_key_env)
            if not isinstance(key, str) or not key.strip() or not key.isascii() or "\n" in key or "\r" in key:
                raise ConfigurationError("A selected provider credential is missing or invalid; no requests sent.")

    def skip(self, row: dict, reason: str):
        if row["id"] in self.results:
            return
        result = {"id": row["id"], "provider": row["provider"], "stage": row["stage"],
                  "status": "not_run", "row_sha256": digest(row), "reason": reason,
                  "sent": False, "http_complete": False, "usage": None, "scores": None}
        save_record(trial_root(self.root, row) / "result.json", result)
        self.results[row["id"]] = result

    def unavailable(self, row: dict) -> str | None:
        reason = stop_reason(row["provider"], self.results, self.clock())
        if reason:
            return reason
        for item in self.results.values():
            if item.get("path_stop") == row["path_id"]:
                return "request_configuration_rejected"
        if select_config(row, self.bundle, self.results) is None:
            return "no_eligible_configuration"
        return None

    def backend(self, row: dict, trace=None):
        key = select_config(row, self.bundle, self.results)
        if key is None:
            raise ConfigurationError("No eligible frozen configuration.")
        group = (row["group_id"], key)
        if group not in self.backends:
            config = BackendConfig.model_validate(self.bundle["configs"][key])
            self.backends[group] = self.factory(config.adapter, config=config,
                                                api_key=self.environment[config.api_key_env], trace=trace)
        return self.backends[group], key

    def request(self, row: dict, request: ExtractionRequest, backend=None):
        if request.model_dump(mode="json") != row["request"]:
            raise ConfigurationError("Generated request differs from the frozen request; nothing sent.")
        if row["id"] in self.results:
            raise ConfigurationError("An already reserved trial cannot be sent again.")
        # Count reservations, including outcomes unknown after a process crash.
        if sum(value["status"] != "not_run" for value in self.results.values()) >= 108:
            raise ConfigurationError("The frozen experiment call limit has been exhausted.")
        previous = max((r.get("finished_epoch", r.get("started_epoch", 0))
                        for r in self.results.values() if r["status"] != "not_run"), default=0)
        wait = 2 - (self.clock() - previous)
        if wait > 0:
            self.sleep(wait)
        key = select_config(row, self.bundle, self.results)
        started = self.clock()
        base = {"id": row["id"], "row_sha256": digest(row), "stage": row["stage"],
                "provider": row["provider"], "path_id": row["path_id"], "config_key": key,
                "started_epoch": started, "reserved_calls": 1}
        if row["stage"] == "D" and self.bundle.get("semantic_review"):
            base["semantic_review"] = self.bundle["semantic_review"]
        directory = trial_root(self.root, row)
        save_record(directory / "reserved.json", base)
        self.results[row["id"]] = {**base, "status": "reserved_unknown", "sent": None,
                                    "transport_unknown": True}
        events = []
        secrets = [self.environment.get(c["api_key_env"], "") for c in self.bundle["configs"].values()]
        trace = RunTrace(directory / "diagnostics", secrets=secrets, on_event=events.append)
        backend = backend or self.backend(row, trace)[0]
        backend._trace = trace
        result = {**base, "status": "failed", "sent": False, "http_complete": False,
                  "scores": None, "usage": None, "transport_unknown": False}
        response = None
        failure = None
        request_outcome = None
        self.progress(f"[{row['id']}] {row['stage']} {row['provider']} {key}; one planned request")
        try:
            response = backend.generate(request)
            trace.write_json("unified-response.json", response.model_dump(mode="json"))
            result["usage"] = response.usage or None
            result["scores"] = score_response(response.text, request, self.bundle["cases"][row["case_id"]])
            result["status"] = "completed" if result["scores"]["format_passed"] else "failed"
            trace.finish({"run": {"experiment_trial": row["id"]}})
        except BackendError as error:
            failure = error
            trace.fail(error)
            result.update(error_code=error.code, status_code=error.status_code)
            diagnostic = error.http_diagnostics or {}
            request_outcome = diagnostic.get("request_outcome")
            result["transport_unknown"] = bool(error.attempts and diagnostic.get("request_outcome") == "unknown")
            metadata = getattr(error, "response_metadata", None) or {}
            result["usage"] = metadata.get("usage") or None
            if certificate_error(error):
                result["provider_stop"] = "certificate_verification_failed"
            elif error.code in {"authentication_error", "billing_error", "permission_denied", "not_found"}:
                result["provider_stop"] = error.code
            elif error.status_code == 429:
                result["window_paused_until"] = self.clock() + 1800
            elif error.code == "invalid_request":
                result["path_stop"] = row["path_id"]
        except BaseException as error:
            trace.fail(error)
            # Keep the durable reservation unknown. Do not invent an outcome.
            raise
        observed = [e for e in events if e.get("event") == "connection_observed"
                    and e.get("request_kind") == "model_post"]
        http_started = [e for e in events if e.get("stage") == "http" and e.get("event") == "started"]
        completed = [e for e in events if e.get("stage") == "http" and e.get("event") == "completed"]
        result["model_post_attempts"] = len(http_started)
        result["probe_requests"] = sum(e.get("request_kind") == "tls_probe" for e in events
                                       if e.get("event") == "connection_observed")
        result["connection_observation"] = observed[-1] if observed else None
        if completed:
            result["status_code"] = completed[-1].get("status_code")
        result["http_complete"] = bool(completed)
        result["request_outcome"] = ("response_received" if completed else "not_sent"
                                     if request_outcome == "not_sent" or not http_started else "unknown")
        result["sent"] = {"response_received": True, "not_sent": False, "unknown": None}[result["request_outcome"]]
        result["billing_unknown"] = result["request_outcome"] == "unknown" or bool(completed and not result["usage"])
        result["finished_epoch"] = self.clock()
        result["elapsed_seconds"] = result["finished_epoch"] - started
        save_record(directory / "result.json", trace._mask(result))
        self.results[row["id"]] = result
        self.progress(f"[{row['id']}] {result['status']}; HTTP complete={result['http_complete']}; "
                      f"elapsed={result['elapsed_seconds']:.1f}s")
        if failure is not None:
            raise failure
        return response

    def run_trials(self, rows: list[dict]):
        for row in rows:
            if row["id"] in self.results:
                continue
            reason = self.unavailable(row)
            interrupted_group = any(item.get("status") == "reserved_unknown"
                                    and other["group_id"] == row["group_id"]
                                    for other in self.bundle["rows"]
                                    if (item := self.results.get(other["id"])) is not None)
            if reason or interrupted_group:
                self.skip(row, reason or "interrupted_backend_group")
                continue
            try:
                self.request(row, ExtractionRequest.model_validate(row["request"]))
            except BackendError:
                pass  # This numbered trial failed; never retry it or replace it.

    def run_paper(self, rows: list[dict]):
        if not rows:
            return
        group = rows[0]["group_id"]
        group_path = self.root / "papers" / group
        if any(row["id"] in self.results for row in rows):
            for row in rows:
                self.skip(row, "whole_paper_run_already_started")
            return
        reason = self.unavailable(rows[0])
        if reason:
            for row in rows:
                self.skip(row, reason)
            return
        group_path.mkdir(parents=True, exist_ok=False)
        backend, key = self.backend(rows[0])
        runner = self
        by_chunk = {row["chunk_id"]: row for row in rows}

        class FrozenBackend:
            def __getattr__(self, name):
                return getattr(backend, name)

            def generate(self, request):
                if request.chunk_id not in by_chunk:
                    raise ConfigurationError("Extraction introduced an unplanned chunk.")
                return runner.request(by_chunk[request.chunk_id], request, backend)

        secrets = [self.environment.get(c["api_key_env"], "") for c in self.bundle["configs"].values()]
        trace = RunTrace(group_path / "diagnostics", secrets=secrets)
        args = self.bundle["extraction"]
        paper = self.bundle["cases"]["paper"]
        document = Document.from_text(paper["source"], source_name=args["source_name"],
                                      representation=args["representation"])
        state = {"stage": rows[0]["stage"], "provider": rows[0]["provider"],
                 "config_key": key, "fresh": True, "reused_chunks": 0,
                 "expected_chunks": len(rows), "status": "failed"}
        try:
            result = extract(document, paper["task"], backend=FrozenBackend(), trace=trace,
                             generation=GenerationOptions.model_validate(args["generation"]),
                             execution=ExecutionOptions.model_validate(args["options"]),
                             context=ContextOptions.model_validate(args["context"]),
                             max_chunk_chars=args["max_chunk_chars"],
                             table_rows_per_chunk=args["table_rows_per_chunk"],
                             format_retries=0, backend_identity=key)
            if trace._mask(result.model_dump(mode="json")) != result.model_dump(mode="json"):
                raise ConfigurationError("Result contains a configured credential; no successful result was saved.")
            trace.finish(result.model_dump(mode="json"))
            write_new(group_path / "result.json", result.model_dump(mode="json"))
            (group_path / "report.md").write_text(render_markdown_report(result, secrets=secrets), encoding="utf-8")
            state.update(status="completed", completed_chunks=result.run.chunk_count)
        except BackendError as error:
            state.update(error_code=error.code)
        finally:
            for row in rows:
                self.skip(row, "whole_paper_stopped_after_first_failure")
            state["completed_chunks"] = sum(self.results[r["id"]]["status"] == "completed" for r in rows)
            save_record(group_path / "outcome.json", state)


def write_report(root: Path, bundle: dict) -> dict:
    results = load_results(root, bundle["rows"])
    _review_attachment(root, bundle, results)
    summary = summarize(bundle["rows"], results)
    if bundle.get("reviewed_protocol_selection") is not None:
        summary["strict_protocol_selection"] = summary["protocol_selection"]
        summary["protocol_selection"] = bundle["reviewed_protocol_selection"]
        summary["protocol_selection_basis"] = "semantic_review"
        summary["semantic_review"] = bundle["semantic_review"]
        summary["manual_semantic_review"] = "stage_C_AI_review_attached; full_paper_review_pending"
    for full in summary["full_runs"]:
        path = root / "papers" / f"{full['stage']}-{full['provider']}" / "outcome.json"
        outcome = read_record(path) if path.exists() else {}
        full["passed"] = bool(full["trials_passed"] and outcome.get("status") == "completed"
                              and outcome.get("fresh") and outcome.get("reused_chunks") == 0)
        full["pipeline_status"] = outcome.get("status", "not_completed")
    # Reports are derived and replaceable; call reservations and results are not.
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rendered = render_report(summary)
    if summary.get("protocol_selection_basis") == "semantic_review":
        note = ["## Semantic review selection", "",
                "Candidate paths use the verified AI review attachment. Original strict scores are unchanged; this is not expert gold.",
                f"Review digest: `{summary['semantic_review']['review_sha256']}`; see `semantic-review.json`.", ""]
        for provider, strict in summary["strict_protocol_selection"].items():
            note.append(f"- {provider} original strict selection: `{strict['selected']}` ({strict['reason']}).")
        rendered = "\n".join(note) + "\n\n" + rendered
    (root / "report.md").write_text(rendered, encoding="utf-8")
    return summary


def run_stage(bundle_path: Path, environment: dict, stage: str, round_number: int | None = None,
              *, backend_factory=None, clock=time.time, sleep=time.sleep, progress=print) -> dict:
    bundle = load_bundle(bundle_path, environment)
    root = bundle_path.resolve() / "results"
    with exclusive_run(root):
        results = load_results(root, bundle["rows"])
        attachment = _review_attachment(root, bundle, results)
        compatibility = bundle.get("runtime_compatibility") or {}
        if compatibility.get("legacy") and (stage != "D" or attachment is None):
            raise ConfigurationError("Legacy experiments can continue only D with a verified semantic review attachment.")
        if stage == "D":
            selection = bundle.get("reviewed_protocol_selection")
            selection = selection if selection is not None else select_protocols(bundle["rows"], results)
            if not any(item.get("selected") for item in selection.values()):
                raise ConfigurationError("No qualified protocol for D. Attach a verified review before running; no D trials were reserved.")
        runner = ExperimentRunner(bundle, root, environment, backend_factory=backend_factory,
                                  clock=clock, sleep=sleep, progress=progress)
        if stage not in {"A", "B", "C", "D"} or (stage == "B") != (round_number is not None):
            raise ConfigurationError("Select stage A/C/D, or stage B with round 1, 2 or 3.")
        if round_number is not None and round_number not in {1, 2, 3}:
            raise ConfigurationError("B round must be 1, 2 or 3.")
        previous_stages = {"A": [], "B": ["A"], "C": ["A", "B"], "D": ["A", "B", "C"]}[stage]
        required = [r for r in bundle["rows"] if r["stage"] in previous_stages or
                    (stage == "B" and r["stage"] == "B" and r["round"] < round_number)]
        if any(row["id"] not in runner.results for row in required):
            raise ConfigurationError("Finish or explicitly stop the preceding experiment stage first.")
        if stage == "B" and round_number == 3:
            starts = [runner.results[r["id"]].get("started_epoch") for r in bundle["rows"]
                      if r["stage"] == "B" and r["round"] == 1 and r["id"] in runner.results]
            starts = [value for value in starts if value is not None]
            if starts and clock() - min(starts) < 1800:
                raise ConfigurationError(f"B round 3 is not due; wait {int(1800 - (clock() - min(starts))) + 1} seconds. No requests sent.")
        rows = [r for r in bundle["rows"] if r["stage"] == stage and
                (stage != "B" or r["round"] == round_number)]
        runner.check_credentials()
        try:
            if stage in {"A", "D"}:
                for provider in ("qwen", "deepseek"):
                    runner.run_paper([r for r in rows if r["provider"] == provider])
            else:
                runner.run_trials(rows)
        finally:
            summary = write_report(root, bundle)
    return summary

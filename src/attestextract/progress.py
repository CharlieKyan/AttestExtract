"""Small console summaries; raw model text and field-level events stay in logs."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, TextIO


class ConsoleProgress:
    """Render safe structured events, with a bounded waiting heartbeat on stderr."""

    def __init__(self, mode: str = "normal", *, stream: TextIO | None = None,
                 heartbeat_seconds: float = 15) -> None:
        self.mode = mode
        self.stream = stream if stream is not None else sys.stderr
        self.heartbeat_seconds = heartbeat_seconds
        self.total = 0
        self.current = 0
        self._last_chunk = None
        self._managed_execution = False
        self._calls_reserved = 0
        self._call_limit = 0
        self._read_timeout: float | None = None
        self._waiting_since: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self, text: str) -> None:
        # Do not allow model IDs or paths containing terminal control characters.
        text = "".join(ch if ch.isprintable() else " " for ch in text)
        print(text, file=self.stream, flush=True)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            with self._lock:
                if self._waiting_since is not None:
                    elapsed = time.monotonic() - self._waiting_since
                    limit = f"; read timeout {self._read_timeout:g}s" if self._read_timeout is not None else ""
                    self._write(f"[{self.current}/{self.total}] waiting for model; {elapsed:.0f}s elapsed{limit}")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def __call__(self, event: dict[str, Any]) -> None:
        if self.mode == "quiet":
            return
        with self._lock:
            self._render(event)

    def _render(self, event: dict[str, Any]) -> None:
        stage, kind = event["stage"], event["event"]
        prefix = f"[{self.current}/{self.total}]"
        if stage == "configuration" and kind == "completed":
            self._read_timeout = event.get("read_timeout")
            self._write(f"Model: {event.get('model', '?')} | output: {event.get('structured_output', '?')} | "
                        f"protocol: {event.get('output_protocol', 'flat-v1')} | "
                        f"token limit: {event.get('max_output_tokens') or 'provider default'} | "
                        f"HTTP attempts: {event.get('max_attempts', '?')} | format retries: {event.get('format_retries', '?')}")
            if self._read_timeout is not None:
                policy = f"TLS policy {event.get('tls_mode', '?')}; " if self.mode == "verbose" else ""
                self._write(f"Connection: {policy}read timeout {self._read_timeout:g}s; stop on read timeout")
        elif stage == "configuration" and kind == "task_ready":
            self._write(f"Task: {event['record_type_count']} targets; {event['example_count']} examples; "
                        "target list retained in full")
        elif stage == "transport" and kind == "tls_selected":
            suffix = "; compatibility fallback selected automatically" if event.get("fallback") else ""
            self._write(f"Connection ready{suffix}; {event.get('probe_requests', 0)} probe request(s), no generation")
            if self.mode == "verbose":
                effective = event.get("effective_tls_mode") or "managed by custom transport"
                reason = event.get("fallback_reason")
                detail = f"; reason {reason}" if reason else ""
                self._write(f"Selected TLS policy: {effective}{detail}; actual version is recorded with each connection")
        elif stage == "http" and kind == "connection_observed":
            if self.mode == "verbose":
                self._write(f"Connection observation: TLS {event.get('actual_tls_version', 'unknown')}; "
                            f"HTTP version {event.get('actual_http_version', 'unknown')}; "
                            f"{event.get('request_kind', 'request')}")
        elif stage == "http" and kind == "started":
            self._read_timeout = event.get("read_timeout", self._read_timeout)
            self._waiting_since = time.monotonic()
        elif stage == "http" and kind == "transport_failed":
            self._waiting_since = None
            if event.get("request_outcome") == "unknown":
                self._write(f"{prefix} {event.get('phase') or 'transport'} failure: server processing is unknown; not automatically retried; saved progress can be resumed")
        elif stage == "document" and kind == "completed":
            self._write(f"Input: {event['character_count']} characters")
        elif stage == "chunking" and kind == "completed":
            self.total = event["chunk_count"]
            self._write(f"Chunks: {self.total}")
            if event.get("oversized_chunks"):
                self._write(f"{event['oversized_chunks']} chunk(s) exceed the character target to preserve indivisible source units")
        elif stage == "execution" and kind == "started":
            self._managed_execution = True
            self._calls_reserved = event["calls_reserved"]
            self._call_limit = event["max_model_calls"]
            self._write(f"Model-call budget: {event['calls_reserved']}/{event['max_model_calls']} reserved; "
                        f"{event['completed_chunks']} completed chunks available for reuse; "
                        f"{event.get('leaf_chunks', self.total) - event['completed_chunks']} remaining; "
                        f"{event['max_model_calls'] - event['calls_reserved']} calls left")
        elif stage == "execution" and kind == "call_reserved":
            self._calls_reserved, self._call_limit = event["used"], event["limit"]
        elif stage == "execution" and kind == "route_switched":
            self._waiting_since = None
            self._write(f"{prefix} compatibility fallback: {event.get('model', event['to_route'])}; "
                        f"{event.get('adapter', '?')} / {event.get('structured_output', '?')}; "
                        f"reason: {event.get('reason', '?')}; "
                        f"{event['max_model_calls'] - event['calls_reserved']} calls left")
        elif stage == "execution" and kind == "format_retry":
            self._waiting_since = None
            self._write(f"{prefix} format regeneration requested; one additional reserved call")
        elif stage == "execution" and kind in {"finished", "stopped"}:
            self._waiting_since = None
            self._write(f"Chunk summary: {event['completed_chunks']}/{event['leaf_chunks']} responses processed; "
                        f"{event['first_pass_chunks']} first call; {event['format_recovered_chunks']} format recovered; "
                        f"{event['split_chunks']} subdivided; {event['reused_chunks']} reused; "
                        f"{event['failed_chunks']} failed; {event['pending_chunks']} pending; "
                        f"calls {event['calls_reserved']}/{event['max_model_calls']}; processing does not imply every record passed")
        elif stage == "execution" and kind == "split":
            self._waiting_since = None
            # A saved truncated parent can split on resume before a request
            # starts in this run; do not subtract a previously reused chunk.
            if self._last_chunk == event["chunk_id"]:
                self.current = max(0, self.current - 1)
            self.total = event["leaf_chunks"]
            self._write(f"Truncated {event['chunk_id']}; split into {len(event['children'])} children; {self.total} leaf chunks")
        elif stage == "execution" and kind == "reused":
            self.current += 1
            self._last_chunk = event["chunk_id"]
            self._write(f"[{self.current}/{self.total}] reused completed response; validating evidence again")
        elif stage == "execution" and kind in {"unsplittable", "budget_exhausted", "format_retry_exhausted"}:
            self._waiting_since = None
            self._write(f"Execution stopped: {kind}; retain this run and its checkpoint")
        elif stage == "model_request" and kind == "started":
            if self._last_chunk != event["chunk_id"]:
                self.current += 1
                self._last_chunk = event["chunk_id"]
            self._waiting_since = time.monotonic()
            if self._thread is None:
                self._thread = threading.Thread(target=self._heartbeat, daemon=True)
                self._thread.start()
            budget = f"; calls {self._calls_reserved}/{self._call_limit}" if self._managed_execution else ""
            self._write(f"[{self.current}/{self.total}] requesting model; format attempt {event['format_attempt']}{budget}")
        elif stage == "model_response" and kind == "completed":
            self._waiting_since = None
            usage = event.get("usage", {})
            elapsed = event.get("elapsed_seconds")
            timing = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else "time unavailable"
            self._write(f"{prefix} response {timing}; tokens in/out: "
                        f"{usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
        elif stage == "validation" and kind == "rejected":
            position = f" original response line {event['line']}, column {event['column']}" if event.get("line") else ""
            self._write(f"{prefix} rejected: {event['phase']}{position}; {event['error_count']} error(s)")
            structure = event.get("structure")
            if structure and structure.get("code") == "unexpected_object_in_object":
                self._write(f"{prefix} structure: {structure['path']}; expected a named property, found anonymous '{{'")
        elif stage == "format_retry" and kind in {"retry", "resumed"}:
            self._write(f"{prefix} format_retry: {event['chunk_id']} regeneration 1/1 "
                        f"{'resumed from checkpoint' if kind == 'resumed' else 'requested'}; additional model call")
        elif stage == "format_retry" and kind == "succeeded":
            self._write(f"{prefix} {event['chunk_id']}: regenerated format passed; checking source evidence")
        elif kind == "retry" and stage in {"http", "format_retry"}:
            self._write(f"{prefix} {stage}: retry requested; an additional model call may be charged")
        elif stage == "chunk" and kind == "completed":
            self._write(f"{prefix} checked: {event['candidate_count']} candidates; "
                        f"{event['review_count']} review items; {event.get('info_count', 0)} info items")
        elif stage == "table" and kind == "completed":
            self._write(f"{prefix} table checked: {event['candidate_count']} retained; "
                        f"{event.get('rejected_count', 0)} quarantined; status {event['status']}")
        elif stage == "complete" and kind in {"partial_format_error", "format_error"}:
            self._waiting_since = None
            self._write(f"Partial result: {event.get('prose_candidates', 0)} prose candidates retained; "
                        f"{event.get('table_candidates', 0)} table records retained; "
                        f"{event.get('quarantined_records', 0)} table records quarantined. "
                        "Inspect the report and table_results.rejections; ordinary resume reuses these responses.")
        elif stage == "complete" and kind == "succeeded":
            run = event.get("run", {})
            elapsed = run.get("elapsed_seconds", 0)
            usage = run.get("usage", {})
            scope = "observed cumulative " if self._managed_execution else ""
            self._write(f"Completed: {run.get('candidate_count', 0)} candidates; "
                        f"{scope}{run.get('request_attempts', 0)} HTTP attempts; this invocation {elapsed:.1f}s; "
                        f"{scope}tokens in/out: {usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
        elif kind == "failed":
            self._waiting_since = None
            self._write(f"Failed during {stage}; run did not complete; inspect saved artifacts")
        elif self.mode == "verbose" and kind in {"started", "completed", "prepared", "passed", "checked"}:
            # Deliberately omit event payloads (which can contain task/source data).
            self._write(f"[{stage}] {kind}")

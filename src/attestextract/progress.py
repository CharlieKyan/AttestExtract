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
                    self._write(f"[{self.current}/{self.total}] waiting for model; {elapsed:.0f}s elapsed")

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
            self._write(f"Model: {event.get('model', '?')} | output: {event.get('structured_output', '?')} | "
                        f"token limit: {event.get('max_output_tokens') or 'provider default'} | "
                        f"HTTP attempts: {event.get('max_attempts', '?')} | format retries: {event.get('format_retries', '?')}")
        elif stage == "document" and kind == "completed":
            self._write(f"Input: {event['character_count']} characters")
        elif stage == "chunking" and kind == "completed":
            self.total = event["chunk_count"]
            self._write(f"Chunks: {self.total}")
        elif stage == "model_request" and kind == "started":
            if self._last_chunk != event["chunk_id"]:
                self.current += 1
                self._last_chunk = event["chunk_id"]
            self._waiting_since = time.monotonic()
            if self._thread is None:
                self._thread = threading.Thread(target=self._heartbeat, daemon=True)
                self._thread.start()
            self._write(f"[{self.current}/{self.total}] requesting model; format attempt {event['format_attempt']}")
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
        elif kind == "retry" and stage in {"http", "format_retry"}:
            self._write(f"{prefix} {stage}: retry requested; an additional model call may be charged")
        elif stage == "chunk" and kind == "completed":
            self._write(f"{prefix} checked: {event['candidate_count']} candidates; "
                        f"{event['review_count']} review items; {event.get('info_count', 0)} info items")
        elif stage == "complete" and kind == "succeeded":
            run = event.get("run", {})
            elapsed = run.get("elapsed_seconds", 0)
            usage = run.get("usage", {})
            self._write(f"Completed: {run.get('candidate_count', 0)} candidates; "
                        f"{run.get('request_attempts', 0)} HTTP attempts; {elapsed:.1f}s; "
                        f"tokens in/out: {usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
        elif kind == "failed":
            self._waiting_since = None
            self._write(f"Failed during {stage}; run did not complete; inspect saved artifacts")
        elif self.mode == "verbose" and kind in {"started", "completed", "prepared", "passed", "checked"}:
            # Deliberately omit event payloads (which can contain task/source data).
            self._write(f"[{stage}] {kind}")

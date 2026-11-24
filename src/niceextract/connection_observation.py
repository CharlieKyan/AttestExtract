"""Value-free observations of one HTTP exchange, without controlling transport.

Trace payloads can contain credentials, URLs, headers and bodies. Only selected
stage names, durations and protocol enums leave this collector. Missing runtime
extensions remain unknown; an observer never infers TLS from configuration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx


_PHASES = ("tcp_connect", "tls_handshake", "request_headers", "request_body",
           "response_headers", "response_body")
_HTTP_STAGES = {"send_request_headers": "request_headers", "send_request_body": "request_body",
                "receive_response_headers": "response_headers", "receive_response_body": "response_body"}
_TLS_VERSIONS = frozenset({"TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"})
_HTTP_VERSIONS = frozenset({"HTTP/1.0", "HTTP/1.1", "HTTP/2", "HTTP/3"})


def _http_version(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value if isinstance(value, str) and value in _HTTP_VERSIONS else "unknown"


class ConnectionObservation:
    """Observe origin requests only, excluding a proxy's CONNECT response/TLS."""

    def __init__(self, url: str, *, method: str, custom_transport: bool,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self._host = httpx.URL(url).raw_host
        self._method = method.encode("ascii")
        self._custom = custom_transport
        self._clock = clock
        self._started = clock()
        self._active: dict[str, tuple[str, float]] = {}
        self._phase_seconds: dict[str, float | None] = dict.fromkeys(_PHASES)
        self.actual_tls_version = "unknown"
        self.actual_http_version = "unknown"
        self.headers_received = False
        self.body_complete = False
        self.status_code: int | None = None
        self.failure_phase: str | None = None

    def _origin_request(self, request: Any) -> bool:
        method = getattr(request, "method", None)
        if isinstance(method, str):
            method = method.encode("ascii", errors="replace")
        url = getattr(request, "url", None)
        host = getattr(url, "raw_host", None) or getattr(url, "host", None)
        if isinstance(host, str):
            host = host.encode("ascii", errors="replace")
        return method == self._method and host == self._host

    def _tls(self, stream: Any) -> None:
        if self._custom or stream is None:
            return
        try:
            ssl_object = stream.get_extra_info("ssl_object")
            version = ssl_object.version() if ssl_object is not None else None
            if isinstance(version, str) and version in _TLS_VERSIONS:
                self.actual_tls_version = version
        except Exception:
            # Optional diagnostic access must not change request behavior.
            return

    def trace(self, name: str, info: dict[str, Any]) -> None:
        if self._custom:
            return
        try:
            self._trace(name, info)
        except Exception:
            # Never forward payloads or failures of optional runtime extensions.
            return

    def _trace(self, name: str, info: dict[str, Any]) -> None:
        parts = name.split(".")
        if len(parts) != 3 or parts[2] not in {"started", "complete", "failed"}:
            return
        component, operation, state = parts
        key = ".".join(parts[:2])
        if state == "started":
            phase = None
            if component == "connection" and operation == "connect_tcp":
                phase = "tcp_connect"
            elif component in {"connection", "proxy"} and operation == "start_tls":
                hostname = info.get("server_hostname")
                if isinstance(hostname, str):
                    hostname = hostname.encode("ascii", errors="replace")
                if hostname == self._host:
                    phase = "tls_handshake"
            elif component in {"http11", "http2"} and operation in _HTTP_STAGES:
                if self._origin_request(info.get("request")):
                    phase = _HTTP_STAGES[operation]
            if phase is not None:
                self._active[key] = phase, self._clock()
            return
        active = self._active.pop(key, None)
        if active is None:
            return
        phase, started = active
        self._phase_seconds[phase] = (self._phase_seconds[phase] or 0.0) + max(0.0, self._clock() - started)
        if state == "failed":
            self.failure_phase = phase
            return
        if phase == "tls_handshake":
            self._tls(info.get("return_value"))
        elif phase == "response_headers":
            # HTTP/1.1 and HTTP/2 return different tuples; the response hook
            # supplies definitive protocol/status for both, before body read.
            self.headers_received = True
        elif phase == "response_body":
            self.body_complete = True

    def response(self, response: httpx.Response) -> None:
        """HTTPX calls this before consuming the body, including error statuses."""
        try:
            if not self._origin_request(response.request):
                return
            self.headers_received = True
            self.status_code = response.status_code
            if not self._custom:
                self.actual_http_version = _http_version(response.extensions.get("http_version"))
                self._tls(response.extensions.get("network_stream"))
        except Exception:
            return

    def snapshot(self, *, request_status: str) -> dict[str, Any]:
        known = (self.actual_tls_version != "unknown", self.actual_http_version != "unknown")
        observed = "observed" if all(known) else "partial" if any(known) or any(
            value is not None for value in self._phase_seconds.values()) else "unavailable"
        return {"observation_status": observed, "custom_transport": self._custom,
                "actual_tls_version": self.actual_tls_version, "actual_http_version": self.actual_http_version,
                "status_code": self.status_code, "request_status": request_status,
                "headers_received": self.headers_received, "body_complete": self.body_complete,
                "elapsed_seconds": max(0.0, self._clock() - self._started),
                "phase_seconds": dict(self._phase_seconds), "failure_phase": self.failure_phase}

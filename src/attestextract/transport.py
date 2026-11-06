"""Verified TLS compatibility and bounded, value-free transport diagnostics."""

from __future__ import annotations

import os
import re
import ssl
import math
from pathlib import Path
from typing import Any
from urllib.request import getproxies

import certifi
import httpx

from .errors import ConfigurationError


def bounded_timeout(timeout: float | None = None, read_timeout: float | None = None) -> httpx.Timeout:
    """Omission selects bounded phase defaults, never an unlimited timeout."""
    for value in (timeout, read_timeout):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not 0 < value <= 600):
            raise ConfigurationError("Timeouts must be greater than 0 and at most 600 seconds.")
    phases = ({"connect": 15.0, "read": 180.0, "write": 30.0, "pool": 15.0}
              if timeout is None else dict.fromkeys(("connect", "read", "write", "pool"), float(timeout)))
    if read_timeout is not None:
        phases["read"] = float(read_timeout)
    return httpx.Timeout(**phases)


def exception_chain(exc: BaseException) -> list[BaseException]:
    result = []
    current: BaseException | None = exc
    while current is not None and all(current is not item for item in result) and len(result) < 8:
        result.append(current)
        current = current.__cause__ or current.__context__
    return result


def tls_fallback_allowed(exc: BaseException) -> bool:
    """Only identified record MAC errors qualify; certificate failure wins."""
    chain = exception_chain(exc)
    if any(isinstance(item, ssl.SSLCertVerificationError) or
           isinstance(item, ssl.SSLError) and getattr(item, "reason", None) == "CERTIFICATE_VERIFY_FAILED"
           for item in chain):
        return False
    return any(isinstance(item, ssl.SSLError) and getattr(item, "reason", None) in {
        "SSLV3_ALERT_BAD_RECORD_MAC", "DECRYPTION_FAILED_OR_BAD_RECORD_MAC",
    } for item in chain)


def connection_signature(base_url: str, mode: str, transport: object) -> tuple:
    """Private cache identity; proxy/CA values must never enter diagnostics."""
    certificates = []
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        value = os.environ.get(name)
        try:
            stat = Path(value).stat() if value else None
        except OSError:
            stat = None
        certificates.append((value, (stat.st_mtime_ns, stat.st_size) if stat else None))
    return base_url, mode, id(transport), tuple(sorted(getproxies().items())), tuple(certificates)


def safe_to_retry(exc: httpx.TransportError) -> bool:
    # Read/write failures can occur after the provider has started generation.
    return isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout))


def timeout_phase(exc: BaseException) -> str | None:
    for kind, phase in ((httpx.ConnectTimeout, "connect"), (httpx.ReadTimeout, "read"),
                        (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool")):
        if isinstance(exc, kind):
            return phase
    return None


def tls_context(mode: str) -> ssl.SSLContext | bool:
    """Keep HTTPX defaults in auto mode; never disable certificate validation."""
    if mode == "auto":
        return True
    if mode != "1.2":
        raise ConfigurationError("Resolve TLS policy before constructing its SSL context.")
    # Match HTTPX's certificate environment precedence, including enterprise CAs.
    if os.environ.get("SSL_CERT_FILE"):
        context = ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    elif os.environ.get("SSL_CERT_DIR"):
        context = ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    else:
        context = ssl.create_default_context(cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def transport_details(exc: Exception, *, tls_mode: str, attempt: int) -> dict[str, Any]:
    """Keep exception types and numeric/TLS codes, never exception strings or URLs."""
    chain = []
    for current in exception_chain(exc):
        item: dict[str, Any] = {"type": type(current).__name__}
        for name in ("errno", "winerror", "verify_code"):
            value = getattr(current, name, None)
            if type(value) is int:
                item[name] = value
        reason = getattr(current, "reason", None)
        if isinstance(current, ssl.SSLError) and isinstance(reason, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", reason):
            item["reason"] = reason
        chain.append(item)
    return {"diagnostic_status": "transport_failure", "attempt": attempt,
            "tls_mode": tls_mode, "certificate_verification": True, "causes": chain}

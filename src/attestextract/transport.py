"""Verified TLS compatibility and bounded, value-free transport diagnostics."""

from __future__ import annotations

import os
import re
import ssl
from typing import Any

import certifi


def tls_context(mode: str) -> ssl.SSLContext | bool:
    """Keep HTTPX defaults in auto mode; never disable certificate validation."""
    if mode == "auto":
        return True
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
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item: dict[str, Any] = {"type": type(current).__name__}
        for name in ("errno", "winerror", "verify_code"):
            value = getattr(current, name, None)
            if type(value) is int:
                item[name] = value
        reason = getattr(current, "reason", None)
        if isinstance(current, ssl.SSLError) and isinstance(reason, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", reason):
            item["reason"] = reason
        chain.append(item)
        current = current.__cause__ or current.__context__
    return {"diagnostic_status": "transport_failure", "attempt": attempt,
            "tls_mode": tls_mode, "certificate_verification": True, "causes": chain}

"""Finite, explicit connection choices for the minimal extraction entry point.

This module performs no network discovery or generation. The execution layer
reserves and records each attempt before invoking an individual route backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Literal
import uuid

import httpx
from pydantic import Field

from .config import BackendConfig
from .diagnostics import RunTrace
from .errors import BackendError, ConfigurationError, InvalidResponseError
from .models import ContractModel, ExtractionRequest, GenerationOptions
from .providers import resolve_model_credentials
from .registry import default_registry


class ModelConnection(ContractModel):
    """One explicitly chosen service and credential; never discover other accounts."""

    base_url: str = Field(min_length=1, repr=False)
    model: str = Field(min_length=1)
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    api_key_env: str = Field(default="ATTESTEXTRACT_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout: float | None = Field(default=None, gt=0, le=600)
    read_timeout: float | None = Field(default=None, gt=0, le=600)
    tls_mode: Literal["auto", "1.2", "auto_fallback", "tls12_fallback"] = "tls12_fallback"
    generation: GenerationOptions | None = None


_TRANSPORT = {"timeout", "read_timeout", "tls_mode", "max_attempts"}
_CONFIG_EXCLUDE = _TRANSPORT | {"api_key_env", "format_retries"}
_CACHE_VERSION = "1"
_POLICY_VERSION = "1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AutomaticRoute:
    route_id: str
    role: Literal["primary", "fallback"]
    config: BackendConfig = field(repr=False)
    backend: Any = field(repr=False)
    verified_alternative: bool = False

    @property
    def generation(self) -> GenerationOptions:
        return self.config.generation

    def snapshot(self) -> dict[str, Any]:
        return {"id": self.route_id, "role": self.role,
                "config": self.config.model_dump(mode="json", exclude=_CONFIG_EXCLUDE),
                "verified_alternative": self.verified_alternative}


def public_route_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bind public provenance to a private configuration without exporting it."""
    fields = {"adapter", "model", "structured_output", "output_token_parameter", "generation"}
    return {"id": snapshot["id"], "role": snapshot["role"],
            "config": {name: value for name, value in snapshot["config"].items() if name in fields},
            "config_digest": _digest(snapshot)}


def _known_settings(base_url: str, model: str) -> tuple[dict, bool]:
    """Match the service as well as the model; names alone convey no capability."""
    try:
        url = httpx.URL(base_url)
    except (httpx.InvalidURL, ValueError):
        raise ConfigurationError("The API base URL is invalid.") from None
    standard = (url.scheme == "https" and url.port in (None, 443)
                and not url.userinfo and not url.query and not url.fragment)
    path = url.path.rstrip("/")
    if standard and url.host == "api.deepseek.com" and path in ("", "/v1"):
        # All official Chat endpoints reject Schema, including unlisted models.
        values = {"structured_output": "json_object"}
        if model == "deepseek-flash":
            values.update(provider_options={"thinking": {"type": "disabled"}},
                          generation={"max_output_tokens": 8192, "temperature": 0})
            return values, True
        return values, False
    qwen = (url.host == "dashscope.aliyuncs.com"
            or bool(url.host and url.host.endswith(".cn-beijing.maas.aliyuncs.com")))
    if standard and qwen and path == "/compatible-mode/v1" and model in {"qwen3.8-27b", "qwen-plus"}:
        return {"structured_output": "json_object", "provider_options": {"enable_thinking": False},
                "generation": {"max_output_tokens": 8192, "temperature": 0}}, False
    if standard and url.host == "api.wenwen-ai.com" and path == "/v1" and model in {"gemini-2.5-flash", "gpt-4.1"}:
        return {"structured_output": "json_schema"}, False
    return {"structured_output": "json_schema"}, False


def _configs(connection: ModelConnection, overrides: Mapping[str, Any]) -> list[tuple[BackendConfig, bool]]:
    allowed = {"timeout", "read_timeout", "tls_mode", "max_attempts", "generation", "provider_options",
               "structured_output", "output_token_parameter"}
    if overrides.keys() - allowed:
        raise ConfigurationError("Unsupported automatic connection option; use the model and credential arguments directly.")
    if overrides.get("max_attempts", 1) != 1:
        raise ConfigurationError("Automatic adaptation requires one HTTP attempt per reserved generation.")
    known, responses = _known_settings(connection.base_url, connection.model)
    values = {"base_url": connection.base_url, "model": connection.model,
              "api_key_env": connection.api_key_env, "timeout": connection.timeout,
              "read_timeout": connection.read_timeout, "tls_mode": connection.tls_mode,
              "max_attempts": 1, "generation": {"max_output_tokens": 8192}, **known}
    generation = dict(values["generation"])
    if connection.generation is not None:
        generation.update(connection.generation.model_dump(exclude_none=True))
    if "generation" in overrides:
        if not isinstance(overrides["generation"], Mapping):
            raise ConfigurationError("generation must be a mapping of generation options.")
        generation.update(overrides["generation"])
    values.update(overrides)
    values["generation"] = generation
    # Uniform timeout explicitly overrides a lower-priority read timeout.
    if overrides.get("timeout") is not None and "read_timeout" not in overrides:
        values["read_timeout"] = None
    try:
        first = BackendConfig.model_validate(values)
    except (ValueError, TypeError):
        raise ConfigurationError("Invalid automatic connection options; check field names, types and limits.") from None
    modes = [first.structured_output]
    if "structured_output" not in overrides:
        modes = ["json_schema", "json_object", "off"] if first.structured_output == "json_schema" else ["json_object", "off"]
    token_fields = [first.output_token_parameter]
    if "output_token_parameter" not in overrides:
        token_fields.append("max_completion_tokens")
    result = []
    for mode in modes:
        for token_field in token_fields:
            config = first.model_copy(update={"structured_output": mode, "output_token_parameter": token_field})
            result.append((config, False))
    if responses and "provider_options" not in overrides:
        response_modes = [first.structured_output] if "structured_output" in overrides else ["json_schema", "json_object"]
        for mode in response_modes:
            response = first.model_copy(update={"adapter": "deepseek-responses", "structured_output": mode,
                                               "output_token_parameter": "max_output_tokens",
                                               "provider_options": {"reasoning": {"effort": "none"}}})
            result.append((response, True))
    return result


def rejection_reason(exc: Exception, route: AutomaticRoute) -> str | None:
    """Classify only explicit, bounded provider rejections, never a generic 400."""
    if not isinstance(exc, BackendError):
        return None
    if exc.status_code in {401, 403} or exc.code in {
        "timeout", "transport_error", "authentication_error", "permission_denied", "refusal", "truncated_response"
    }:
        return None
    if isinstance(exc, InvalidResponseError) and (
        exc.validation_details is not None or exc.code == "empty_response"
    ):
        return "format"
    availability = "unavailable" if exc.code in {
        "billing_error", "rate_limit", "rate_limited", "server_error"
    } else None
    diagnostic = exc.http_diagnostics or {}
    if not isinstance(diagnostic, dict) or diagnostic.get("truncated"):
        return availability
    error = diagnostic.get("provider_error", {})
    if not isinstance(error, dict):
        return availability
    code = error.get("code", "")
    param = error.get("param", "")
    message = error.get("message", "")
    if not all(isinstance(value, str) and len(value) <= 1000 for value in (code, param, message)):
        return availability
    code, param, message = code.lower(), param.lower(), message.lower()
    if exc.status_code in {400, 422}:
        unsupported = code in {"unsupported_parameter", "unsupported_value", "unsupported_feature", "not_supported"}
        phrase = bool(re.search(r"\b(?:unsupported|not supported|does not support)\b", message))
        if (unsupported or phrase) and param in {"max_tokens", "max_completion_tokens"}:
            if param == route.config.output_token_parameter:
                return "token_parameter"
        if unsupported or phrase:
            format_param = param in {"response_format", "response_format.type", "text.format", "text.format.type"}
            mode_named = route.config.structured_output in {"json_schema", "json_object"} and route.config.structured_output in message
            if format_param and (unsupported or mode_named):
                return "output_mode"
        if code in {"unsupported_endpoint", "unsupported_api", "unsupported_operation"}:
            return "interface"
    if exc.status_code in {404, 405, 501} and code in {"unsupported_endpoint", "unsupported_api", "unsupported_operation"}:
        return "interface"
    if code in {"model_not_found", "model_unavailable", "insufficient_quota", "insufficient_balance"}:
        return "unavailable"
    return availability


class AutomaticBackend:
    """Immutable route inventory; it never performs a hidden generation."""

    name = "automatic"
    output_protocol = "flat-v1"

    def __init__(self, routes: list[AutomaticRoute], *, cache_dir: str | Path | None = None,
                 use_cache: bool = True) -> None:
        self.routes = {route.route_id: route for route in routes}
        self.preferred_route_id = next(route.route_id for route in routes if route.role == "primary")
        self.fallback_route_id = next((route.route_id for route in routes if route.role == "fallback"), None)
        self._cache_dir = Path(cache_dir) if cache_dir is not None else Path(
            os.environ.get("ATTESTEXTRACT_MODEL_CACHE_DIR", str(Path.home() / ".attestextract" / "cache")))
        self._use_cache = use_cache
        if use_cache:
            self.preferred_route_id = self._cached_route("primary") or self.preferred_route_id
            if self.fallback_route_id is not None:
                self.fallback_route_id = self._cached_route("fallback") or self.fallback_route_id

    @property
    def initial_route_id(self) -> str:
        return self.preferred_route_id

    @property
    def model(self) -> str:
        return self.routes[self.preferred_route_id].config.model

    @property
    def generation(self) -> GenerationOptions:
        return self.routes[self.preferred_route_id].generation

    @property
    def structured_output(self) -> str:
        return self.routes[self.preferred_route_id].config.structured_output

    @property
    def tls_mode(self) -> str:
        return self.routes[self.preferred_route_id].config.tls_mode

    @property
    def timeout(self) -> float | None:
        return self.routes[self.preferred_route_id].config.timeout

    @property
    def read_timeout(self) -> float | None:
        return self.routes[self.preferred_route_id].config.read_timeout

    max_attempts = 1

    @property
    def transport_settings(self) -> dict[str, Any]:
        return {route_id: route.config.model_dump(mode="json", include=_TRANSPORT)
                for route_id, route in self.routes.items()}

    def identity_snapshot(self) -> dict[str, Any]:
        return {"version": _POLICY_VERSION, "routes": [route.snapshot() for route in self.routes.values()]}

    def snapshot(self) -> dict[str, Any]:
        return {**self.identity_snapshot(), "preferred_route_id": self.preferred_route_id,
                "fallback_route_id": self.fallback_route_id}

    def generate(self, request: ExtractionRequest):
        raise ConfigurationError("Automatic routes require execution-layer reservations; invoke extract() rather than generate().")

    def next_route(self, route_id: str, exc: Exception, attempted: set[str],
                   disabled: set[str] | None = None) -> str | None:
        route = self.routes[route_id]
        reason = rejection_reason(exc, route)
        if reason is None:
            return None
        excluded = set(attempted) | set(disabled or ()) | {route_id}
        candidates = [item for item in self.routes.values() if item.route_id not in excluded and item.role == route.role]
        if reason == "output_mode":
            modes = {"json_schema": "json_object", "json_object": "off"}
            next_mode = modes.get(route.config.structured_output)
            for item in candidates:
                if (item.config.adapter == route.config.adapter and item.config.structured_output == next_mode
                        and item.config.output_token_parameter == route.config.output_token_parameter):
                    return item.route_id
        elif reason == "token_parameter":
            for item in candidates:
                if (item.config.adapter == route.config.adapter
                        and item.config.structured_output == route.config.structured_output
                        and item.config.output_token_parameter != route.config.output_token_parameter):
                    return item.route_id
        elif reason in {"format", "interface"}:
            strength = {"off": 0, "json_object": 1, "json_schema": 2}
            for item in candidates:
                if (item.verified_alternative and item.config.adapter != route.config.adapter
                        and strength[item.config.structured_output] >= strength[route.config.structured_output]):
                    return item.route_id
        if route.role == "primary" and self.fallback_route_id not in excluded:
            return self.fallback_route_id
        return None

    def choose_next(self, route: str | AutomaticRoute, error: Exception, attempted: set[str],
                    disabled: set[str] | None = None) -> str | None:
        return self.next_route(route if isinstance(route, str) else route.route_id, error, attempted, disabled)

    def _cache_key(self, role: str) -> str:
        snapshots = []
        for route in self.routes.values():
            if route.role == role:
                value = route.snapshot()
                snapshots.append({"config": value["config"], "verified_alternative": value["verified_alternative"]})
        return _digest({"version": _POLICY_VERSION, "routes": snapshots})

    def _cache_path(self, role: str) -> Path:
        return self._cache_dir / (self._cache_key(role) + ".json")

    def _cached_route(self, role: str) -> str | None:
        try:
            path = self._cache_path(role)
            if path.stat().st_size > 16384:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or set(payload) != {"cache_version", "configuration_digest", "config_digest", "evidence"}
                    or payload["cache_version"] != _CACHE_VERSION or payload["evidence"] != "observed_success"
                    or payload["configuration_digest"] != self._cache_key(role)):
                return None
            return next((route.route_id for route in self.routes.values() if route.role == role
                         and _digest(route.snapshot()["config"]) == payload["config_digest"]), None)
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return None

    def remember_success(self, route_id: str) -> None:
        if not self._use_cache:
            return
        route = self.routes[route_id]
        payload = {"cache_version": _CACHE_VERSION, "configuration_digest": self._cache_key(route.role),
                   "config_digest": _digest(route.snapshot()["config"]), "evidence": "observed_success"}
        target = self._cache_path(route.role)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass  # Optional preference caching never changes an extraction outcome.
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def invalidate(self, route_id: str) -> None:
        if not self._use_cache:
            return
        route = self.routes[route_id]
        if self._cached_route(route.role) == route_id:
            try:
                self._cache_path(route.role).unlink(missing_ok=True)
            except OSError:
                pass


def build_automatic_backend(
    primary: ModelConnection, *, fallback: ModelConnection | None = None,
    trace: RunTrace | None = None, environment: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None, cache_dir: str | Path | None = None,
    use_cache: bool = True,
) -> AutomaticBackend:
    if not isinstance(primary, ModelConnection) or fallback is not None and not isinstance(fallback, ModelConnection):
        raise ConfigurationError("Primary and fallback connections must be ModelConnection objects.")
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ConfigurationError("Automatic model overrides must be a mapping.")
    environment = dict(os.environ) if environment is None else environment
    routes = []
    registry = default_registry()
    for role, connection in (("primary", primary), ("fallback", fallback)):
        if connection is None:
            continue
        if role == "fallback" and connection.api_key is None and "api_key_env" not in connection.model_fields_set:
            raise ConfigurationError("A fallback needs its own explicit api_key or api_key_env binding.")
        if trace is not None and connection.api_key:
            trace.add_secrets(connection.api_key)
        choices = _configs(connection, dict(overrides or {}) if role == "primary" else {})
        key = resolve_model_credentials(choices[0][0], environment, api_key=connection.api_key)
        for index, (config, verified) in enumerate(choices, 1):
            backend = registry.create(config.adapter, config=config, api_key=key, trace=trace)
            # Backend URL parsing produces a canonical identity, without touching the network.
            config = config.model_copy(update={"base_url": backend.base_url})
            routes.append(AutomaticRoute(f"{role}-{index:02d}", role, config, backend, verified))
    return AutomaticBackend(routes, cache_dir=cache_dir, use_cache=use_cache)

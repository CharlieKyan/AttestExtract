"""Explicit backend registration, shared by Python and the CLI bootstrap."""

from __future__ import annotations

from collections.abc import Callable
import re

from . import backends
from .config import BackendConfig
from .diagnostics import RunTrace
from .errors import ConfigurationError
from .models import ExtractionBackend


BackendFactory = Callable[..., ExtractionBackend]


class BackendRegistry:
    """A local registry; configuration selects names but cannot execute imports."""

    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}

    def register(self, name: str, factory: BackendFactory) -> None:
        if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise ConfigurationError("Adapter names must use lowercase letters, digits, underscores or hyphens.")
        if name in self._factories:
            raise ConfigurationError("An adapter with this name is already registered.")
        if not callable(factory):
            raise ConfigurationError("An adapter factory must be callable.")
        self._factories[name] = factory

    def create(
        self, name: str, *, config: BackendConfig, api_key: str | None = None,
        trace: RunTrace | None = None,
    ) -> ExtractionBackend:
        if name not in self._factories:
            raise ConfigurationError("Unknown adapter; register it in the application bootstrap before running the CLI.")
        backend = self._factories[name](config=config, api_key=api_key, trace=trace)
        if not isinstance(getattr(backend, "name", None), str) or not backend.name.strip() or not callable(getattr(backend, "generate", None)):
            raise ConfigurationError("An adapter factory must return a named backend with generate(request).")
        return backend


def _compatible_factory(
    *, config: BackendConfig, api_key: str | None = None, trace: RunTrace | None = None,
) -> ExtractionBackend:
    return backends.OpenAICompatibleBackend(
        base_url=config.base_url, model=config.model, api_key=api_key,
        timeout=config.timeout, max_attempts=config.max_attempts, trace=trace,
        structured_output=config.structured_output, provider_options=config.provider_options,
        output_token_parameter=config.output_token_parameter,
    )


def default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register("openai-compatible", _compatible_factory)
    return registry

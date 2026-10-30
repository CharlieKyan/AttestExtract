"""Explicit named TOML profiles and deterministic CLI configuration merging."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import tomllib
from typing import Any, Literal

from pydantic import Field, ValidationError

from .errors import ConfigurationError
from .models import ContractModel, GenerationOptions


class BackendConfig(ContractModel):
    adapter: str = Field(default="openai-compatible", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: str = Field(min_length=1, repr=False)
    model: str = Field(min_length=1)
    api_key_env: str = Field(default="ATTESTEXTRACT_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout: float = Field(default=60, gt=0, le=60)
    max_attempts: int = Field(default=3, ge=1)
    structured_output: Literal["off", "json_object", "json_schema"] = "off"
    format_retries: int = Field(default=0, ge=0, le=1)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    provider_options: dict[str, Any] = Field(default_factory=dict, repr=False)
    output_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


def load_profile(path: str | Path, name: str) -> dict[str, Any]:
    """Read one profile as data; never import plugins or expand environment text."""
    try:
        document = tomllib.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise ConfigurationError("Could not read the selected configuration as UTF-8 TOML.") from None
    if set(document) != {"profiles"} or not isinstance(document["profiles"], dict):
        raise ConfigurationError("Configuration must contain only a profiles table.")
    profile = document["profiles"].get(name)
    if not isinstance(profile, dict):
        raise ConfigurationError("The selected profile is missing or is not a table.")
    # Validate before overrides so a typo or inline credential is never hidden.
    _validate(profile)
    return profile


def _validate(values: dict[str, Any]) -> BackendConfig:
    try:
        config = BackendConfig.model_validate(values)
    except (ValidationError, TypeError, ValueError):
        # Pydantic's default exception includes rejected values, possibly secrets.
        raise ConfigurationError("Invalid backend configuration: check field names, types and limits; use api_key_env for credentials.") from None
    if not config.base_url.strip() or not config.model.strip() or type(config.format_retries) is not int:
        raise ConfigurationError("Configuration needs nonblank base_url/model and an integer format_retries (0 or 1).")
    return config


def resolve_backend_config(
    *, overrides: Mapping[str, Any], environment: Mapping[str, str],
    config_path: str | Path | None = None, profile: str | None = None,
) -> BackendConfig:
    """Explicit CLI > selected profile > defaults; retain legacy env without a profile.

    Credentials are resolved separately from the already merged process/.env
    mapping. Old global endpoint/model variables cannot contaminate a profile.
    """
    if (config_path is None) != (profile is None):
        raise ConfigurationError("--config and --profile must be supplied together.")
    values = load_profile(config_path, profile) if config_path is not None else {
        "base_url": environment.get("ATTESTEXTRACT_BASE_URL"),
        "model": environment.get("ATTESTEXTRACT_MODEL"),
    }
    generation = dict(values.get("generation", {}))
    if overrides.get("generation") is not None:
        if not isinstance(overrides["generation"], Mapping):
            raise ConfigurationError("generation overrides must be a mapping of generation controls.")
        generation.update(overrides["generation"])
    for key, value in overrides.items():
        if value is not None:
            if key in {"temperature", "max_output_tokens"}:
                generation[key] = value
            elif key == "generation":
                continue
            else:
                values[key] = value
    if not values.get("base_url") or not values.get("model"):
        raise ConfigurationError("Configure --base-url and --model, or ATTESTEXTRACT_BASE_URL and ATTESTEXTRACT_MODEL, or a named profile.")
    values["generation"] = generation
    return _validate(values)

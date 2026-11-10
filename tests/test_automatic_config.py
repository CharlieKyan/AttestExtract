"""Automatic choices are explicit, bounded, and require no online discovery."""

import json

import httpx
import pytest

from attestextract.automatic import (
    ModelConnection, build_automatic_backend, public_route_snapshot, rejection_reason,
)
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import ExtractionRequest


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "cache"))
    def reject_network(*args, **kwargs):
        pytest.fail("Configuration must not contact a service")
    monkeypatch.setattr(httpx.Client, "send", reject_network)


def connection(url="https://unregistered.example/v1", model="never-registered-731", **kwargs):
    return ModelConnection(base_url=url, model=model, api_key="synthetic-primary-secret", **kwargs)


def error(param, message="", code="unsupported_parameter", status=400):
    return BackendError("request rejected", status_code=status, code="invalid_request",
                        http_diagnostics={"provider_error": {"code": code, "param": param, "message": message},
                                          "truncated": False})


def test_unknown_service_uses_minimal_schema_request_and_opaque_model_name():
    plan = build_automatic_backend(connection(model="unregistered:opaque-model"), environment={})
    route = plan.routes[plan.initial_route_id]
    request = ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "JSON. Lamp A uses 8 W."}],
                                output_schema={"type": "object", "properties": {}}, generation=route.generation)
    payload = route.backend.prepare_payload(request)
    assert payload["model"] == "unregistered:opaque-model"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["max_tokens"] == 8192
    assert "temperature" not in payload
    assert "enable_thinking" not in payload and "thinking" not in payload
    assert all(item.backend.max_attempts == 1 for item in plan.routes.values())
    with pytest.raises(ConfigurationError, match="reservations"):
        plan.generate(request)


@pytest.mark.parametrize("url,model,mode,temperature,thinking,adapters", [
    ("https://api.deepseek.com", "deepseek-flash", "json_object", 0, {"thinking": {"type": "disabled"}},
     {"openai-compatible", "deepseek-responses"}),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-27b", "json_object", 0,
     {"enable_thinking": False}, {"openai-compatible"}),
    ("https://api.wenwen-ai.com/v1", "gemini-2.5-flash", "json_schema", None, {}, {"openai-compatible"}),
    ("https://api.wenwen-ai.com/v1", "gpt-4.1", "json_schema", None, {}, {"openai-compatible"}),
])
def test_exact_service_rules(url, model, mode, temperature, thinking, adapters):
    plan = build_automatic_backend(connection(url, model), environment={})
    config = plan.routes[plan.initial_route_id].config
    assert config.structured_output == mode
    assert config.generation.temperature == temperature
    assert config.provider_options == thinking
    assert {route.config.adapter for route in plan.routes.values()} == adapters


def test_model_name_does_not_import_foreign_provider_options():
    plan = build_automatic_backend(connection(model="deepseek-flash"), environment={})
    assert plan.structured_output == "json_schema"
    assert plan.routes[plan.initial_route_id].config.provider_options == {}
    assert all(route.config.adapter == "openai-compatible" for route in plan.routes.values())


def test_known_api_other_prefix_does_not_import_official_rules():
    plan = build_automatic_backend(connection("https://api.wenwen-ai.com/custom", "gpt-4.1"), environment={})
    assert all(not route.verified_alternative for route in plan.routes.values())


def test_explicit_mode_and_generation_are_preserved():
    plan = build_automatic_backend(connection(), environment={}, overrides={
        "structured_output": "json_object", "output_token_parameter": "max_completion_tokens",
        "generation": {"max_output_tokens": 1234}, "read_timeout": 221,
    })
    assert len(plan.routes) == 1
    config = plan.routes[plan.initial_route_id].config
    assert config.generation.max_output_tokens == 1234
    assert config.read_timeout == 221
    assert plan.next_route(plan.initial_route_id, error("response_format"), set()) is None


@pytest.mark.parametrize("overrides", [{"max_attempts": 2}, {"unexpected": "private"},
                                       {"generation": "private"}, {"generation": {"max_output_tokens": -1}}])
def test_invalid_automatic_overrides_do_not_echo_values(overrides):
    with pytest.raises(ConfigurationError) as caught:
        build_automatic_backend(connection(), environment={}, overrides=overrides)
    assert "private" not in str(caught.value)


def test_only_explicit_mode_rejection_downgrades_in_order():
    plan = build_automatic_backend(connection(), environment={})
    first = plan.initial_route_id
    second = plan.next_route(first, error("response_format", "json_schema is not supported"), {first})
    assert plan.routes[second].config.structured_output == "json_object"
    third = plan.next_route(second, error("response_format", "json_object is not supported"), {first, second})
    assert plan.routes[third].config.structured_output == "off"
    assert plan.next_route(third, error("response_format"), {first, second, third}) is None


@pytest.mark.parametrize("exc", [
    error("response_format", "invalid schema properties", code="invalid_request_error"),
    error("some_parameter", "bad request", code="invalid_request_error"),
    error("response_format", "json_schema must include required", code="invalid_request_error"),
    BackendError("lost", code="transport_error", retryable=True),
    BackendError("timeout", code="timeout", retryable=True),
    BackendError("credential", code="authentication_error", status_code=401),
    InvalidResponseError("refusal", code="refusal"),
    InvalidResponseError("truncated", code="truncated_response"),
])
def test_unknown_errors_and_uncertain_outcomes_do_not_switch_even_with_backup(exc):
    plan = build_automatic_backend(connection(), fallback=connection(model="backup"), environment={})
    assert plan.next_route(plan.initial_route_id, exc, set()) is None


def test_token_parameter_mapping_keeps_the_limit_and_output_mode():
    plan = build_automatic_backend(connection(), environment={})
    first = plan.initial_route_id
    second = plan.next_route(first, error("max_tokens", "Use max_completion_tokens"), {first})
    config = plan.routes[second].config
    assert config.output_token_parameter == "max_completion_tokens"
    assert config.structured_output == "json_schema"
    assert config.generation.max_output_tokens == 8192
    assert plan.next_route(first, error("max_completion_tokens"), {first}) is None


def test_format_failure_does_not_downgrade_output_constraint():
    plan = build_automatic_backend(connection(), environment={})
    exc = InvalidResponseError("invalid JSON", validation_details={"phase": "json_syntax"})
    assert plan.next_route(plan.initial_route_id, exc, set()) is None
    with_backup = build_automatic_backend(connection(), fallback=connection(model="backup"), environment={})
    assert with_backup.next_route(with_backup.initial_route_id, exc, set()) == with_backup.fallback_route_id


def test_verified_deepseek_alternative_can_handle_format_or_interface_failure():
    plan = build_automatic_backend(connection("https://api.deepseek.com", "deepseek-flash"), environment={})
    first = plan.initial_route_id
    for exc in [InvalidResponseError("invalid", validation_details={"phase": "records_contract"}),
                error("", code="unsupported_endpoint", status=404)]:
        selected = plan.next_route(first, exc, {first})
        assert plan.routes[selected].config.adapter == "deepseek-responses"
        assert plan.routes[selected].config.structured_output == "json_schema"


def test_disabled_or_already_attempted_routes_cannot_loop():
    plan = build_automatic_backend(connection(), fallback=connection(model="backup"), environment={})
    assert plan.next_route(plan.initial_route_id, error("response_format"), set(plan.routes)) is None
    assert plan.next_route(plan.initial_route_id, error("response_format"), set(), set(plan.routes)) is None


@pytest.mark.parametrize("status,code", [(402, "billing_error"), (429, "rate_limited"), (503, "server_error")])
def test_explicit_availability_failure_can_use_only_the_chosen_backup(status, code):
    plan = build_automatic_backend(connection(), fallback=connection(model="chosen-backup"), environment={})
    exc = BackendError("unavailable", status_code=status, code=code,
                       http_diagnostics={"truncated": True})
    assert plan.next_route(plan.initial_route_id, exc, set()) == plan.fallback_route_id
    assert plan.next_route(plan.fallback_route_id, exc, set()) is None


def test_unknown_not_found_does_not_guess_model_or_interface():
    plan = build_automatic_backend(connection(), fallback=connection(model="backup"), environment={})
    exc = BackendError("not found", status_code=404, code="not_found")
    assert plan.next_route(plan.initial_route_id, exc, set()) is None


@pytest.mark.parametrize("provider_code", ["unsupported_endpoint", "unsupported_api", "unsupported_operation"])
def test_explicit_501_interface_rejection_precedes_generic_server_error(provider_code):
    from attestextract.backends import _http_error_code

    plan = build_automatic_backend(connection("https://api.deepseek.com", "deepseek-flash"),
                                   fallback=connection(model="backup"), environment={})
    route = plan.routes[plan.initial_route_id]
    exc = BackendError("interface unavailable", status_code=501, code=_http_error_code(501),
                       http_diagnostics={"provider_error": {"code": provider_code}})
    assert rejection_reason(exc, route) == "interface"
    selected = plan.next_route(route.route_id, exc, {route.route_id})
    assert plan.routes[selected].role == "primary"
    assert plan.routes[selected].config.adapter == "deepseek-responses"


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_status_cannot_be_overridden_by_interface_claim(status):
    plan = build_automatic_backend(connection("https://api.deepseek.com", "deepseek-flash"),
                                   fallback=connection(model="backup"), environment={})
    exc = BackendError("denied", status_code=status, code="server_error",
                       http_diagnostics={"provider_error": {"code": "unsupported_endpoint"}})
    assert plan.next_route(plan.initial_route_id, exc, set()) is None


def test_credentials_are_explicit_isolated_and_never_serialized():
    primary = ModelConnection(base_url="https://new.example/v1", model="new-model", api_key_env="MY_KEY")
    fallback = ModelConnection(base_url="https://backup.example/v1", model="backup", api_key_env="BACKUP_KEY")
    plan = build_automatic_backend(primary, fallback=fallback, environment={
        "MY_KEY": "primary-secret", "BACKUP_KEY": "backup-secret", "DEEPSEEK_API_KEY": "unrelated-secret",
    })
    for route in plan.routes.values():
        assert route.backend._api_key == ("primary-secret" if route.role == "primary" else "backup-secret")
    serialized = json.dumps(plan.snapshot())
    for forbidden in ("primary-secret", "backup-secret", "unrelated-secret", "api_key", "MY_KEY", "BACKUP_KEY"):
        assert forbidden not in serialized
    assert "synthetic-primary-secret" not in repr(connection())
    assert "api_key" not in connection().model_dump()


def test_fallback_does_not_inherit_primary_global_credential():
    fallback = ModelConnection(base_url="https://other.example/v1", model="other")
    with pytest.raises(ConfigurationError, match="explicit"):
        build_automatic_backend(connection(), fallback=fallback, environment={"ATTESTEXTRACT_API_KEY": "primary-secret"})


def test_public_provenance_omits_service_configuration():
    plan = build_automatic_backend(connection(), environment={}, overrides={"provider_options": {"routing": "private"}})
    snapshot = public_route_snapshot(plan.routes[plan.initial_route_id].snapshot())
    text = json.dumps(snapshot)
    assert set(snapshot) == {"id", "role", "config", "config_digest"}
    assert len(snapshot["config_digest"]) == 64
    for forbidden in ("base_url", "unregistered.example", "provider_options", "private", "api_key", "tls_mode"):
        assert forbidden not in text


def test_success_cache_is_only_an_observation_and_never_changes_identity(tmp_path):
    plan = build_automatic_backend(connection(), environment={})
    first = plan.initial_route_id
    next_id = plan.next_route(first, error("response_format"), {first})
    plan.remember_success(next_id)
    cached = build_automatic_backend(connection(), environment={})
    assert cached.initial_route_id == next_id
    assert cached.identity_snapshot() == plan.identity_snapshot()
    payload = json.loads(next((tmp_path / "cache").glob("*.json")).read_text())
    assert payload["evidence"] == "observed_success"
    assert not any(value in json.dumps(payload) for value in ("secret", "https", "never-registered"))
    cached.invalidate(next_id)
    assert build_automatic_backend(connection(), environment={}).initial_route_id == first


def test_cached_parameters_do_not_apply_after_configuration_change():
    plan = build_automatic_backend(connection(), environment={})
    plan.remember_success(plan.next_route(plan.initial_route_id, error("response_format"), set()))
    changed = build_automatic_backend(connection(), environment={}, overrides={"generation": {"max_output_tokens": 100}})
    assert changed.structured_output == "json_schema"
    assert changed.generation.max_output_tokens == 100


def test_cache_cannot_inject_configuration_and_malformed_cache_is_ignored(tmp_path):
    plan = build_automatic_backend(connection(), environment={})
    plan.remember_success(plan.initial_route_id)
    path = next((tmp_path / "cache").glob("*.json"))
    payload = json.loads(path.read_text())
    payload["base_url"] = "https://foreign.example"
    path.write_text(json.dumps(payload))
    assert build_automatic_backend(connection(), environment={}).initial_route_id == plan.initial_route_id
    path.write_text("[invalid")
    assert build_automatic_backend(connection(), environment={}).initial_route_id == plan.initial_route_id
    path.write_text(" " * 16385)
    assert build_automatic_backend(connection(), environment={}).initial_route_id == plan.initial_route_id


def test_use_cache_false_does_not_write_or_read(tmp_path):
    plan = build_automatic_backend(connection(), environment={}, use_cache=False)
    plan.remember_success(plan.initial_route_id)
    assert not (tmp_path / "cache").exists()


def test_transport_changes_do_not_change_content_identity():
    plan = build_automatic_backend(connection(), environment={})
    changed = build_automatic_backend(connection(read_timeout=240, tls_mode="1.2"), environment={})
    assert plan.identity_snapshot() == changed.identity_snapshot()
    assert plan.transport_settings != changed.transport_settings

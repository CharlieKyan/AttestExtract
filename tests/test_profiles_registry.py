"""Profile selection and registry behavior, using synthetic inputs and no network."""

import json

import pytest

from attestextract import cli
from attestextract.config import BackendConfig, resolve_backend_config
from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry, default_registry


PROFILE = '''[profiles.test]
adapter = "custom"
base_url = "https://synthetic.invalid/v1"
model = "profile-model"
api_key_env = "SYNTHETIC_KEY"
timeout = 12.0
max_attempts = 1
structured_output = "json_object"
format_retries = 1
[profiles.test.generation]
temperature = 0.4
max_output_tokens = 80
'''


def test_explicit_cli_overrides_profile_but_preserves_other_profile_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE)
    config = resolve_backend_config(
        config_path=path, profile="test", environment={
            "ATTESTEXTRACT_MODEL": "stale-model", "ATTESTEXTRACT_BASE_URL": "https://stale.invalid",
        }, overrides={"model": "cli-model", "temperature": 0.0, "structured_output": "off", "format_retries": 0},
    )
    assert config.model == "cli-model"
    assert config.base_url == "https://synthetic.invalid/v1"
    assert config.structured_output == "off" and config.format_retries == 0
    assert config.max_attempts == 1 and config.timeout == 12
    assert config.generation.temperature == 0 and config.generation.max_output_tokens == 80


def test_legacy_environment_and_defaults_are_preserved():
    config = resolve_backend_config(overrides={"model": "explicit"}, environment={
        "ATTESTEXTRACT_MODEL": "old", "ATTESTEXTRACT_BASE_URL": "https://synthetic.invalid",
    })
    assert config.model == "explicit" and config.adapter == "openai-compatible"
    assert config.timeout == 60 and config.max_attempts == 3
    assert config.structured_output == "off" and config.format_retries == 0
    assert config.generation.max_output_tokens is None


def test_python_generation_mapping_is_merged_and_never_silently_discarded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE)
    config = resolve_backend_config(config_path=path, profile="test", environment={}, overrides={
        "generation": {"temperature": 0.1, "max_output_tokens": 90}, "temperature": 0.2,
    })
    assert config.generation.temperature == 0.2 and config.generation.max_output_tokens == 90
    with pytest.raises(ConfigurationError):
        resolve_backend_config(config_path=path, profile="test", environment={}, overrides={"generation": "invalid"})


@pytest.mark.parametrize("content", [
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\napi_key="private-marker"',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\ntimeout="private-marker"',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\nformat_retries=true',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\nmax_attempts=true',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\ntimeout=inf',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\nunknown="private-marker"',
    '[profiles.test]\nbase_url="https://synthetic.invalid"\nmodel="x"\n[profiles.test.generation]\ntemperature=nan',
    'private-marker', '[defaults]\nprivate_marker=1',
])
def test_invalid_profiles_fail_without_echoing_values(content, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigurationError) as caught:
        resolve_backend_config(config_path=path, profile="test", environment={}, overrides={})
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize("config_path,profile", [(None, "test"), ("config.toml", None)])
def test_config_and_profile_must_be_selected_together(config_path, profile):
    with pytest.raises(ConfigurationError, match="together"):
        resolve_backend_config(config_path=config_path, profile=profile, overrides={}, environment={})


def test_registry_is_local_and_rejects_unknown_duplicates_and_invalid_results():
    registry = BackendRegistry()
    config = BackendConfig(base_url="https://synthetic.invalid", model="x")
    registry.register("custom", lambda **kwargs: object())
    with pytest.raises(ConfigurationError, match="already"):
        registry.register("custom", lambda **kwargs: object())
    with pytest.raises(ConfigurationError, match="named backend"):
        registry.create("custom", config=config)
    with pytest.raises(ConfigurationError, match="Unknown"):
        default_registry().create("custom", config=config)
    with pytest.raises(ConfigurationError, match="callable"):
        registry.register("bad", None)
    with pytest.raises(ConfigurationError, match="names"):
        registry.register("module:execute", lambda **kwargs: None)


def _inputs(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes("Device A uses 8 W.".encode())
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"name": "power", "record_types": {"power": {"fields": {"value": {"type": "integer"}}}}}))
    profile = tmp_path / "config.toml"
    profile.write_text(PROFILE)
    return ["extract", "--input", str(source), "--task", str(task),
            "--config", str(profile), "--profile", "test"]


def test_cli_uses_registered_adapter_and_profile_credentials_without_global_contamination(tmp_path, monkeypatch):
    argv = _inputs(tmp_path)
    env = tmp_path / "settings.env"
    env.write_text("SYNTHETIC_KEY=file-value\nATTESTEXTRACT_MODEL=stale\nATTESTEXTRACT_BASE_URL=https://stale.invalid")
    monkeypatch.setenv("SYNTHETIC_KEY", "process-value")
    seen = []

    class Backend:
        name = "custom"
        mode = "replay"

        def generate(self, request):
            assert request.generation.max_output_tokens == 80
            assert request.generation.temperature == 0.0
            return BackendResponse(text='{"records":[{"record_type":"power","fields":{"value":{"value":8,"quote":"8"}}}]}')

    def factory(*, config, api_key, trace):
        seen.append((config, api_key))
        return Backend()

    registry = BackendRegistry()
    registry.register("custom", factory)
    output = tmp_path / "result.json"
    logs = tmp_path / "logs"
    assert cli.main(argv + ["--env-file", str(env), "--output", str(output), "--log-dir", str(logs),
                            "--temperature", "0", "--format-retries", "0"], registry=registry) == 0
    config, key = seen[0]
    assert config.model == "profile-model" and key == "process-value"
    assert config.timeout == 12 and config.structured_output == "json_object"
    assert config.format_retries == 0
    assert json.loads(output.read_text())["run"]["mode"] == "replay"
    assert "process-value" not in "".join(p.read_text(encoding="utf-8") for p in logs.rglob("*") if p.is_file())


@pytest.mark.parametrize("target", ["--output", "--report"])
def test_cli_cannot_overwrite_profile_before_backend_creation(tmp_path, target):
    argv = _inputs(tmp_path)
    path = tmp_path / "config.toml"
    original = path.read_bytes()
    args = ["--output", str(tmp_path / "result.json")] if target == "--report" else []
    assert cli.main(argv + args + [target, str(path)]) == 1
    assert path.read_bytes() == original


def test_unknown_cli_adapter_fails_before_request(tmp_path, capsys):
    argv = _inputs(tmp_path)
    output = tmp_path / "result.json"
    assert cli.main(argv + ["--output", str(output)]) == 1
    assert "Unknown adapter" in capsys.readouterr().err
    assert not output.exists()

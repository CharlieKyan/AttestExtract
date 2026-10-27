"""Dotenv loading uses synthetic files and never evaluates their contents."""

from __future__ import annotations

import os

import pytest

from attestextract.envconfig import load_env_file, parse_env_file
from attestextract.errors import ConfigurationError


def test_common_dotenv_syntax_bom_comments_quotes_and_unicode(tmp_path):
    path = tmp_path / "model.env"
    path.write_bytes((
        "\ufeff# Synthetic model connection settings\r\n"
        "export ATTESTEXTRACT_BASE_URL = https://model.example/v1 # endpoint\r\n"
        'ATTESTEXTRACT_MODEL="model with spaces" # note\r\n'
        "ATTESTEXTRACT_API_KEY='synthetic-key#part'\r\n"
        "CUSTOM_KEY = unquoted#literal\r\n"
        "EMPTY=\r\n"
        'EMPTY_QUOTED=""\r\n'
        'LABEL="中文🙂 # inside quote"\r\n'
    ).encode("utf-8"))
    assert parse_env_file(path) == {
        "ATTESTEXTRACT_BASE_URL": "https://model.example/v1",
        "ATTESTEXTRACT_MODEL": "model with spaces",
        "ATTESTEXTRACT_API_KEY": "synthetic-key#part",
        "CUSTOM_KEY": "unquoted#literal",
        "EMPTY": "",
        "EMPTY_QUOTED": "",
        "LABEL": "中文🙂 # inside quote",
    }


def test_existing_environment_wins_including_empty_values_and_is_not_mutated(tmp_path):
    path = tmp_path / "model.env"
    path.write_text(
        "ATTESTEXTRACT_MODEL=file-model\n"
        "ATTESTEXTRACT_API_KEY=file-key\n"
        "CUSTOM_PROVIDER_KEY=custom-file-key\n"
        "EMPTY=file-default\n",
        encoding="utf-8",
    )
    existing = {"ATTESTEXTRACT_API_KEY": "process-key", "EMPTY": "", "OTHER": "retained"}
    before = dict(existing)
    loaded = load_env_file(path, environ=existing)
    assert loaded == {
        "ATTESTEXTRACT_MODEL": "file-model",
        "ATTESTEXTRACT_API_KEY": "process-key",
        "CUSTOM_PROVIDER_KEY": "custom-file-key",
        "EMPTY": "",
        "OTHER": "retained",
    }
    assert existing == before


def test_default_environment_merge_does_not_set_process_variables(monkeypatch, tmp_path):
    path = tmp_path / "model.env"
    path.write_text("ATTESTEXTRACT_MODEL=file-model\nCUSTOM_PROVIDER_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "process-model")
    monkeypatch.delenv("CUSTOM_PROVIDER_KEY", raising=False)
    loaded = load_env_file(path)
    assert loaded["ATTESTEXTRACT_MODEL"] == "process-model"
    assert loaded["CUSTOM_PROVIDER_KEY"] == "file-key"
    assert "CUSTOM_PROVIDER_KEY" not in os.environ


def test_empty_explicit_environment_does_not_fall_back_to_process(monkeypatch, tmp_path):
    path = tmp_path / "model.env"
    path.write_text("ATTESTEXTRACT_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "process-model")
    assert load_env_file(path, environ={}) == {"ATTESTEXTRACT_MODEL": "file-model"}


def test_values_are_data_not_shell_commands_or_variable_expansion(tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    path = tmp_path / "model.env"
    path.write_text(
        f"COMMAND=$(New-Item '{marker}')\n"
        'REFERENCE="${ATTESTEXTRACT_API_KEY}"\n'
        "REFERENCE_TWO='$ATTESTEXTRACT_API_KEY'\n"
        "PYTHON=__import__('os').system('echo unsafe')\n",
        encoding="utf-8",
    )
    loaded = parse_env_file(path)
    assert loaded["COMMAND"] == f"$(New-Item '{marker}')"
    assert loaded["REFERENCE"] == "${ATTESTEXTRACT_API_KEY}"
    assert loaded["REFERENCE_TWO"] == "$ATTESTEXTRACT_API_KEY"
    assert loaded["PYTHON"].startswith("__import__")
    assert not marker.exists()


def test_quoted_escapes_and_windows_paths_are_preserved(tmp_path):
    path = tmp_path / "model.env"
    path.write_text(
        r'''QUOTED="model \"quoted\""''' + "\n"
        + r"SINGLE='model \'quoted\''" + "\n"
        + r'PATH_VALUE="C:\models\new"' + "\n"
        + r'BACKSLASH="a\\b"' + "\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {
        "QUOTED": 'model "quoted"',
        "SINGLE": "model 'quoted'",
        "PATH_VALUE": r"C:\models\new",
        "BACKSLASH": "a\\b",
    }


def test_comments_can_explain_shell_syntax_without_becoming_configuration(tmp_path):
    path = tmp_path / "model.env"
    path.write_text(
        "# In PowerShell use $env:KEY and Read-Host\n"
        "KEY=synthetic-key # $env:KEY=Read-Host\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {"KEY": "synthetic-key"}


@pytest.mark.parametrize(("line", "hint"), [
    ('$env:ATTESTEXTRACT_API_KEY="synthetic-secret"', "PowerShell"),
    ("KEY=$env:OTHER_SECRET", "PowerShell"),
    ('KEY=Read-Host "synthetic-secret"', "Read-Host"),
    ('KEY="(Read-Host -AsSecureString synthetic-secret)"', "Read-Host"),
    ('KEY="[synthetic-secret](https://model.example/v1)"', "Markdown link"),
    ("KEY=[endpoint](https://model.example/v1?synthetic-secret)", "Markdown link"),
    ("```dotenv synthetic-secret", "code fences"),
    ("KEY: synthetic-secret", "expected"),
    ("1BAD=synthetic-secret", "expected"),
    ("BAD-NAME=synthetic-secret", "expected"),
    ('KEY="synthetic-secret', "close on the same line"),
    ("KEY='synthetic-secret' trailing", "after the closing quote"),
    ("KEY=synthetic-secret\x00", "NUL"),
])
def test_bad_formats_report_line_and_actionable_hint_without_value(tmp_path, line, hint):
    path = tmp_path / "model.env"
    path.write_bytes(("# synthetic config\n" + line + "\n").encode("utf-8"))
    with pytest.raises(ConfigurationError) as caught:
        parse_env_file(path)
    assert "line 2" in str(caught.value)
    assert hint in str(caught.value)
    assert "synthetic-secret" not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_duplicate_keys_are_rejected_without_printing_key_or_value(tmp_path):
    path = tmp_path / "model.env"
    path.write_text("SECRET_KEY=first-secret\nSECRET_KEY=second-secret\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        parse_env_file(path)
    message = str(caught.value)
    assert "line 2" in message
    assert "duplicate" in message
    assert "SECRET_KEY" not in message
    assert "first-secret" not in message and "second-secret" not in message


@pytest.mark.parametrize("content", [b"\xff\xfeprivate-secret", b"\x80private-secret"])
def test_invalid_encoding_has_safe_errors(tmp_path, content):
    path = tmp_path / "model.env"
    path.write_bytes(content)
    with pytest.raises(ConfigurationError) as caught:
        parse_env_file(path)
    assert "UTF-8" in str(caught.value)
    assert "private-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_missing_file_has_a_safe_error(tmp_path):
    path = tmp_path / "private-secret.env"
    with pytest.raises(ConfigurationError) as caught:
        parse_env_file(path)
    assert "private-secret" not in str(caught.value)
    assert caught.value.__suppress_context__

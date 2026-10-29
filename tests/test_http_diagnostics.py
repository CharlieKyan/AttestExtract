"""Provider HTTP errors stay useful, bounded, and distinct from local inference."""

from __future__ import annotations

import json

import httpx
import pytest

from attestextract import cli, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError


TASK = {
    "name": "Synthetic HTTP diagnostics",
    "record_types": {"entry": {"fields": {"amount": {"type": "number"}}}},
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_text(directory):
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())


def run_failure(tmp_path, response, *, api_key="synthetic-http-secret", structured_output="off"):
    directory = tmp_path / "http-run"
    trace = RunTrace(directory)
    requests = []

    def endpoint(request):
        requests.append(request)
        return response

    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid/v1", model="synthetic-model", api_key=api_key,
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None,
        trace=trace, structured_output=structured_output,
    )
    with pytest.raises(BackendError) as caught:
        extract("Synthetic source only.", TASK, backend=backend, trace=trace)
    error = caught.value
    diagnostic = error.http_diagnostics
    assert isinstance(diagnostic, dict)
    assert diagnostic["schema_version"] == "0.1"
    assert diagnostic["layer"] == "http"
    assert diagnostic["status_code"] == response.status_code
    assert diagnostic["attempt"] == error.attempts
    assert diagnostic["chunk_id"]
    assert diagnostic["root_cause_verified"] is False
    files = list((directory / "http").glob("*.error.json"))
    assert files
    final = directory / "http" / f"{diagnostic['chunk_id']}.attempt-{diagnostic['attempt']}.error.json"
    assert read_json(final) == diagnostic
    assert directory / error.http_details == final
    saved_error = read_json(directory / "error.json")
    assert saved_error["http_diagnostics"] == diagnostic
    assert saved_error["http_details"] == error.http_details
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert not (directory / "result.json").exists()
    return error, diagnostic, directory, requests


@pytest.mark.parametrize("status_code", [400, 422])
def test_provider_error_details_and_request_id_are_retained_without_retry_or_fallback(tmp_path, status_code):
    response = httpx.Response(status_code, headers={"x-request-id": "synthetic-request-400"}, json={
        "error": {
            "code": "unsupported_response_format", "type": "invalid_request_error",
            "message": "Synthetic provider does not accept the requested format.",
            "param": "response_format", "private_extra": "DO-NOT-SAVE-THIS-FIELD",
        },
        "private_top_level": "DO-NOT-SAVE-THE-RESPONSE-BODY",
    })
    exc, diagnostic, directory, requests = run_failure(tmp_path, response, structured_output="json_schema")

    assert len(requests) == exc.attempts == 1
    assert diagnostic["body_format"] == "json"
    assert diagnostic["diagnostic_status"] == "provider_detail_available"
    assert diagnostic["provider_error"] == {
        "code": "unsupported_response_format", "type": "invalid_request_error",
        "message": "Synthetic provider does not accept the requested format.", "param": "response_format",
    }
    assert diagnostic["request_ids"]["x-request-id"] == "synthetic-request-400"
    assert diagnostic.get("compatibility_hint")
    assert "compatibility_hint" not in diagnostic["provider_error"]
    assert "Synthetic provider does not accept" not in str(exc)
    saved = artifact_text(directory)
    assert "DO-NOT-SAVE-THIS-FIELD" not in saved
    assert "DO-NOT-SAVE-THE-RESPONSE-BODY" not in saved


def test_google_style_error_preserves_numeric_code_status_and_message(tmp_path):
    response = httpx.Response(400, json={"error": {
        "code": 400, "status": "INVALID_ARGUMENT", "message": "Synthetic argument mismatch.",
    }})
    _, diagnostic, _, requests = run_failure(tmp_path, response)
    assert len(requests) == 1
    assert diagnostic["provider_error"] == {
        "code": 400, "status": "INVALID_ARGUMENT", "message": "Synthetic argument mismatch.",
    }


@pytest.mark.parametrize("payload", [
    {"error": "Synthetic simple error."},
    {"detail": "Synthetic simple error."},
    {"message": "Synthetic simple error."},
])
def test_simple_provider_message_shapes_have_available_details(tmp_path, payload):
    _, diagnostic, _, _ = run_failure(tmp_path, httpx.Response(401, json=payload))
    assert diagnostic["diagnostic_status"] == "provider_detail_available"
    assert diagnostic["provider_error"]["message"] == "Synthetic simple error."


def test_auth_error_masks_raw_escaped_credentials_and_urls_and_ignores_private_headers(tmp_path):
    secret = 'synthetic-"credential\\segment'
    escaped = json.dumps(secret)[1:-1]
    url = "https://provider.example/private?token=synthetic-query-secret"
    response = httpx.Response(401, headers={
        "x-request-id": "synthetic-auth-request", "set-cookie": "PRIVATE-COOKIE-DO-NOT-SAVE",
        "x-private-internal": "PRIVATE-HEADER-DO-NOT-SAVE",
    }, json={
        "error": {"code": "invalid_api_key", "message": f"Synthetic auth error: {secret}; {escaped}; {url}"},
        "private": {"headers": {"Authorization": f"Bearer {secret}"}, "body": "PRIVATE-RAW-BODY-DO-NOT-SAVE"},
    })
    exc, diagnostic, directory, requests = run_failure(tmp_path, response, api_key=secret)
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == f"Bearer {secret}"
    assert diagnostic["request_ids"] == {"x-request-id": "synthetic-auth-request"}
    combined = json.dumps(diagnostic, ensure_ascii=False) + artifact_text(directory) + str(exc)
    for forbidden in (secret, escaped, url, "synthetic-query-secret", "PRIVATE-COOKIE-DO-NOT-SAVE", "PRIVATE-HEADER-DO-NOT-SAVE", "PRIVATE-RAW-BODY-DO-NOT-SAVE"):
        assert forbidden not in combined
    assert "[REDACTED" in diagnostic["provider_error"]["message"]
    assert "Synthetic auth error" not in str(exc)


def test_provider_trimming_a_padded_key_does_not_expose_the_credential(tmp_path):
    secret = "  synthetic-padded-key  "
    response = httpx.Response(401, json={"error": {
        "message": "Synthetic invalid credential: " + secret.strip(),
    }})
    _, diagnostic, directory, _ = run_failure(tmp_path, response, api_key=secret)
    assert secret.strip() not in json.dumps(diagnostic) + artifact_text(directory)
    assert "[REDACTED]" in diagnostic["provider_error"]["message"]


@pytest.mark.parametrize("content,headers,body_format", [
    (b"<html>private synthetic gateway page</html>", {"content-type": "text/html"}, "non_json"),
    (b"", {}, "non_json"),
    (b'{"unrecognized":"private synthetic response"}', {"content-type": "application/json"}, "json"),
    (b'{"error":broken', {"content-type": "application/json"}, "invalid_json"),
])
def test_missing_provider_details_remain_unavailable_without_guessing_a_reason(tmp_path, content, headers, body_format):
    _, diagnostic, directory, requests = run_failure(tmp_path, httpx.Response(401, content=content, headers=headers))
    assert len(requests) == 1
    assert diagnostic["body_format"] == body_format
    assert diagnostic["diagnostic_status"] == "provider_detail_unavailable"
    assert diagnostic["provider_error"] == {}
    assert "private synthetic" not in artifact_text(directory)


def test_nested_non_scalar_provider_values_are_not_saved_as_messages(tmp_path):
    response = httpx.Response(400, json={"error": {
        "code": {"private": "NESTED-CODE-DO-NOT-SAVE"},
        "type": ["NESTED-TYPE-DO-NOT-SAVE"],
        "message": {"private": "NESTED-MESSAGE-DO-NOT-SAVE"},
        "param": ["NESTED-PARAM-DO-NOT-SAVE"],
        "details": [{"private": "NESTED-DETAIL-DO-NOT-SAVE"}],
    }})
    _, diagnostic, directory, _ = run_failure(tmp_path, response)
    assert diagnostic["provider_error"] == {}
    assert diagnostic["diagnostic_status"] == "provider_detail_unavailable"
    assert "DO-NOT-SAVE" not in artifact_text(directory)


def test_long_provider_message_is_bounded_and_explicitly_marked_as_truncated(tmp_path):
    message = "Synthetic message " + "x" * 5000 + "PRIVATE-LONG-TAIL"
    _, diagnostic, directory, _ = run_failure(tmp_path, httpx.Response(400, json={"error": {"message": message}}))
    saved_message = diagnostic["provider_error"]["message"]
    assert len(saved_message) <= 1100
    assert "PRIVATE-LONG-TAIL" not in artifact_text(directory)
    assert "truncat" in json.dumps(diagnostic).lower()


def test_oversized_error_body_is_skipped_without_parsing_private_nested_payload(tmp_path):
    content = json.dumps({"error": {"message": "OVERSIZED-PRIVATE-BODY" + "x" * (2 * 1024 * 1024)}}).encode()
    _, diagnostic, directory, _ = run_failure(tmp_path, httpx.Response(400, content=content, headers={"content-type": "application/json"}))
    assert diagnostic["body_format"] == "too_large"
    assert diagnostic["provider_error"] == {}
    assert diagnostic["diagnostic_status"] == "provider_detail_unavailable"
    assert "OVERSIZED-PRIVATE-BODY" not in artifact_text(directory)


def test_provider_control_characters_are_removed_from_stored_scalar_details(tmp_path):
    _, diagnostic, _, _ = run_failure(tmp_path, httpx.Response(400, json={"error": {
        "message": "Synthetic\x00 provider\x1b[31m error\r\ncontinued\tmessage.",
    }}))
    message = diagnostic["provider_error"]["message"]
    assert "Synthetic" in message and "continued" in message
    assert all(character.isprintable() for character in message)


@pytest.mark.parametrize("code_literal", [str(10 ** 500), "NaN", "Infinity"])
def test_extreme_numeric_provider_codes_are_ignored_without_crashing(tmp_path, code_literal):
    content = '{"error":{"code":' + code_literal + '}}'
    _, diagnostic, _, _ = run_failure(tmp_path, httpx.Response(
        400, content=content, headers={"content-type": "application/json"},
    ))
    assert "code" not in diagnostic["provider_error"]
    assert diagnostic["diagnostic_status"] == "provider_detail_unavailable"


def test_control_only_provider_message_is_ignored_after_normalization(tmp_path):
    _, diagnostic, _, _ = run_failure(tmp_path, httpx.Response(400, json={"error": {
        "message": "\x00\x1b",
    }}))
    assert diagnostic["provider_error"] == {}
    assert diagnostic["diagnostic_status"] == "provider_detail_unavailable"


def test_retry_success_keeps_each_failed_attempt_and_finishes_as_success(tmp_path):
    directory = tmp_path / "retry"
    trace = RunTrace(directory)
    received = []

    def endpoint(request):
        received.append(request)
        if len(received) == 1:
            return httpx.Response(429, headers={"x-request-id": "synthetic-throttle-request"}, json={"error": {
                "code": "rate_limit_exceeded", "message": "Synthetic retryable limit.",
            }})
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": '{"records":[]}'},
        }]})

    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid/v1", model="synthetic-model",
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None, trace=trace,
    )
    result = extract("Synthetic source only.", TASK, backend=backend, trace=trace)
    trace.finish(result.model_dump(mode="json"))
    failures = list((directory / "http").glob("*.error.json"))
    assert len(received) == result.run.request_attempts == 2
    assert len(failures) == 1
    failure = read_json(failures[0])
    assert failure["attempt"] == 1 and failure["status_code"] == 429
    assert failure["provider_error"]["code"] == "rate_limit_exceeded"
    assert failure["request_ids"]["x-request-id"] == "synthetic-throttle-request"
    manifest = read_json(directory / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert manifest["artifact_roles"][f"http/{failures[0].name}"]
    assert not (directory / "error.json").exists()


@pytest.mark.parametrize("inject_recursion", [False, True], ids=["native-decoder", "injected-recursion-error"])
def test_deeply_nested_error_json_does_not_replace_http_status_or_prevent_retry(tmp_path, monkeypatch, inject_recursion):
    directory = tmp_path / "deep-retry"
    trace = RunTrace(directory)
    nested = ("[" * 5000 + "]" * 5000).encode("ascii")
    assert len(nested) < 65536
    error_response = httpx.Response(
        429, content=nested, headers={"content-type": "application/json"},
    )
    success_response = httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": '{"records":[]}'},
    }]})
    decoded = {"error": 0, "success": 0}
    if inject_recursion:
        original_json = httpx.Response.json

        def response_json(response, *args, **kwargs):
            if response is error_response:
                decoded["error"] += 1
                raise RecursionError("Synthetic decoder depth limit")
            if response is success_response:
                decoded["success"] += 1
            return original_json(response, *args, **kwargs)

        monkeypatch.setattr(httpx.Response, "json", response_json)
    received = []

    def endpoint(request):
        received.append(request)
        return error_response if len(received) == 1 else success_response

    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid/v1", model="synthetic-model",
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None, trace=trace,
    )
    result = extract("Synthetic source only.", TASK, backend=backend, trace=trace)
    trace.finish(result.model_dump(mode="json"))

    assert len(received) == result.run.request_attempts == 2
    failures = list((directory / "http").glob("*.error.json"))
    assert len(failures) == 1
    failure = read_json(failures[0])
    assert failure["status_code"] == 429
    # This is valid JSON. Decoder depth limits vary by Python build/version,
    # so either decoding it or declining it is acceptable. Inject the exception
    # separately to cover the fallback deterministically on every runtime.
    assert failure["body_format"] in {"json", "invalid_json"}
    if inject_recursion:
        assert decoded == {"error": 1, "success": 1}
        assert failure["body_format"] == "invalid_json"
    assert failure["provider_error"] == {}
    assert failure["diagnostic_status"] == "provider_detail_unavailable"
    assert nested.decode("ascii") not in artifact_text(directory)
    assert read_json(directory / "manifest.json")["status"] == "succeeded"
    assert not (directory / "error.json").exists()


def test_exhausted_rate_limit_keeps_all_attempt_diagnostics(tmp_path):
    exc, diagnostic, directory, requests = run_failure(tmp_path, httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}))
    assert len(requests) == exc.attempts == diagnostic["attempt"] == 3
    failures = [read_json(path) for path in sorted((directory / "http").glob("*.error.json"))]
    assert [item["attempt"] for item in failures] == [1, 2, 3]
    assert all(item["status_code"] == 429 for item in failures)


def test_cli_error_links_to_http_diagnostics_without_echoing_provider_message(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    directory = tmp_path / "cli"
    source.write_text("Synthetic source only.", encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-cli-http-secret")

    def endpoint(request):
        return httpx.Response(401, headers={"x-request-id": "synthetic-cli-request"}, json={"error": {
            "code": "invalid_api_key", "message": "PRIVATE-PROVIDER-EXPLANATION synthetic-cli-http-secret",
        }})

    def backend(**kwargs):
        return OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None)

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", backend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--base-url", "https://example.invalid/v1", "--model", "synthetic-model", "--log-dir", str(directory),
    ])
    assert status == 1
    assert not output.exists()
    error = read_json(directory / "error.json")
    diagnostic = error["http_diagnostics"]
    assert diagnostic["status_code"] == 401
    assert diagnostic["provider_error"]["code"] == "invalid_api_key"
    assert diagnostic["request_ids"]["x-request-id"] == "synthetic-cli-request"
    assert read_json(directory / error["http_details"]) == diagnostic
    assert any(read_json(path) == diagnostic for path in (directory / "http").glob("*.error.json"))
    captured = capsys.readouterr()
    assert "PRIVATE-PROVIDER-EXPLANATION" not in captured.err
    assert "synthetic-cli-http-secret" not in captured.err + artifact_text(directory)

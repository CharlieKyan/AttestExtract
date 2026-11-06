"""Synthetic requests: TLS probing never sends prompts or retries uncertain POSTs."""

import json
import ssl

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError
from attestextract.models import ExtractionRequest
from attestextract.responses_backend import DeepSeekResponsesBackend
from attestextract.transport import tls_context, tls_fallback_allowed


def request():
    return ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "Synthetic paper."}])


def completion():
    return {"choices": [{"finish_reason": "stop", "message": {"content": '{"records": []}'}}]}


def ssl_failure(reason="SSLV3_ALERT_BAD_RECORD_MAC"):
    failure = ssl.SSLError(1, "synthetic private exception")
    failure.reason = reason
    error = httpx.ReadError("synthetic secret URL")
    error.__cause__ = failure
    return error


def intercept_clients(monkeypatch, handler):
    """Intercept at HTTPX construction, leaving built-in adapter transport unset."""
    actual_client = httpx.Client
    options = []

    def client(**kwargs):
        options.append(kwargs.copy())
        assert kwargs.get("transport") is None
        return actual_client(**{**kwargs, "transport": httpx.MockTransport(handler)})

    monkeypatch.setattr(httpx, "Client", client)
    return options


class UnreadableBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("The TLS probe must not read its body")


@pytest.mark.parametrize("status", [200, 401, 404, 405, 302])
def test_probe_receives_http_without_auth_body_redirect_or_cookie_leak(monkeypatch, status):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        if incoming.method == "GET":
            assert str(incoming.url) == "https://models.example/workspace/prefix/models"
            assert not incoming.content
            assert "authorization" not in incoming.headers
            assert "cookie" not in incoming.headers
            return httpx.Response(status, headers={"set-cookie": "probe=private", "location": "https://other.example/"},
                                  stream=UnreadableBody())
        assert incoming.headers["authorization"] == "Bearer synthetic-key"
        assert "cookie" not in incoming.headers
        return httpx.Response(200, json=completion())

    options = intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/workspace/prefix", "synthetic", "synthetic-key",
                                      tls_mode="auto_fallback")
    assert not calls  # Construction is offline.
    adapter.generate(request())
    adapter.generate(request())
    assert [item.method for item in calls] == ["GET", "POST", "POST"]
    assert adapter.probe_requests == 1
    assert adapter.effective_tls_mode == "auto"
    assert options[0]["timeout"] == 5
    assert all(item["follow_redirects"] is False for item in options)
    assert all(item.get("trust_env", True) is True for item in options)


def test_bad_mac_probes_tls12_before_one_post_and_caches_choice(monkeypatch, tmp_path):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        if calls == ["GET"]:
            raise ssl_failure()
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    options = intercept_clients(monkeypatch, handler)
    trace = RunTrace(tmp_path / "trace")
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback", trace=trace)
    assert adapter.generate(request()).attempts == 1
    adapter.generate(request())
    assert calls == ["GET", "GET", "POST", "POST"]
    assert adapter.probe_requests == 2
    assert adapter.effective_tls_mode == "1.2"
    for option in options[1:]:
        context = option["verify"]
        assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_2
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
    diagnostic = json.loads((tmp_path / "trace/http/chunk-0001.probe-1.transport.json").read_text())
    assert diagnostic["model_post_attempts"] == 0
    assert diagnostic["request_outcome"] == "not_sent"
    assert "synthetic private" not in json.dumps(diagnostic)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("private"), httpx.ConnectError("private"),
                                      httpx.ProxyError("private"), ssl_failure("OTHER_TLS_ERROR")])
def test_unqualified_probe_failure_stops_without_a_post(monkeypatch, failure):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        raise failure

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback", max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert calls == ["GET"]
    assert caught.value.attempts == 0
    assert caught.value.http_diagnostics["request_outcome"] == "not_sent"
    assert not caught.value.retryable


def test_probe_timeout_reports_probe_limits_instead_of_model_limits(monkeypatch):
    events = []
    def handler(incoming):
        assert incoming.extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
        raise httpx.ReadTimeout("synthetic")
    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback",
                                      read_timeout=240, trace=RunTrace(None, on_event=events.append))
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert caught.value.http_diagnostics["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
    event = next(item for item in events if item["event"] == "transport_failed")
    assert event["read_timeout"] == 5
    assert event["model_post_attempts"] == 0


def test_certificate_failure_wins_over_bad_mac():
    error = ssl_failure()
    error.__cause__.__cause__ = ssl.SSLCertVerificationError(1, "private certificate")
    assert not tls_fallback_allowed(error)
    assert tls_fallback_allowed(ssl_failure("DECRYPTION_FAILED_OR_BAD_RECORD_MAC"))
    assert not tls_fallback_allowed(httpx.ReadError("SSLV3_ALERT_BAD_RECORD_MAC"))


@pytest.mark.parametrize("certificate", [False, True])
def test_failed_probe_is_bounded_and_certificate_never_falls_back(monkeypatch, certificate):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        error = ssl_failure()
        if certificate:
            error.__cause__.__cause__ = ssl.SSLCertVerificationError(1, "synthetic private certificate")
        raise error

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback", max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert calls == ["GET"] * (1 if certificate else 2)
    assert caught.value.attempts == 0
    assert caught.value.http_diagnostics["probe_requests"] == len(calls)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError,
                                      httpx.WriteError, httpx.RemoteProtocolError])
def test_uncertain_post_is_not_retried_even_with_three_attempts(failure):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        raise failure("synthetic secret")

    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback",
                                      transport=httpx.MockTransport(handler), max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert len(calls) == 1
    assert caught.value.attempts == 1
    assert "not automatically retried" in str(caught.value)
    details = caught.value.http_diagnostics
    assert details["request_outcome"] == "unknown"
    assert details["probe_requests"] == 0
    assert details["certificate_verification"] is None
    assert details["effective_tls_mode"] is None
    assert not details["automatic_retry_allowed"]
    if failure is httpx.ReadTimeout:
        assert details["phase"] == "read"
        assert details["timeout"]["read"] == 180


def test_failed_post_invalidates_cached_tls_without_retry(monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        if calls == ["GET", "POST"]:
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    with pytest.raises(BackendError):
        adapter.generate(request())
    adapter.generate(request())
    assert calls == ["GET", "POST", "GET", "POST"]


def test_proxy_or_endpoint_change_invalidates_probe_cache(monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    adapter.generate(request())
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    adapter.generate(request())
    adapter.base_url = "https://models.example/workspace/v1"
    adapter.generate(request())
    assert calls == ["GET", "POST"] * 3


def test_ca_file_change_invalidates_selection_and_keeps_values_private(monkeypatch, tmp_path):
    # Context creation is isolated here; certificate validity is checked in the TLS1.2 test.
    monkeypatch.setattr("attestextract.backends.tls_context", lambda mode: True)
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("synthetic certificate")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    calls = []
    actual_client = httpx.Client

    def handler(incoming):
        calls.append(incoming.method)
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    def client(**kwargs):
        # A custom MockTransport with trust_env=False avoids loading our synthetic CA.
        return actual_client(**{**kwargs, "transport": httpx.MockTransport(handler), "trust_env": False})

    monkeypatch.setattr(httpx, "Client", client)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    adapter.generate(request())
    ca.write_text("changed synthetic certificate contents")
    adapter.generate(request())
    assert calls == ["GET", "POST"] * 2


def test_plain_http_skips_tls_probes(monkeypatch):
    calls = []
    intercept_clients(monkeypatch, lambda incoming: calls.append(incoming.method) or httpx.Response(200, json=completion()))
    adapter = OpenAICompatibleBackend("http://localhost/v1", "synthetic", tls_mode="auto_fallback")
    adapter.generate(request())
    assert calls == ["POST"]
    assert adapter.effective_tls_mode == "not_applicable"


@pytest.mark.parametrize("bypass", ["auto", "1.2", "http", "custom_transport"])
def test_return_from_bypass_reprobes_instead_of_reusing_old_tls_selection(monkeypatch, bypass):
    calls = []
    actual_client = httpx.Client

    def handler(incoming):
        calls.append(incoming.method)
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    def client(**kwargs):
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(handler)
        return actual_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    adapter.generate(request())
    if bypass in {"auto", "1.2"}:
        adapter.tls_mode = bypass
    elif bypass == "http":
        adapter.base_url = "http://localhost/v1"
    else:
        adapter._transport = httpx.MockTransport(handler)
    adapter.generate(request())
    adapter.tls_mode = "auto_fallback"
    adapter.base_url = "https://models.example/v1"
    adapter._transport = None
    adapter.generate(request())
    assert calls == ["GET", "POST", "POST", "GET", "POST"]
    assert adapter.probe_requests == 2


@pytest.mark.parametrize("options, expected", [
    ({}, {"connect": 15, "read": 180, "write": 30, "pool": 15}),
    ({"timeout": 60}, {"connect": 60, "read": 60, "write": 60, "pool": 60}),
    ({"timeout": 20, "read_timeout": 300}, {"connect": 20, "read": 300, "write": 20, "pool": 20}),
    ({"read_timeout": 600}, {"connect": 15, "read": 600, "write": 30, "pool": 15}),
])
def test_phase_timeouts_reach_http_requests(options, expected):
    def handler(incoming):
        assert incoming.extensions["timeout"] == expected
        return httpx.Response(200, json=completion())

    OpenAICompatibleBackend("https://models.example/v1", "synthetic",
                            transport=httpx.MockTransport(handler), **options).generate(request())


@pytest.mark.parametrize("name", ["timeout", "read_timeout"])
@pytest.mark.parametrize("value", [0, -1, 601, True, "180", float("inf"), float("nan")])
def test_invalid_timeout_is_rejected_offline(name, value):
    with pytest.raises(ConfigurationError):
        OpenAICompatibleBackend("https://models.example/v1", "synthetic", **{name: value})


def test_responses_adapter_shares_timeout_and_no_read_retry():
    calls = []

    def handler(incoming):
        calls.append(incoming)
        assert incoming.url.path == "/responses"
        assert incoming.extensions["timeout"]["read"] == 240
        raise httpx.ReadTimeout("synthetic")

    adapter = DeepSeekResponsesBackend("https://api.deepseek.com", "deepseek-flash", read_timeout=240,
                                      max_attempts=3, transport=httpx.MockTransport(handler), structured_output="off")
    with pytest.raises(BackendError):
        adapter.generate(request())
    assert len(calls) == 1


def test_unresolved_tls_policy_cannot_silently_force_tls12():
    with pytest.raises(ConfigurationError):
        tls_context("auto_fallback")

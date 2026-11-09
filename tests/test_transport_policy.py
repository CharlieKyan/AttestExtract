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
from attestextract.transport import tls_context, tls_fallback_allowed, tls_fallback_reason


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
@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_probe_receives_http_without_auth_body_redirect_or_cookie_leak(tls_mode, monkeypatch, status):
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
                                      tls_mode=tls_mode)
    assert not calls  # Construction is offline.
    adapter.generate(request())
    adapter.generate(request())
    assert [item.method for item in calls] == ["GET", "POST", "POST"]
    assert adapter.probe_requests == 1
    assert adapter.effective_tls_mode == ("1.2" if tls_mode == "tls12_fallback" else "auto")
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
@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_unqualified_probe_failure_stops_without_a_post(tls_mode, monkeypatch, failure):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        raise failure

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode, max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert calls == ["GET"]
    assert caught.value.attempts == 0
    assert caught.value.http_diagnostics["request_outcome"] == "not_sent"
    assert not caught.value.retryable


@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_probe_timeout_reports_probe_limits_instead_of_model_limits(tls_mode, monkeypatch):
    events = []
    def handler(incoming):
        assert incoming.extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
        raise httpx.ReadTimeout("synthetic")
    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode,
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
@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_failed_probe_is_bounded_and_certificate_never_falls_back(tls_mode, monkeypatch, certificate):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        error = ssl_failure()
        if certificate:
            error.__cause__.__cause__ = ssl.SSLCertVerificationError(1, "synthetic private certificate")
        raise error

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode, max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(request())
    assert calls == ["GET"] * (1 if certificate else 2)
    assert caught.value.attempts == 0
    assert caught.value.http_diagnostics["probe_requests"] == len(calls)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError,
                                      httpx.WriteError, httpx.RemoteProtocolError])
@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_uncertain_post_is_not_retried_even_with_three_attempts(tls_mode, failure):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        raise failure("synthetic secret")

    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode,
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


@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_failed_post_invalidates_cached_tls_without_retry(tls_mode, monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        if calls == ["GET", "POST"]:
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode)
    with pytest.raises(BackendError):
        adapter.generate(request())
    adapter.generate(request())
    assert calls == ["GET", "POST", "GET", "POST"]


@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_proxy_or_endpoint_change_invalidates_probe_cache(tls_mode, monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode)
    adapter.generate(request())
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    adapter.generate(request())
    adapter.base_url = "https://models.example/workspace/v1"
    adapter.generate(request())
    assert calls == ["GET", "POST"] * 3


@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_ca_file_change_invalidates_selection_and_keeps_values_private(tls_mode, monkeypatch, tmp_path):
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
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode)
    adapter.generate(request())
    ca.write_text("changed synthetic certificate contents")
    adapter.generate(request())
    assert calls == ["GET", "POST"] * 2


@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_plain_http_skips_tls_probes(tls_mode, monkeypatch):
    calls = []
    intercept_clients(monkeypatch, lambda incoming: calls.append(incoming.method) or httpx.Response(200, json=completion()))
    adapter = OpenAICompatibleBackend("http://localhost/v1", "synthetic", tls_mode=tls_mode)
    adapter.generate(request())
    assert calls == ["POST"]
    assert adapter.effective_tls_mode == "not_applicable"


@pytest.mark.parametrize("bypass", ["auto", "1.2", "http", "custom_transport"])
@pytest.mark.parametrize("tls_mode", ["auto_fallback", "tls12_fallback"])
def test_return_from_bypass_reprobes_instead_of_reusing_old_tls_selection(tls_mode, monkeypatch, bypass):
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
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode=tls_mode)
    adapter.generate(request())
    if bypass in {"auto", "1.2"}:
        adapter.tls_mode = bypass
    elif bypass == "http":
        adapter.base_url = "http://localhost/v1"
    else:
        adapter._transport = httpx.MockTransport(handler)
    adapter.generate(request())
    adapter.tls_mode = tls_mode
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
    with pytest.raises(ConfigurationError):
        tls_context("tls12_fallback")


def test_default_prefers_tls12_without_reporting_a_fallback(monkeypatch):
    calls, events = [], []

    def handler(incoming):
        calls.append(incoming.method)
        return httpx.Response(404) if incoming.method == "GET" else httpx.Response(200, json=completion())

    options = intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic",
                                      trace=RunTrace(None, on_event=events.append))
    assert adapter.tls_mode == "tls12_fallback" and adapter.max_attempts == 1
    adapter.generate(request())
    adapter.generate(request())
    assert calls == ["GET", "POST", "POST"]
    for option in options:
        context = option["verify"]
        assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_2
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    selected = next(item for item in events if item["event"] == "tls_selected")
    assert selected["effective_tls_mode"] == "1.2"
    assert selected["fallback"] is False and selected["fallback_reason"] is None


@pytest.mark.parametrize("reason", ["TLSV1_ALERT_PROTOCOL_VERSION", "UNSUPPORTED_PROTOCOL",
                                    "SSLV3_ALERT_HANDSHAKE_FAILURE", "SSLV3_ALERT_BAD_RECORD_MAC",
                                    "DECRYPTION_FAILED_OR_BAD_RECORD_MAC"])
def test_tls12_compatibility_failure_uses_auto_once_then_reuses_it(monkeypatch, tmp_path, reason):
    calls, events = [], []

    def handler(incoming):
        calls.append(incoming.method)
        if len(calls) == 1:
            raise ssl_failure(reason)
        return httpx.Response(401) if incoming.method == "GET" else httpx.Response(200, json=completion())

    options = intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic",
                                      trace=RunTrace(tmp_path / "trace", on_event=events.append))
    assert adapter.generate(request()).attempts == 1
    adapter.generate(request())
    assert calls == ["GET", "GET", "POST", "POST"]
    assert options[0]["verify"].maximum_version == ssl.TLSVersion.TLSv1_2
    assert all(option["verify"] is True for option in options[1:])
    assert adapter.probe_requests == 2 and adapter.effective_tls_mode == "auto"
    selected = next(item for item in events if item["event"] == "tls_selected")
    assert selected["fallback"] is True and selected["fallback_reason"] == reason
    diagnostic = json.loads((tmp_path / "trace/http/chunk-0001.probe-1.transport.json").read_text())
    assert diagnostic["model_post_attempts"] == 0 and diagnostic["request_outcome"] == "not_sent"
    assert diagnostic["causes"][1]["reason"] == reason
    assert "synthetic private" not in json.dumps(events)


@pytest.mark.parametrize("reason", ["TLSV1_ALERT_PROTOCOL_VERSION", "UNSUPPORTED_PROTOCOL",
                                    "SSLV3_ALERT_HANDSHAKE_FAILURE"])
def test_old_auto_fallback_does_not_expand_its_mac_only_allowlist(monkeypatch, reason):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        raise ssl_failure(reason)

    intercept_clients(monkeypatch, handler)
    with pytest.raises(BackendError):
        OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback").generate(request())
    assert calls == ["GET"]


@pytest.mark.parametrize("outer", [httpx.ProxyError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_unrelated_transport_failure_never_qualifies_from_a_nested_tls_reason(outer):
    error = outer("synthetic")
    error.__cause__ = ssl_failure("TLSV1_ALERT_PROTOCOL_VERSION")
    assert tls_fallback_reason(error, policy="tls12_fallback") is None


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("synthetic"), httpx.RemoteProtocolError("synthetic"),
                                      ssl_failure()])
def test_tls12_post_failure_does_not_reprobe_or_resend(monkeypatch, failure):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        if incoming.method == "GET":
            return httpx.Response(401)
        raise failure

    intercept_clients(monkeypatch, handler)
    with pytest.raises(BackendError) as caught:
        OpenAICompatibleBackend("https://models.example/v1", "synthetic", max_attempts=3).generate(request())
    assert calls == ["GET", "POST"]
    assert caught.value.attempts == 1 and caught.value.http_diagnostics["request_outcome"] == "unknown"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_default_attempt_count_does_not_resend_retryable_status(status):
    calls = []
    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(status, json={"error": {"message": "synthetic"}})
    with pytest.raises(BackendError) as caught:
        OpenAICompatibleBackend("https://models.example/v1", "synthetic",
                                transport=httpx.MockTransport(handler)).generate(request())
    assert caught.value.attempts == 1 and len(calls) == 1

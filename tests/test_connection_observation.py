"""Synthetic HTTPX trace events; no live service or TLS negotiation claims."""

import json
from types import SimpleNamespace

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend
from attestextract.connection_observation import ConnectionObservation
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError
from attestextract.models import ExtractionRequest
from attestextract.responses_backend import DeepSeekResponsesBackend


class SSLObject:
    def __init__(self, version="TLSv1.3"):
        self._version = version

    def version(self):
        return self._version


class NetworkStream:
    def __init__(self, version="TLSv1.3"):
        self.ssl = SSLObject(version)

    def get_extra_info(self, key):
        assert key == "ssl_object"
        return self.ssl


def extraction_request():
    return ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "Synthetic source."}])


def completion():
    return {"choices": [{"finish_reason": "stop", "message": {"content": '{"records": []}'}}]}


def origin_trace(incoming, *, version="TLSv1.3"):
    callback = incoming.extensions["trace"]
    callback("connection.connect_tcp.started", {"host": "private-host", "port": 443})
    callback("connection.connect_tcp.complete", {"return_value": object()})
    callback("connection.start_tls.started", {"server_hostname": incoming.url.host})
    callback("connection.start_tls.complete", {"return_value": NetworkStream(version)})
    callback("http11.send_request_headers.started", {"request": incoming})
    callback("http11.send_request_headers.complete", {"private": "synthetic-secret"})
    callback("http11.send_request_body.started", {"request": incoming})
    callback("http11.send_request_body.complete", {})
    callback("http11.receive_response_headers.started", {"request": incoming})
    return callback


def intercept_clients(monkeypatch, handler):
    original = httpx.Client
    options = []

    def client(**kwargs):
        options.append(kwargs.copy())
        assert kwargs.get("transport") is None
        return original(**{**kwargs, "transport": httpx.MockTransport(handler)})

    monkeypatch.setattr(httpx, "Client", client)
    return options


def test_actual_post_protocols_and_phase_durations_do_not_come_from_tls_policy(monkeypatch, tmp_path):
    events = []

    def handler(incoming):
        callback = origin_trace(incoming, version="TLSv1.2")
        callback("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", 200, b"OK", [])})
        return httpx.Response(200, json=completion(), extensions={
            "http_version": b"HTTP/1.1", "network_stream": NetworkStream("TLSv1.2")})

    options = intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto",
                                      trace=RunTrace(tmp_path / "trace", on_event=events.append))
    adapter.generate(extraction_request())
    observed = adapter.last_connection_observations[0]
    assert observed["request_kind"] == "model_post"
    assert observed["effective_tls_mode"] == "auto"
    assert observed["actual_tls_version"] == "TLSv1.2"
    assert observed["actual_http_version"] == "HTTP/1.1"
    assert observed["observation_status"] == "observed"
    assert observed["body_complete"] and observed["headers_received"]
    assert observed["phase_seconds"]["tls_handshake"] >= 0
    assert observed["phase_seconds"]["response_body"] is None  # Missing trace is not invented.
    assert options[0]["transport"] is None and options[0]["verify"] is True
    event = next(e for e in events if e["event"] == "connection_observed")
    assert event["actual_tls_version"] == "TLSv1.2"
    saved = json.loads((tmp_path / "trace/http/chunk-0001.attempt-1.connection.json").read_text())
    assert saved == observed


def test_probe_protocol_never_substitutes_for_model_protocol(monkeypatch):
    seen = []

    def handler(incoming):
        seen.append(incoming.method)
        version = "TLSv1.2" if incoming.method == "GET" else "TLSv1.3"
        callback = origin_trace(incoming, version=version)
        callback("http11.receive_response_headers.complete", {})
        return httpx.Response(401 if incoming.method == "GET" else 200,
                              json={} if incoming.method == "GET" else completion(), extensions={
                                  "http_version": b"HTTP/1.1", "network_stream": NetworkStream(version)})

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    adapter.generate(extraction_request())
    probe, post = adapter.last_connection_observations
    assert seen == ["GET", "POST"]
    assert probe["request_kind"] == "tls_probe" and probe["actual_tls_version"] == "TLSv1.2"
    assert not probe["body_complete"]
    assert post["request_kind"] == "model_post" and post["actual_tls_version"] == "TLSv1.3"
    adapter.generate(extraction_request())
    assert len(adapter.last_connection_observations) == 1  # No stale probe observation on cache hit.
    assert adapter.last_connection_observations[0]["request_kind"] == "model_post"


def test_failure_before_headers_preserves_observed_tls_without_retry(monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        callback = origin_trace(incoming, version="TLSv1.3")
        callback("http11.receive_response_headers.failed", {"exception": httpx.RemoteProtocolError("private")})
        raise httpx.RemoteProtocolError("private")

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto", max_attempts=3)
    with pytest.raises(BackendError) as caught:
        adapter.generate(extraction_request())
    observed = caught.value.http_diagnostics["connection_observation"]
    assert len(calls) == 1
    assert observed["request_kind"] == "model_post"
    assert observed["actual_tls_version"] == "TLSv1.3"
    assert observed["actual_http_version"] == "unknown"
    assert observed["failure_phase"] == "response_headers"
    assert observed["phase_seconds"]["response_headers"] >= 0
    assert not observed["headers_received"] and not observed["body_complete"]
    assert observed["request_status"] == "transport_failure"


def test_failed_probe_observation_is_not_a_model_post(monkeypatch):
    calls = []

    def handler(incoming):
        calls.append(incoming.method)
        origin_trace(incoming)
        raise httpx.ReadTimeout("private")

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto_fallback")
    with pytest.raises(BackendError) as caught:
        adapter.generate(extraction_request())
    assert calls == ["GET"] and caught.value.attempts == 0
    observed = caught.value.http_diagnostics["connection_observation"]
    assert observed["request_kind"] == "tls_probe"
    assert observed["actual_tls_version"] == "TLSv1.3"
    assert observed["request_status"] == "transport_failure"


def test_interruption_keeps_available_connection_observation(monkeypatch):
    def handler(incoming):
        origin_trace(incoming, version="TLSv1.2")
        raise KeyboardInterrupt()

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto")
    with pytest.raises(KeyboardInterrupt):
        adapter.generate(extraction_request())
    observed = adapter.last_connection_observations[0]
    assert observed["request_status"] == "interrupted"
    assert observed["actual_tls_version"] == "TLSv1.2"


def test_decoding_failure_keeps_headers_observation(monkeypatch):
    intercept_clients(monkeypatch, lambda _: httpx.Response(200, stream=httpx.ByteStream(b"invalid gzip"),
        headers={"content-encoding": "gzip"}, extensions={"http_version": b"HTTP/1.1"}))
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto")
    with pytest.raises(BackendError):
        adapter.generate(extraction_request())
    observed = adapter.last_connection_observations[0]
    assert observed["request_status"] == "decode_failure"
    assert observed["headers_received"] and observed["actual_http_version"] == "HTTP/1.1"


def test_response_hook_preserves_http_and_tls_when_body_fails(monkeypatch):
    class BrokenBody(httpx.SyncByteStream):
        def __init__(self, incoming):
            self.incoming = incoming

        def __iter__(self):
            callback = self.incoming.extensions["trace"]
            callback("http11.receive_response_body.started", {"request": self.incoming})
            yield b"{"
            callback("http11.receive_response_body.failed", {"exception": httpx.ReadError("private")})
            raise httpx.ReadError("private")

    def handler(incoming):
        origin_trace(incoming)
        return httpx.Response(200, stream=BrokenBody(incoming), extensions={
            "http_version": b"HTTP/1.1", "network_stream": NetworkStream()})

    intercept_clients(monkeypatch, handler)
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="auto")
    with pytest.raises(BackendError) as caught:
        adapter.generate(extraction_request())
    observed = caught.value.http_diagnostics["connection_observation"]
    assert observed["headers_received"] and not observed["body_complete"]
    assert observed["status_code"] == 200
    assert observed["actual_http_version"] == "HTTP/1.1"
    assert observed["actual_tls_version"] == "TLSv1.3"
    assert observed["failure_phase"] == "response_body"


def test_missing_and_custom_transport_extensions_stay_unknown():
    def handler(incoming):
        origin_trace(incoming)
        return httpx.Response(200, json=completion(), extensions={
            "http_version": b"HTTP/1.1", "network_stream": NetworkStream()})

    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="1.2",
                                      transport=httpx.MockTransport(handler))
    adapter.generate(extraction_request())
    observed = adapter.last_connection_observations[0]
    assert observed["custom_transport"]
    assert observed["actual_tls_version"] == observed["actual_http_version"] == "unknown"
    assert observed["observation_status"] == "unavailable"
    assert all(value is None for value in observed["phase_seconds"].values())
    missing = ConnectionObservation("https://models.example/v1", method="POST", custom_transport=False)
    response = httpx.Response(200, json=completion(), request=httpx.Request("POST", "https://models.example/v1"))
    missing.response(response)
    assert missing.snapshot(request_status="http_response")["actual_http_version"] == "unknown"
    assert missing.snapshot(request_status="http_response")["actual_tls_version"] == "unknown"


def test_proxy_connect_and_proxy_tls_do_not_count_as_origin_response():
    observation = ConnectionObservation("https://models.example/v1", method="POST", custom_transport=False)
    observation.trace("connection.start_tls.started", {"server_hostname": "proxy.example"})
    observation.trace("connection.start_tls.complete", {"return_value": NetworkStream("TLSv1.3")})
    connect = SimpleNamespace(method=b"CONNECT", url=SimpleNamespace(host=b"proxy.example"))
    observation.trace("http11.send_request_headers.started", {"request": connect})
    observation.trace("http11.send_request_headers.complete", {})
    observation.trace("http11.receive_response_headers.started", {"request": connect})
    observation.trace("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", 200, b"OK", [])})
    before = observation.snapshot(request_status="transport_failure")
    assert before["actual_tls_version"] == "unknown"
    assert not before["headers_received"]
    assert all(value is None for value in before["phase_seconds"].values())
    observation.trace("proxy.start_tls.started", {"server_hostname": "models.example"})
    observation.trace("proxy.start_tls.complete", {"return_value": NetworkStream("TLSv1.2")})
    assert observation.snapshot(request_status="transport_failure")["actual_tls_version"] == "TLSv1.2"


def test_whitelisted_durations_and_no_sensitive_trace_values_leak():
    tick = [0.0]
    observation = ConnectionObservation("https://private-host.example/v1", method="POST", custom_transport=False,
                                         clock=lambda: tick[0])
    incoming = httpx.Request("POST", "https://private-host.example/v1", headers={"Authorization": "Bearer private-key"},
                            json={"private-field": "private-paper"})
    observation.trace("http11.send_request_headers.started", {"request": incoming, "private-key": "private-value"})
    tick[0] = 0.25
    observation.trace("http11.send_request_headers.complete", {"return_value": "private-secret"})
    observation.trace("http11.private-secret.started", {"private-key": "private-value"})
    observation.trace("http11.private-secret.failed", {"exception": RuntimeError("private-value")})
    response = httpx.Response(200, request=incoming, extensions={
        "network_stream": NetworkStream("private-version"), "http_version": b"private-version"})
    observation.response(response)
    snapshot = observation.snapshot(request_status="http_response")
    assert snapshot["phase_seconds"]["request_headers"] == 0.25
    assert snapshot["actual_tls_version"] == snapshot["actual_http_version"] == "unknown"
    assert "private" not in json.dumps(snapshot)


def test_broken_optional_ssl_extensions_do_not_change_result(monkeypatch):
    class UnsupportedStream:
        def get_extra_info(self, key):
            raise RuntimeError("private")

    intercept_clients(monkeypatch, lambda _: httpx.Response(200, json=completion(), extensions={
        "network_stream": UnsupportedStream(), "http_version": b"HTTP/1.1"}))
    adapter = OpenAICompatibleBackend("https://models.example/v1", "synthetic", tls_mode="1.2")
    assert adapter.generate(extraction_request()).text == '{"records": []}'
    assert adapter.last_connection_observations[0]["actual_tls_version"] == "unknown"


def test_responses_backend_uses_same_observer_without_payload_changes(monkeypatch):
    def handler(incoming):
        assert incoming.url.path == "/responses"
        assert set(json.loads(incoming.content)) == {"model", "input"}
        origin_trace(incoming, version="TLSv1.2")
        return httpx.Response(200, json={"object": "response", "status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": '{"records": []}'}]}
        ]}, extensions={"network_stream": NetworkStream("TLSv1.2"), "http_version": b"HTTP/1.1"})

    intercept_clients(monkeypatch, handler)
    adapter = DeepSeekResponsesBackend("https://api.deepseek.com", "deepseek-flash", structured_output="off",
                                      tls_mode="auto")
    assert adapter.generate(extraction_request()).text == '{"records": []}'
    assert adapter.last_connection_observations[0]["actual_tls_version"] == "TLSv1.2"

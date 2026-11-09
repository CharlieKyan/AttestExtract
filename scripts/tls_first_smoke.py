"""Installed acceptance using real TLS on loopback, never a model provider.

The bundled certificate and key are public synthetic test fixtures. This runner
does not load .env or call external hosts. Temporary CA/proxy environment changes
are restored when each case exits, including on failure.
"""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import sys
import threading

import attestextract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError
from attestextract.models import ExtractionRequest


@contextmanager
def local_environment(certificate=None):
    names = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    saved = {key: value for key, value in os.environ.items() if key.upper() in names}
    try:
        for key in list(os.environ):
            if key.upper() in names:
                del os.environ[key]
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"
        if certificate is not None:
            os.environ["SSL_CERT_FILE"] = str(certificate)
        yield
    finally:
        for key in list(os.environ):
            if key.upper() in names:
                del os.environ[key]
        os.environ.update(saved)


@contextmanager
def local_server(certificate_dir, version):
    """A real HTTPS peer restricted to exactly one TLS version."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.load_cert_chain(certificate_dir / "localhost-cert.pem", certificate_dir / "localhost-key.pem")
    requests, handshake_errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body):
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path, "tls": self.connection.version(),
                             "has_auth": "Authorization" in self.headers,
                             "body_length": int(self.headers.get("Content-Length", "0"))})
            self.respond(401, {"error": "Synthetic unauthenticated probe"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"method": "POST", "path": self.path, "tls": self.connection.version(),
                             "auth": self.headers.get("Authorization"), "body": body})
            self.respond(200, {"choices": [{"finish_reason": "stop", "message": {"content": '{"records":[]}'}}]})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(5)
            try:
                return context.wrap_socket(sock, server_side=True), address
            except ssl.SSLError as error:
                handshake_errors.append(error.reason)
                sock.close()
                raise

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}/v1", requests, handshake_errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=6)
        assert not thread.is_alive(), "Loopback server did not stop"


def success_case(certificate_dir, version):
    expected_tls = "TLSv1.2" if version == ssl.TLSVersion.TLSv1_2 else "TLSv1.3"
    expected_probes = 1 if version == ssl.TLSVersion.TLSv1_2 else 2
    events = []
    with local_environment(certificate_dir / "localhost-cert.pem"):
        with local_server(certificate_dir, version) as (url, calls, handshake_errors):
            adapter = OpenAICompatibleBackend(url, "synthetic", "synthetic-loopback-key",
                                              trace=RunTrace(None, on_event=events.append))
            assert adapter.tls_mode == "tls12_fallback" and adapter.max_attempts == 1
            for index in (1, 2):
                request = ExtractionRequest(chunk_id=f"chunk-{index:04}",
                    messages=[{"role": "user", "content": "Synthetic local TLS acceptance."}])
                response = adapter.generate(request)
                assert response.text == '{"records":[]}' and response.attempts == 1
                if index == 1:
                    first_observations = list(adapter.last_connection_observations)
            assert [call["method"] for call in calls] == ["GET", "POST", "POST"]
            assert calls[0]["path"] == "/v1/models"
            assert not calls[0]["has_auth"] and calls[0]["body_length"] == 0
            assert all(call["path"] == "/v1/chat/completions" for call in calls[1:])
            assert all(call["auth"] == "Bearer synthetic-loopback-key" for call in calls[1:])
            assert all(call["tls"] == expected_tls for call in calls)
            assert adapter.probe_requests == expected_probes and adapter.last_probe_requests == 0
            selected = [event for event in events if event["event"] == "tls_selected"]
            assert len(selected) == 1 and selected[0]["fallback"] is (expected_probes == 2)
            observed = [item for item in first_observations if item["request_kind"] == "model_post"]
            assert len(observed) == 1 and observed[0]["actual_tls_version"] == expected_tls
            assert len(handshake_errors) == expected_probes - 1
    return {"case": expected_tls + "-only", "passed": True, "actual_tls_version": expected_tls,
            "probe_attempts": expected_probes, "http_probes_received": 1, "model_posts": 2,
            "selected_mode": adapter.effective_tls_mode, "cached_second_request": True,
            "failed_handshakes": len(handshake_errors)}


def certificate_case(certificate_dir):
    with local_environment():
        with local_server(certificate_dir, ssl.TLSVersion.TLSv1_2) as (url, calls, _):
            adapter = OpenAICompatibleBackend(url, "synthetic", "synthetic-loopback-key")
            request = ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "Synthetic."}])
            try:
                adapter.generate(request)
            except BackendError as error:
                diagnostic = error.http_diagnostics
                assert error.attempts == 0 and diagnostic["model_post_attempts"] == 0
                assert diagnostic["request_outcome"] == "not_sent"
                assert any(item.get("type") == "SSLCertVerificationError" or
                           item.get("reason") == "CERTIFICATE_VERIFY_FAILED" for item in diagnostic["causes"])
            else:
                raise AssertionError("Untrusted loopback certificate was accepted")
            assert calls == [] and adapter.probe_requests == 1
    return {"case": "untrusted-certificate", "passed": True, "probe_attempts": 1,
            "http_probes_received": 0, "model_posts": 0, "fallback": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--certificate-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "tests/fixtures/tls_local")
    args = parser.parse_args(argv)
    if not ssl.HAS_TLSv1_3:
        raise RuntimeError("This acceptance requires a runtime with TLS 1.3 support")
    original_environment = dict(os.environ)
    cases = [success_case(args.certificate_dir, ssl.TLSVersion.TLSv1_2),
             success_case(args.certificate_dir, ssl.TLSVersion.TLSv1_3),
             certificate_case(args.certificate_dir)]
    assert dict(os.environ) == original_environment
    assert not any(name == "langextract" or name.startswith("langextract.") for name in sys.modules)
    summary = {"passed": True, "package_version": attestextract.__version__, "module_path": attestextract.__file__,
               "real_endpoint_calls": 0, "loopback_only": True, "local_model_posts": sum(c["model_posts"] for c in cases),
               "probe_attempts": sum(c["probe_attempts"] for c in cases), "environment_restored": True,
               "cases": cases, "scope": "Real local TLS handshakes and synthetic responses; no provider reliability claim."}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real loopback TLS, with public synthetic certificates and no provider calls."""

import os
from pathlib import Path
import ssl
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from tls_first_smoke import certificate_case, local_environment, success_case


CERTIFICATES = Path(__file__).parent / "fixtures/tls_local"


@pytest.mark.parametrize("version,probes", [(ssl.TLSVersion.TLSv1_2, 1), (ssl.TLSVersion.TLSv1_3, 2)])
def test_real_tls_selection_and_cached_second_generation(version, probes):
    assert ssl.HAS_TLSv1_3, "The supported test runtimes must implement TLS 1.3"
    environment = dict(os.environ)
    result = success_case(CERTIFICATES, version)
    assert result["passed"] and result["probe_attempts"] == probes
    assert result["model_posts"] == 2 and result["cached_second_request"]
    assert dict(os.environ) == environment


def test_real_certificate_rejection_never_falls_back_or_posts():
    environment = dict(os.environ)
    result = certificate_case(CERTIFICATES)
    assert result["model_posts"] == 0 and result["probe_attempts"] == 1 and not result["fallback"]
    assert dict(os.environ) == environment


def test_local_environment_restores_on_failure(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-proxy.invalid:1234")
    monkeypatch.setenv("SSL_CERT_FILE", "synthetic-path-not-read.pem")
    original = dict(os.environ)
    with pytest.raises(RuntimeError):
        with local_environment(CERTIFICATES / "localhost-cert.pem"):
            assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
            assert "HTTPS_PROXY" not in os.environ
            raise RuntimeError("synthetic interruption")
    assert dict(os.environ) == original

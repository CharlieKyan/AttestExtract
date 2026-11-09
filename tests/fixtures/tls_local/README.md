# Public synthetic loopback certificate

`localhost-cert.pem` and `localhost-key.pem` are a deliberately public test pair.
They are not provider credentials, deployment secrets or a production trust root.
Never use this key to protect real data. The certificate identifies only
`localhost` and `127.0.0.1`; the test server binds an ephemeral IPv4 loopback port.

Generated for real local TLS 1.2/TLS 1.3 acceptance with OpenSSL (RSA 2048,
SHA-256, self-signed, ten-year validity beginning 2026-11-09). Tests load the
certificate temporarily through `SSL_CERT_FILE` and restore the process
environment. The rejection case deliberately does not trust this certificate.
No OpenSSL executable or extra Python dependency is needed when running tests.

The test key is included so independent source-package acceptance can reproduce
the handshake; no real model endpoint, API key or paper is involved.

"""TEST-ONLY synthetic mTLS PKI generator for utils/evidence_publisher_v1.py tests.

Every certificate/key this module produces is:
  - throwaway (regenerated fresh into a pytest tmp_path on every test run — nothing here is
    committed to the repository as static PEM material),
  - self-signed off a synthetic, test-only CA that exists nowhere outside this process,
  - never derived from, copied from, or related in any way to real HERMES/FALCON PKI.

Uses the system `openssl` CLI (no new Python dependency) purely to produce RSA keys + X.509
certs for exercising ssl.SSLContext in tests. NOT for use outside tests.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple


class TestPKI(NamedTuple):
    ca_cert: str
    server_cert: str
    server_key: str
    client_cert: str
    client_key: str
    # A second, independently-generated CA + client cert — useful for negative tests
    # (e.g. a client certificate the server's trust store does not recognise).
    other_ca_cert: str
    other_client_cert: str
    other_client_key: str


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _make_ca(tmp_path: Path, name: str) -> tuple[str, str]:
    key = tmp_path / f"{name}.key"
    cert = tmp_path / f"{name}.crt"
    _run("openssl", "genrsa", "-out", str(key), "2048")
    _run(
        "openssl", "req", "-x509", "-new", "-nodes", "-key", str(key), "-sha256",
        "-days", "2", "-out", str(cert), "-subj", f"/CN=HERMES-TEST-CA-{name}",
    )
    return str(cert), str(key)


def _issue(tmp_path: Path, name: str, cn: str, ca_cert: str, ca_key: str, san: str = None) -> tuple[str, str]:
    key = tmp_path / f"{name}.key"
    csr = tmp_path / f"{name}.csr"
    cert = tmp_path / f"{name}.crt"
    _run("openssl", "genrsa", "-out", str(key), "2048")
    _run("openssl", "req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/CN={cn}")
    extra = []
    if san:
        extfile = tmp_path / f"{name}.ext"
        extfile.write_text(f"subjectAltName={san}\n")
        extra = ["-extfile", str(extfile)]
    _run(
        "openssl", "x509", "-req", "-in", str(csr), "-CA", ca_cert, "-CAkey", ca_key,
        "-CAcreateserial", "-out", str(cert), "-days", "2", "-sha256", *extra,
    )
    return str(cert), str(key)


def generate_test_pki(tmp_path: Path) -> TestPKI:
    """Generate a fresh, throwaway CA + server + client cert/key set under `tmp_path`.

    The server cert carries SANs for both `localhost` and `127.0.0.1` so it validates
    against whichever loopback address the test's fake listener binds to.
    """
    ca_cert, ca_key = _make_ca(tmp_path, "ca")
    server_cert, server_key = _issue(
        tmp_path, "server", "hermes-evidence-test-server", ca_cert, ca_key,
        san="DNS:localhost,IP:127.0.0.1",
    )
    client_cert, client_key = _issue(
        tmp_path, "client", "hermes-evidence-test-client", ca_cert, ca_key,
    )

    other_ca_cert, other_ca_key = _make_ca(tmp_path, "other-ca")
    other_client_cert, other_client_key = _issue(
        tmp_path, "other-client", "untrusted-test-client", other_ca_cert, other_ca_key,
    )

    return TestPKI(
        ca_cert=ca_cert,
        server_cert=server_cert,
        server_key=server_key,
        client_cert=client_cert,
        client_key=client_key,
        other_ca_cert=other_ca_cert,
        other_client_cert=other_client_cert,
        other_client_key=other_client_key,
    )

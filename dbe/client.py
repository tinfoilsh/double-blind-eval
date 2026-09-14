"""Party-side client: verify the enclave, pin its TLS key, sign requests.

Verification is delegated to the ``tinfoil`` CLI (``tinfoil attestation verify
--json``), which checks the Sigstore-published measurement for the repo against
the live attestation and returns the attested TLS public-key fingerprint. Every
connection this client opens re-checks the server certificate against that
fingerprint, so a request can only ever reach the verified enclave.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from dbe.canonical import (
    Ed25519PrivateKey,
    HEADER_NONCE,
    HEADER_PARTY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    approval_signing_input,
    new_nonce,
    request_signing_input,
    sha256_hex,
    sign,
)

DBE_HOME = Path(os.environ.get("DBE_HOME", str(Path.home() / ".dbe")))


class VerificationError(Exception):
    pass


class PinMismatch(Exception):
    pass


class APIError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass
class Session:
    enclave: str
    repo: str
    tls_public_key_sha256: str
    audit_record: dict
    verified_at: float
    run_public_key: str | None = None
    identity: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return DBE_HOME / "sessions" / f"{self.enclave}.json"

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), indent=2))
        return self.path

    @classmethod
    def load(cls, enclave: str) -> "Session":
        path = DBE_HOME / "sessions" / f"{enclave}.json"
        if not path.exists():
            raise VerificationError(f"no verified session for {enclave}; run `dbe verify` first")
        return cls(**json.loads(path.read_text()))


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise VerificationError(f"tinfoil CLI produced no JSON: {text.strip()[:300]}")
    return json.loads(text[start : end + 1])


def verify_enclave(enclave: str, repo: str, tinfoil_bin: str | None = None) -> Session:
    binary = tinfoil_bin or shutil.which("tinfoil")
    if not binary:
        raise VerificationError("the `tinfoil` CLI is not installed; see https://docs.tinfoil.sh/containers/connecting")
    proc = subprocess.run(
        [binary, "attestation", "verify", "-e", enclave, "-r", repo, "--json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    record = _extract_json(proc.stdout or proc.stderr)
    if record.get("status") != "ok":
        raise VerificationError(f"attestation verification failed: {record.get('error') or record.get('status')}\n{proc.stderr.strip()[:500]}")
    keys = record.get("keys") or {}
    enclave_fp = keys.get("enclave")
    if not enclave_fp:
        raise VerificationError("audit record has no attested TLS key fingerprint")
    if keys.get("connection") and keys["connection"] != enclave_fp:
        raise VerificationError("attested TLS key differs from the key the CLI saw on the wire")
    if record.get("repo") != repo:
        raise VerificationError(f"audit record is for repo {record.get('repo')!r}, expected {repo!r}")
    return Session(enclave=enclave, repo=repo, tls_public_key_sha256=enclave_fp, audit_record=record, verified_at=time.time())


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that only completes if the server's SPKI hash matches the attested key."""

    def __init__(self, host: str, expected_spki_sha256: str, timeout: float = 600.0, port: int = 443):
        super().__init__(host, port=port, timeout=timeout)
        self.expected = expected_spki_sha256.lower()

    def connect(self) -> None:  # noqa: D401
        sock = socket.create_connection((self.host, self.port), self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)
        der = self.sock.getpeercert(binary_form=True)
        if not der:
            self.sock.close()
            raise PinMismatch("server presented no certificate")
        cert = x509.load_der_x509_certificate(der)
        spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        actual = hashlib.sha256(spki).hexdigest()
        if actual != self.expected:
            self.sock.close()
            raise PinMismatch(f"TLS key {actual[:16]}… is not the attested key {self.expected[:16]}…")


def pack_adapter(path: Path) -> bytes:
    """Return the adapter as tar.gz bytes. A directory is archived; an archive file is passed through."""
    path = Path(path)
    if path.is_file():
        return path.read_bytes()
    if not (path / "adapter_config.json").exists():
        raise FileNotFoundError(f"{path} has no adapter_config.json; is this a PEFT adapter directory?")
    buf = io.BytesIO()
    # Deterministic archive: fixed gzip mtime and normalized tar metadata, so the
    # same directory always produces the same bytes.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=6) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for file in sorted(p for p in path.rglob("*") if p.is_file()):
                info = tar.gettarinfo(str(file), arcname=str(file.relative_to(path)).replace("\\", "/"))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode = 0o644
                with open(file, "rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


class EnclaveClient:
    def __init__(
        self,
        session: Session | None = None,
        party: str | None = None,
        key: Ed25519PrivateKey | None = None,
        dev_url: str | None = None,
        timeout: float = 600.0,
    ):
        if session is None and dev_url is None:
            raise ValueError("either a verified session or a dev URL is required")
        self.session = session
        self.party = party
        self.key = key
        self.dev_url = dev_url
        self.timeout = timeout

    # ----- transport ----------------------------------------------------------------
    def _connection(self) -> http.client.HTTPConnection:
        if self.dev_url:
            parts = urlsplit(self.dev_url)
            if parts.scheme == "https":
                return http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=self.timeout, context=ssl._create_unverified_context())  # noqa: SLF001 - dev only
            return http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=self.timeout)
        assert self.session is not None
        return PinnedHTTPSConnection(self.session.enclave, self.session.tls_public_key_sha256, timeout=self.timeout)

    def request(self, method: str, path: str, body: bytes = b"", content_type: str = "application/octet-stream", signed: bool = True, extra_headers: dict | None = None):
        headers = {"Content-Type": content_type, "Content-Length": str(len(body)), "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        if signed:
            if not self.party or self.key is None:
                raise ValueError("this call must be signed; configure --party and --key")
            timestamp = str(int(time.time()))
            nonce = new_nonce()
            headers[HEADER_PARTY] = self.party
            headers[HEADER_TIMESTAMP] = timestamp
            headers[HEADER_NONCE] = nonce
            headers[HEADER_SIGNATURE] = sign(self.key, request_signing_input(method, path, sha256_hex(body), timestamp, nonce))
        conn = self._connection()
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        payload = data
        if (resp.getheader("Content-Type") or "").startswith("application/json"):
            payload = json.loads(data or b"null")
        if resp.status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else payload
            raise APIError(resp.status, detail)
        return resp.status, payload

    # ----- API ------------------------------------------------------------------------
    def healthz(self):
        return self.request("GET", "/api/healthz", signed=False)[1]

    def identity(self):
        return self.request("GET", "/api/identity", signed=False)[1]

    def status(self):
        return self.request("GET", "/api/status")[1]

    def manifest(self):
        return self.request("GET", "/api/manifest")[1]

    def run(self):
        return self.request("GET", "/api/run")[1]

    def upload_adapter(self, path: Path):
        data = pack_adapter(Path(path))
        return self.request("PUT", "/api/model/adapter", data, content_type="application/x-tar")[1]

    def upload_benchmark(self, path: Path):
        path = Path(path)
        return self.request("PUT", "/api/benchmark", path.read_bytes(), content_type="text/plain", extra_headers={"X-DBE-Filename": path.name})[1]

    def approve(self):
        if self.key is None:
            raise ValueError("approve needs the party key")
        manifest = self.manifest()
        digest = manifest["manifest_sha256"]
        body = json.dumps({"manifest_sha256": digest, "signature": sign(self.key, approval_signing_input(digest))}).encode()
        return self.request("POST", "/api/approve", body, content_type="application/json")[1], manifest

    def results(self):
        return self.request("GET", "/api/results")

    def receipt(self):
        return self.request("GET", "/api/receipt")[1]

"""Signing conventions shared by the enclave harness and the party client.

Everything that is signed goes through one of three domain-separated inputs so a
signature made for one purpose can never be replayed as another.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

REQUEST_DOMAIN = b"dbe-request-v1"
APPROVAL_DOMAIN = b"dbe-approve-v1"
RECEIPT_DOMAIN = b"dbe-receipt-v1"

BENCHMARK_OWNER = "benchmark-owner"
MODEL_OWNER = "model-owner"
PARTIES = (BENCHMARK_OWNER, MODEL_OWNER)
PARTY_ALIASES = {"bo": BENCHMARK_OWNER, "mo": MODEL_OWNER}

HEADER_PARTY = "X-DBE-Party"
HEADER_TIMESTAMP = "X-DBE-Timestamp"
HEADER_NONCE = "X-DBE-Nonce"
HEADER_SIGNATURE = "X-DBE-Signature"


def normalize_party(value: str) -> str:
    value = value.strip().lower()
    value = PARTY_ALIASES.get(value, value)
    if value not in PARTIES:
        raise ValueError(f"unknown party {value!r}; expected one of {PARTIES}")
    return value


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_signing_input(method: str, path: str, body_sha256: str, timestamp: str, nonce: str) -> bytes:
    """Ed25519 is deterministic, so the nonce is what makes two otherwise identical requests distinct."""
    return b"\n".join(
        [REQUEST_DOMAIN, method.upper().encode(), path.encode(), body_sha256.encode(), timestamp.encode(), nonce.encode()]
    )


def new_nonce() -> str:
    import secrets

    return secrets.token_hex(16)


def approval_signing_input(manifest_sha256: str) -> bytes:
    return APPROVAL_DOMAIN + b"\n" + manifest_sha256.encode()


def receipt_signing_input(receipt_body: dict) -> bytes:
    return RECEIPT_DOMAIN + b"\n" + canonical_json(receipt_body)


def generate_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_hex(key: Ed25519PrivateKey) -> str:
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return raw.hex()


def load_private_key_hex(value: str) -> Ed25519PrivateKey:
    raw = bytes.fromhex(value.strip())
    if len(raw) != 32:
        raise ValueError("private key must be 32 bytes of hex")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_hex(key: Ed25519PublicKey) -> str:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def load_public_key_hex(value: str) -> Ed25519PublicKey:
    raw = bytes.fromhex(value.strip())
    if len(raw) != 32:
        raise ValueError("public key must be 32 bytes of hex")
    return Ed25519PublicKey.from_public_bytes(raw)


def sign(key: Ed25519PrivateKey, message: bytes) -> str:
    return base64.b64encode(key.sign(message)).decode("ascii")


def verify(key: Ed25519PublicKey, message: bytes, signature_b64: str) -> bool:
    try:
        key.verify(base64.b64decode(signature_b64, validate=True), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def write_private_key(path: Path, key: Ed25519PrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(private_key_hex(key) + "\n")
    path.chmod(0o600)


def read_private_key(path: Path) -> Ed25519PrivateKey:
    return load_private_key_hex(path.read_text())

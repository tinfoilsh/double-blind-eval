"""What this enclave instance is: a per-boot run key plus the measured workload facts.

The measured config (``/tinfoil/config.yml``) is the file whose hash is in the
attested kernel command line, so its sha256 is the workload identity a verifier
can compare against the repo at the release tag. The attestation document the
shim publishes is hashed too, for cross-reference; parsing the quote is the
verifier's job, not the harness's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dbe.canonical import (
    Ed25519PrivateKey,
    generate_private_key,
    public_key_hex,
    sha256_hex,
)

TINFOIL_CONFIG = Path(os.environ.get("DBE_TINFOIL_CONFIG", "/tinfoil/config.yml"))
TINFOIL_ATTESTATION = Path(os.environ.get("DBE_TINFOIL_ATTESTATION", "/tinfoil/attestation.json"))


@dataclass
class ModelIdentity:
    name: str | None = None
    repo: str | None = None
    roothash: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "repo": self.repo, "roothash": self.roothash}


@dataclass
class Identity:
    run_key: Ed25519PrivateKey = field(default_factory=generate_private_key)
    config_sha256: str | None = None
    attestation_document_sha256: str | None = None
    image: str | None = None
    cvm_version: str | None = None
    base_model: ModelIdentity = field(default_factory=ModelIdentity)
    harness_version: str = "0.1.0"

    @property
    def run_public_key(self) -> str:
        return public_key_hex(self.run_key.public_key())

    def public_dict(self) -> dict:
        return {
            "run_public_key": self.run_public_key,
            "config_sha256": self.config_sha256,
            "attestation_document_sha256": self.attestation_document_sha256,
            "image": self.image,
            "cvm_version": self.cvm_version,
            "base_model": self.base_model.as_dict(),
            "harness_version": self.harness_version,
        }


def load_identity(
    config_path: Path = TINFOIL_CONFIG,
    attestation_path: Path = TINFOIL_ATTESTATION,
    harness_version: str = "0.1.0",
) -> Identity:
    identity = Identity(harness_version=harness_version)
    if config_path.exists():
        raw = config_path.read_bytes()
        identity.config_sha256 = sha256_hex(raw)
        try:
            config = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            config = {}
        identity.cvm_version = str(config.get("cvm-version")) if config.get("cvm-version") else None
        models = config.get("models") or []
        if models:
            first = models[0] or {}
            mpk = str(first.get("mpk") or first.get("emwp") or "")
            identity.base_model = ModelIdentity(
                name=first.get("name"),
                repo=first.get("repo"),
                roothash=mpk.split("_", 1)[0] if mpk else None,
            )
        containers = config.get("containers") or []
        if containers:
            identity.image = (containers[0] or {}).get("image")
    if attestation_path.exists():
        identity.attestation_document_sha256 = sha256_hex(attestation_path.read_bytes())
    return identity

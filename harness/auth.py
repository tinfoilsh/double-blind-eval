"""Request authentication: every mutating or policy-gated call is signed by a party key.

The two party public keys come from the measured config, so the set of people
who can act on this enclave is part of what both sides verified.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from dbe.canonical import (
    Ed25519PublicKey,
    HEADER_NONCE,
    HEADER_PARTY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    load_public_key_hex,
    normalize_party,
    request_signing_input,
    sha256_hex,
    verify,
)

MAX_SKEW_SECONDS = 300


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class RequestVerifier:
    parties: dict[str, Ed25519PublicKey]
    max_skew: int = MAX_SKEW_SECONDS
    _seen: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_hex(cls, keys: dict[str, str]) -> "RequestVerifier":
        return cls(parties={normalize_party(p): load_public_key_hex(k) for p, k in keys.items()})

    def verify(self, method: str, path: str, body: bytes, headers) -> str:
        party_text = headers.get(HEADER_PARTY)
        timestamp = headers.get(HEADER_TIMESTAMP)
        nonce = headers.get(HEADER_NONCE)
        signature = headers.get(HEADER_SIGNATURE)
        if not party_text or not timestamp or not nonce or not signature:
            raise AuthError(401, f"missing {HEADER_PARTY}, {HEADER_TIMESTAMP}, {HEADER_NONCE} or {HEADER_SIGNATURE}")
        if not (8 <= len(nonce) <= 64) or any(c not in "0123456789abcdef" for c in nonce):
            raise AuthError(401, f"{HEADER_NONCE} must be 8-64 hex characters")
        try:
            party = normalize_party(party_text)
        except ValueError as exc:
            raise AuthError(401, str(exc)) from exc
        key = self.parties.get(party)
        if key is None:
            raise AuthError(403, f"{party} is not a party to this enclave")
        try:
            ts = int(timestamp)
        except ValueError as exc:
            raise AuthError(401, "timestamp must be unix seconds") from exc
        now = time.time()
        if abs(now - ts) > self.max_skew:
            raise AuthError(401, f"timestamp outside the {self.max_skew}s window")
        message = request_signing_input(method, path, sha256_hex(body), timestamp, nonce)
        if not verify(key, message, signature):
            raise AuthError(401, "signature does not verify for the claimed party")
        self._gc(now)
        if signature in self._seen:
            raise AuthError(401, "replayed request")
        self._seen[signature] = now + self.max_skew * 2
        return party

    def _gc(self, now: float) -> None:
        if len(self._seen) > 4096:
            self._seen = {sig: exp for sig, exp in self._seen.items() if exp > now}

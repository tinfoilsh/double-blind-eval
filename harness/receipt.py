"""Run receipts: the one artifact both parties and any third party may hold.

A receipt binds the run manifest, both approvals, the workload identity and the
outcome summary under the enclave's per-boot run key. It never contains
prompts, completions or the adapter.
"""

from __future__ import annotations

from dbe.canonical import (
    Ed25519PrivateKey,
    approval_signing_input,
    load_public_key_hex,
    public_key_hex,
    receipt_signing_input,
    sign,
    verify,
)

RECEIPT_VERSION = "dbe-receipt-v1"


def build_receipt(body: dict, run_key: Ed25519PrivateKey) -> dict:
    body = dict(body)
    body["version"] = RECEIPT_VERSION
    body["run_public_key"] = public_key_hex(run_key.public_key())
    return {"receipt": body, "signature": sign(run_key, receipt_signing_input(body))}


def verify_receipt(envelope: dict) -> list[str]:
    """Return a list of problems; empty means the receipt verifies.

    Checks the enclave signature, both approvals and the party keys. Whether the run key
    belongs to a particular enclave instance is a separate question: a receipt from an
    earlier run or an earlier deployment legitimately carries a different per-boot key.
    """
    problems: list[str] = []
    body = envelope.get("receipt")
    signature = envelope.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, str):
        return ["receipt envelope must have 'receipt' object and 'signature' string"]
    if body.get("version") != RECEIPT_VERSION:
        problems.append(f"unexpected receipt version {body.get('version')!r}")
    run_pub_hex = body.get("run_public_key")
    if not isinstance(run_pub_hex, str):
        return problems + ["receipt has no run_public_key"]
    try:
        run_pub = load_public_key_hex(run_pub_hex)
    except ValueError as exc:
        return problems + [f"bad run_public_key: {exc}"]
    if not verify(run_pub, receipt_signing_input(body), signature):
        problems.append("receipt signature does not verify under run_public_key")
    approvals = body.get("approvals") or {}
    parties = body.get("parties") or {}
    manifest_sha256 = body.get("manifest_sha256")
    for party, approval in approvals.items():
        pub_hex = parties.get(party)
        if not pub_hex:
            problems.append(f"approval from {party} but no party key in receipt")
            continue
        if approval.get("manifest_sha256") != manifest_sha256:
            problems.append(f"{party} approved a different manifest than the receipt's")
            continue
        try:
            pub = load_public_key_hex(pub_hex)
        except ValueError as exc:
            problems.append(f"bad key for {party}: {exc}")
            continue
        if not verify(pub, approval_signing_input(manifest_sha256), approval.get("signature", "")):
            problems.append(f"{party} approval signature does not verify")
    for party in ("benchmark-owner", "model-owner"):
        if party not in approvals:
            problems.append(f"receipt lacks an approval from {party}")
    return problems

# Protocol

Three parties, one enclave, one run.

```
benchmark owner ──verify──▶ enclave ◀──verify── model owner
        │ PUT /api/benchmark      │      PUT /api/model/adapter │
        │ POST /api/approve       │      POST /api/approve      │
        │                  (run starts on the 2nd approval)     │
        ◀── GET /api/results ─────┤───── GET /api/receipt ─────▶
```

## Who is who

The two party public keys are Ed25519 keys written into the measured `tinfoil-config.yml`
(`DBE_BENCHMARK_OWNER_PUBKEY`, `DBE_MODEL_OWNER_PUBKEY`). They are part of the enclave
measurement: a different pair of parties is a different release that both sides have to
verify again.

## Verifying the enclave

`dbe verify` runs `tinfoil attestation verify -e <enclave> -r <repo> --json`, which checks
the Sigstore-published measurement of the repo's latest release against the enclave's live
hardware attestation and returns the attested TLS public-key fingerprint. Every later
connection is pinned to that fingerprint (`dbe/client.py: PinnedHTTPSConnection`). The
enclave's `/api/identity` is fetched over the pinned channel and its `run_public_key` is
stored in the local session for receipt verification.

## Request signing

Every call except `/api/healthz` and `/api/identity` carries three headers:

| Header | Value |
|---|---|
| `X-DBE-Party` | `benchmark-owner` or `model-owner` |
| `X-DBE-Timestamp` | unix seconds; must be within 300 s of the enclave clock |
| `X-DBE-Nonce` | 8–64 hex characters, fresh per request |
| `X-DBE-Signature` | base64 Ed25519 over the signing input below |

Signing input, newline-separated:

```
dbe-request-v1
<METHOD>
<path>
<sha256 hex of the request body>
<timestamp>
<nonce>
```

The enclave rejects unknown parties, stale timestamps and replayed signatures.

## Manifest and approvals

`GET /api/manifest` returns the canonical run description and its sha256:

```json
{
  "version": "dbe-manifest-v1",
  "parties": {"benchmark-owner": "<hex>", "model-owner": "<hex>"},
  "output_policy": "bo:results+receipt,mo:receipt",
  "sampling": {"max_tokens": 100, "temperature": 0.8, "top_k": 40},
  "base_model": "gemma4-31b",
  "served_model": "private-adapter",
  "adapter_sha256": "<hex or null>",
  "benchmark_sha256": "<hex or null>",
  "prompt_count": 10,
  "scored": false
}
```

`adapter_sha256` is a content hash, not the hash of the uploaded archive: sha256 over the
canonical JSON of `{relative path: sha256(file)}` for every file in the adapter directory.
`dbe model hash ./adapter` prints the same value locally, so the model owner can check that
the receipt names exactly the adapter they hold. `benchmark_sha256` is the sha256 of the
uploaded file bytes.

Each party approves by signing `dbe-approve-v1\n<manifest sha256>` and posting
`{"manifest_sha256", "signature"}` to `/api/approve`. The run starts when both parties have
approved the *current* manifest. Uploading either asset again recomputes the manifest and
drops every approval, so consent is always for exactly the bytes that will run.

## Output policy

`DBE_OUTPUT_POLICY` in the measured config names the grants per party. Grants are
`results` (per-prompt completions, timing, optional score) and `receipt`. The default
`bo:results+receipt,mo:receipt` mirrors OpenMined's demo, where results returned to the
benchmark owner only. `/api/identity` and `/api/manifest` are readable by both parties.

## What the enclave never does

- Expose vLLM: it binds to `127.0.0.1`; the shim only forwards `/api/*`. vLLM's runtime
  LoRA loading endpoint (which vLLM itself flags as development-only) is therefore reachable
  solely by the harness, which only calls it with a model-owner-signed upload.
- Egress: the config declares no network, so the firewall is closed.
- Persist: assets and results live in tmpfs and die with the enclave.
- Log content: the harness logs hashes and counts, never prompts or completions.

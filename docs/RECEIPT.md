# Run receipt

The receipt is the one artifact every party (and any third party) may hold. It is a JSON
envelope signed by the enclave's per-boot run key:

```json
{
  "receipt": {
    "version": "dbe-receipt-v1",
    "run_id": "…",
    "run_public_key": "<hex>",
    "manifest": { "...": "the manifest both parties approved" },
    "manifest_sha256": "<hex>",
    "parties": {"benchmark-owner": "<hex>", "model-owner": "<hex>"},
    "approvals": {
      "benchmark-owner": {"manifest_sha256": "<hex>", "signature": "<base64>"},
      "model-owner":     {"manifest_sha256": "<hex>", "signature": "<base64>"}
    },
    "identity": {
      "config_sha256": "<sha256 of the measured tinfoil-config.yml>",
      "attestation_document_sha256": "<hex>",
      "image": "ghcr.io/tinfoilsh/double-blind-eval@sha256:…",
      "cvm_version": "0.14.7",
      "base_model": {"name": "gemma-4-31b", "repo": "google/gemma-4-31B-it@…", "roothash": "<hex>"},
      "harness_version": "0.1.0"
    },
    "prompt_count": 10,
    "completed": 10,
    "failed_prompts": 0,
    "score": null,
    "started_at": 1757800000.0,
    "finished_at": 1757800042.5
  },
  "signature": "<base64 Ed25519 over dbe-receipt-v1\\n<canonical JSON of receipt>>"
}
```

Canonical JSON is `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

## Verifying

`dbe receipt verify receipt.json --tag v0.1.0` checks:

1. the signature under `run_public_key`;
2. both approvals: each party's signature over `dbe-approve-v1\n<manifest_sha256>` under the party key named in the receipt, and that both name the receipt's manifest;
3. that `identity.config_sha256` equals the sha256 of `tinfoil-config.yml` at the given release tag, which is the file whose hash sits in the attested kernel command line.

Steps 1 and 2 need nothing but the file. Step 3 needs the public repo.

The run key is generated fresh at every enclave boot, so a receipt from an earlier run or an
earlier deployment carries a different key than the enclave you verify today. That is expected,
and the command says so rather than failing. If you are checking a receipt from a run you just
took part in, `--require-live` additionally insists that its run key is the one your last
`dbe verify` pinned. Tying a receipt to genuine hardware after the fact relies on whoever
verified that enclave instance at the time; the JSON audit record from `tinfoil attestation
verify` can be stored next to the receipt for that purpose. Binding the run key into the
attestation report itself is a planned follow-up.

## What is not in a receipt

Prompts, completions, the adapter, any party's private data. The model identity is the
pack roothash of the public base weights plus the content hash of the (private) adapter
(`dbe model hash`, see PROTOCOL.md).

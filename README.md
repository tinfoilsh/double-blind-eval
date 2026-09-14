# double-blind-eval

A double-blind model evaluation in one attested [Tinfoil Container](https://docs.tinfoil.sh/containers/overview):
a benchmark owner's private prompts run against a model owner's private model, neither party
sees the other's asset, the operator sees neither, and anyone can verify what ran.

It is a port of OpenMined's PySyft double-blind eval to Tinfoil. The inference engine is
Tinfoil's production gemma4-31b image, pinned by digest. This repo adds a ~600-line harness
beside it, a `dbe` command line for the two parties, and two notebooks that mirror
OpenMined's.

```
benchmark owner                   enclave (inf10, 1×H200, TDX)              model owner
  dbe verify ─────────────────▶  attested release, TLS key pinned  ◀──────── dbe verify
  dbe benchmark upload ───────▶  prompts in tmpfs                   ◀── dbe model upload (LoRA)
  dbe approve ────────────────▶  run starts on the 2nd approval     ◀──────── dbe approve
  dbe results ◀────────────────  completions, TTFT, tok/s, receipt  ─────────▶ dbe receipt
```

Everything that decides the run is in the measured `tinfoil-config.yml`: the engine image,
the base model pack, the two party public keys, the output policy and the sampling
parameters. Approving a run means verifying that measurement and signing the run manifest.

## Quickstarts

Install the client once (Python 3.11+ and the [`tinfoil` CLI](https://docs.tinfoil.sh/containers/cli)):

```sh
pip install git+https://github.com/tinfoilsh/double-blind-eval
dbe keygen --party benchmark-owner     # or model-owner; prints the public key
```

### Operator

```sh
tinfoil container create dbe --repo tinfoilsh/double-blind-eval --tag v0.1.0 \
  --host control.inf10.tinfoil.sh --promote-release=true
# → dbe.<org>.containers.tinfoil.dev ; ready in ~10 minutes (weights load)
```

The operator can start, stop and delete the enclave. It cannot read the prompts, the
adapter or the results, and there is no SSH: debug launches change the measurement and are
rejected by `dbe verify`.

To move to a new release, either let the dashboard's update flow swap it in (this needs a
spare GPU on the host) or delete and re-create the container with the new `--tag`. Both
parties re-run `dbe verify` afterwards, since the measurement changes with every release.

### Model owner

```sh
export DBE_ENCLAVE=dbe.<org>.containers.tinfoil.dev DBE_PARTY=model-owner
dbe verify                          # Sigstore digest → hardware measurement → TLS key pin
dbe model hash ./my-adapter/        # the content hash the enclave will report for your adapter
dbe model upload ./my-adapter/      # PEFT LoRA directory (adapter_config.json + weights) or .tar.gz
dbe manifest                        # what will run; compare the hash with the other party
dbe approve
dbe run --wait
dbe receipt get --out receipt.json  # the model owner gets the receipt, not the results
```

### Benchmark owner

```sh
export DBE_ENCLAVE=dbe.<org>.containers.tinfoil.dev DBE_PARTY=benchmark-owner
dbe verify
bench/fetch_ailuminate_demo.sh      # MLCommons AILuminate demo sample, as in OpenMined's demo
dbe benchmark upload bench/ailuminate_demo_sample.csv
dbe manifest
dbe approve
dbe run --wait
dbe results --out results.json --show
dbe receipt verify receipt.json --tag v0.1.0
```

Benchmarks are CSV in the AILuminate shape (`prompt_uid, hazard, locale, prompt_text`) or
JSONL with `prompt` and an optional `expected` column, which turns on exact-match scoring.

### Verifier (anyone)

```sh
dbe -e dbe.<org>.containers.tinfoil.dev verify
dbe receipt verify receipt.json --tag v0.1.0
```

## Adapting it for your own parties

1. Fork this repo (it must stay public: the measurement is published to Sigstore from it).
2. Put your two party public keys and, if you like, a different `DBE_OUTPUT_POLICY` or
   `DBE_SAMPLING` into `tinfoil-config.yml`.
3. Run the **Tinfoil Release** workflow (`gh workflow run tinfoil-release.yml -f version=v0.1.0`).
   It builds the image on a standard GitHub runner, pins its digest into the config, tags the
   release and publishes the measurement.
4. Deploy with `tinfoil container create` on any Tinfoil-managed host, yours or ours.

No code changes are needed for a new benchmark, a new adapter or new parties.

## What leaves the enclave

| Party | Gets |
|---|---|
| benchmark owner | per-prompt completion, time to first token, decode tokens/s, optional score, receipt |
| model owner | receipt only (score too if the policy says `mo:results`) |
| operator | nothing but ciphertext sizes and timing |
| anyone | the receipt, if a party shares it: manifest hash, both approvals, workload identity, counts |

See [docs/PROTOCOL.md](docs/PROTOCOL.md) and [docs/RECEIPT.md](docs/RECEIPT.md).

## Development

```sh
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"
pytest -q
```

Run the harness locally against a fake engine with `DBE_DEV_URL=http://127.0.0.1:8080` for
the client; the tests in `tests/` show the full flow with a mocked vLLM.

## Status

Running. The reference deployment is `dbe.tinfoil.containers.tinfoil.dev` on a 1×H200 Intel
TDX host (cvmimage 0.14.7): vLLM initializes in about 100 s after the image pull, a ten-prompt
run finishes in under ten seconds, first token in 130–200 ms once warm, decode 40–50 tok/s.

Lane A: assets travel into the running enclave over attested TLS; nothing is staged on the
host. A follow-up lane releases the model owner's key from their own keyserver at boot
([Tinfoil Secrets](https://docs.tinfoil.sh/containers/private-secrets)) so encrypted weights
can sit at rest on the host.

Apache-2.0.

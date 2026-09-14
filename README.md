# double-blind-eval

Run a private benchmark against a private model inside an attested [Tinfoil Container](https://docs.tinfoil.sh/containers/overview).
Neither party sees the other's data, the operator sees neither, and anyone can verify what ran.

A live instance runs at `dbe.tinfoil.containers.tinfoil.dev`.

## Quickstart

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and the [`tinfoil` CLI](https://docs.tinfoil.sh/containers/cli).

```sh
uv tool install git+https://github.com/tinfoilsh/double-blind-eval
export DBE_ENCLAVE=dbe.tinfoil.containers.tinfoil.dev
```

Verify the enclave. This checks the published release measurement against the live hardware attestation and pins the TLS key:

```sh
dbe verify
```

When an evaluation finishes, the enclave signs a **receipt**: which code release ran, the hashes of the prompt set and the adapter, the sampling settings, how many prompts ran, and both parties' approval signatures. No prompts, completions or weights. Anyone can check one offline. Here is one from a real run on the live enclave, made under release v0.0.3:

```sh
curl -sO https://raw.githubusercontent.com/tinfoilsh/double-blind-eval/main/docs/sample-receipt.json
dbe receipt verify sample-receipt.json --tag v0.0.3
```

This checks the enclave's signature, both approvals, and that the receipt's config hash matches `tinfoil-config.yml` at that release tag. Change any value in the file and the check fails. See [docs/RECEIPT.md](docs/RECEIPT.md) for the full format.

## Run an evaluation

Each party needs a key. Its public half goes into the enclave's config, so the enclave only listens to the two parties it was built for.

```sh
dbe keygen --party benchmark-owner    # or model-owner
```

**Benchmark owner**

```sh
export DBE_PARTY=benchmark-owner
dbe verify
dbe benchmark upload prompts.csv
dbe approve
dbe results --out results.json
```

**Model owner**

```sh
export DBE_PARTY=model-owner
dbe verify
dbe model upload ./my-lora-adapter
dbe approve
dbe receipt get --out receipt.json
```

The run starts once both have approved. Results go to the benchmark owner; the model owner gets a signed receipt.
`dbe manifest` shows exactly what you are approving.

Prompts are a CSV with a `prompt_text` column (the MLCommons AILuminate demo set works as-is: `bench/fetch_ailuminate_demo.sh`).
The adapter is a PEFT LoRA directory for `google/gemma-4-31B-it`.

## Follow along in a notebook

There is one notebook per party, with the same steps as above and a short explanation before each one:

- [`notebooks/1-model-owner.ipynb`](notebooks/1-model-owner.ipynb)
- [`notebooks/2-benchmark-owner.ipynb`](notebooks/2-benchmark-owner.ipynb)

```sh
git clone https://github.com/tinfoilsh/double-blind-eval && cd double-blind-eval
export DBE_ENCLAVE=dbe.tinfoil.containers.tinfoil.dev
uv run --group notebooks jupyter notebook notebooks/
```

Open your party's notebook and run the cells top to bottom. Each cell calls the same `dbe` commands, so you can see what it did and repeat it on the command line.

## Deploy your own

1. Fork this repo.
2. Put your two party public keys in `tinfoil-config.yml`.
3. Run the **Tinfoil Release** workflow: `gh workflow run tinfoil-release.yml -f version=v0.1.0`
4. Deploy: `tinfoil container create dbe --repo <you>/double-blind-eval --tag v0.1.0`

## Develop

```sh
uv run pytest
uv run python tests/smoke_cli.py   # the real CLI against the harness with a fake engine
```

## Learn more

- [How the protocol works](docs/PROTOCOL.md): signing, manifests, approvals, output policy
- [What a receipt proves](docs/RECEIPT.md)
- `tinfoil-config.yml`: everything the enclave is measured on

Apache-2.0.

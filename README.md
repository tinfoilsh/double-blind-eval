# double-blind-eval

Run a private benchmark against a private model inside an attested [Tinfoil Container](https://docs.tinfoil.sh/containers/overview).
Neither party sees the other's data, the operator sees neither, and anyone can verify what ran.

Inspired by OpenMined's [PySyft double-blind evaluation](https://www.youtube.com/watch?v=WMP2vn1gO9c), rebuilt on Tinfoil Containers.

A live instance runs at `dbe.tinfoil.containers.tinfoil.dev`.

## Quickstart

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and the [`tinfoil` CLI](https://docs.tinfoil.sh/containers/cli).

```sh
uv tool install git+https://github.com/tinfoilsh/double-blind-eval
export DBE_ENCLAVE=dbe.tinfoil.containers.tinfoil.dev
```

To update later: `uv tool upgrade double-blind-eval`.

Verify the enclave. Anyone can, with no account or key: your machine checks the enclave's hardware attestation against the code measurement published for the release, and pins its TLS key. Tinfoil is not in the trust path.

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

Two parties, five steps. Uploads first, approvals last. At any point, `dbe status` shows what is in, what is missing, and who acts next.

Each party has a key. Its public half is in the enclave's config, so the enclave only listens to the two parties it was built for. If you were handed keys for the sandbox, export them and pick a party per command:

```sh
export DBE_BENCHMARK_OWNER_KEY=...   # the 64-character values you were given, no quotes
export DBE_MODEL_OWNER_KEY=...
dbe --party benchmark-owner status
```

To switch roles on the same terminal, export `DBE_PARTY` before running a command (`export DBE_PARTY=model-owner`); it takes the place of `--party` for every command that follows.

> [!NOTE]
> You do not need to create keys to run the demo; the sandbox keys above are all it takes.
> Creating your own keys only matters if you deploy your own enclave, since the enclave is built for specific keys. See [Deploy your own](#deploy-your-own).

**1. Both parties verify the enclave.**

```sh
export DBE_ENCLAVE=dbe.tinfoil.containers.tinfoil.dev
dbe verify
```

**2. Both parties upload, in either order.**

```sh
# benchmark owner
export DBE_PARTY=benchmark-owner
bench/fetch_ailuminate_demo.sh                       # MLCommons AILuminate sample, or bring your own CSV
dbe benchmark upload bench/ailuminate_demo_sample.csv

# model owner
export DBE_PARTY=model-owner
bench/fetch_demo_adapter.sh                          # a public LoRA for gemma-4-31B-it, or bring your own
dbe model upload demo-adapter/
```

**3. Both parties check what will run.**

```sh
dbe status      # checklist: both uploads in? who has approved?
dbe manifest    # the exact hash you are about to sign; compare it with the other party
```

**4. Both parties approve, in either order.**

```sh
dbe approve
```

`dbe approve` refuses to run before both uploads are in. The run starts the moment the second approval lands. If either party re-uploads after that, both approvals are dropped and step 4 repeats.

**5. Collect.**

```sh
dbe run --wait                          # either party
dbe results --out results.json          # benchmark owner: completions, timing, receipt
dbe receipt get --out receipt.json      # model owner: receipt only
```

Prompts are a CSV with a `prompt_text` column. An adapter is a PEFT LoRA directory (`adapter_config.json` plus weights) trained on `google/gemma-4-31B-it`; `dbe model hash demo-adapter/` prints the identity the receipt will carry.

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
2. Create a key for each party: `dbe keygen --party benchmark-owner --env`, then the same for `model-owner`. Each prints a public key and an export line; the export line goes to whoever will act as that party.
3. Put the two public keys in `tinfoil-config.yml` (`DBE_BENCHMARK_OWNER_PUBKEY`, `DBE_MODEL_OWNER_PUBKEY`).
4. Run the **Tinfoil Release** workflow: `gh workflow run tinfoil-release.yml -f version=v0.1.0`
5. Deploy: `tinfoil container create dbe --repo <you>/double-blind-eval --tag v0.1.0`

Changing a key later means repeating steps 3 to 5: the enclave is built for specific keys, and both parties re-run `dbe verify` after every release.

## Develop

```sh
uv run pytest
uv run python tests/smoke_cli.py   # the real CLI against the harness with a fake engine
```

## New to Tinfoil Containers?

This repo is also a complete, small example of one. The whole deployment is the one file
`tinfoil-config.yml`: which image runs, its environment and command, how much GPU it gets, and
which paths are reachable from outside. A GitHub release measures that file and publishes the
expected enclave measurement; `dbe verify` checks a live enclave against it on your own machine.
No inbound network is open except the paths listed, no egress exists unless declared, and the
operator never sees inside.

[docs/TINFOIL-CONTAINERS.md](docs/TINFOIL-CONTAINERS.md) maps Confidential Space concepts to
Tinfoil ones and walks through this repo's config line by line.

## Learn more

- [Tinfoil Containers, as used in this repo](docs/TINFOIL-CONTAINERS.md): a primer, with a Confidential Space mapping
- [How the protocol works](docs/PROTOCOL.md): signing, manifests, approvals, output policy
- [What a receipt proves](docs/RECEIPT.md)
- [Tinfoil Containers documentation](https://docs.tinfoil.sh/containers/overview)

Apache-2.0.

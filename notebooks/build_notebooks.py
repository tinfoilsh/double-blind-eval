"""Generates the two party notebooks. Run after editing: python notebooks/build_notebooks.py"""
from pathlib import Path
import nbformat as nbf

HERE = Path(__file__).parent

def nb(cells):
    n = nbf.v4.new_notebook()
    n.cells = [nbf.v4.new_markdown_cell(c[1]) if c[0] == "md" else nbf.v4.new_code_cell(c[1]) for c in cells]
    n.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return n

common_setup = '''import os, subprocess, json
from pathlib import Path
if Path.cwd().name == "notebooks":       # run from the repo root so bench/ paths resolve
    os.chdir("..")
ENCLAVE = os.environ.get("DBE_ENCLAVE", "dbe.tinfoil.containers.tinfoil.dev")
REPO = os.environ.get("DBE_REPO", "tinfoilsh/double-blind-eval")
TAG = os.environ.get("DBE_TAG", "v0.1.0")
os.environ.update(DBE_ENCLAVE=ENCLAVE, DBE_REPO=REPO, DBE_PARTY=PARTY)

def dbe(*args):
    """Run a dbe command and print its output."""
    proc = subprocess.run(["dbe", *args], capture_output=True, text=True)
    print(proc.stdout or proc.stderr)
    return proc'''

model_owner = [
    ("md", "# Model owner\n\nYou hold a private LoRA adapter for gemma-4-31B-it. This notebook verifies the enclave, uploads the adapter over the attestation-pinned channel, approves the run and collects the receipt. You never see the benchmark owner's prompts or the results.\n\nSetup once: install [uv](https://docs.astral.sh/uv/getting-started/installation/) and the [`tinfoil` CLI](https://docs.tinfoil.sh/containers/cli), then from the repo root run `uv run --group notebooks jupyter notebook notebooks/`. Create your key with `dbe keygen --party model-owner` and have the operator put the printed public key into `tinfoil-config.yml`. Set `DBE_ENCLAVE` to your enclave before starting Jupyter, or edit the first code cell."),
    ("code", 'PARTY = "model-owner"\n' + common_setup),
    ("md", "## 1. Verify the enclave\n\n`dbe verify` checks the Sigstore-published measurement of the release against the enclave's live hardware attestation and pins the TLS key. Everything below refuses to talk to anything else."),
    ("code", 'dbe("verify")'),
    ("md", "## 2. Upload the private adapter\n\nA PEFT adapter directory (`adapter_config.json` + `adapter_model.safetensors`) or a `.tar.gz` of one. It is held in enclave memory and loaded into vLLM; it never touches the host disk."),
    ("code", 'ADAPTER_PATH = os.environ.get("DBE_ADAPTER", "./adapter")\ndbe("model", "upload", ADAPTER_PATH)'),
    ("md", "## 3. Review and approve the run manifest\n\nThe manifest names the adapter hash, the benchmark hash, the sampling parameters and the output policy. Compare `manifest_sha256` with the benchmark owner out of band, then sign it. The run starts when both parties have approved."),
    ("code", 'dbe("manifest")'),
    ("code", 'dbe("approve")'),
    ("md", "## 4. Wait for the run and collect the receipt\n\nThe output policy gives the model owner the receipt only: proof of what ran, signed by the enclave."),
    ("code", 'dbe("run", "--wait")\ndbe("receipt", "get", "--out", "receipt.json")\ndbe("receipt", "verify", "receipt.json", "--tag", TAG)'),
]

benchmark_owner = [
    ("md", "# Benchmark owner\n\nYou hold a private prompt set. This notebook verifies the enclave, uploads the prompts over the attestation-pinned channel, approves the run and reads the results. You never see the model owner's adapter.\n\nSetup once: install [uv](https://docs.astral.sh/uv/getting-started/installation/) and the [`tinfoil` CLI](https://docs.tinfoil.sh/containers/cli), then from the repo root run `uv run --group notebooks jupyter notebook notebooks/`. Create your key with `dbe keygen --party benchmark-owner` and have the operator put the printed public key into `tinfoil-config.yml`. Set `DBE_ENCLAVE` to your enclave before starting Jupyter, or edit the first code cell."),
    ("code", 'PARTY = "benchmark-owner"\n' + common_setup),
    ("md", "## 1. Verify the enclave"),
    ("code", 'dbe("verify")'),
    ("md", "## 2. Prepare and upload the prompt set\n\nThe sample below is the MLCommons AILuminate demo set, cut to the first prompt per hazard exactly as OpenMined's demo did. Any CSV with `prompt_text` (or JSONL with `prompt` and optional `expected`) works."),
    ("code", '!bash bench/fetch_ailuminate_demo.sh bench/ailuminate_demo_sample.csv\nBENCH = os.environ.get("DBE_BENCH", "bench/ailuminate_demo_sample.csv")\ndbe("benchmark", "upload", BENCH)'),
    ("md", "## 3. Review and approve the run manifest"),
    ("code", 'dbe("manifest")'),
    ("code", 'dbe("approve")'),
    ("md", "## 4. Read the results\n\nPer prompt: completion, time to first token, decode tokens per second. If the benchmark had an `expected` column the exact-match score is included. The receipt is signed by the enclave and carries both approvals."),
    ("code", 'dbe("run", "--wait")\ndbe("results", "--out", "results.json", "--show")\nreceipt = json.load(open("results.json"))["receipt"]\njson.dump(receipt, open("receipt.json", "w"), indent=2)\ndbe("receipt", "verify", "receipt.json", "--tag", TAG)'),
]

nbf.write(nb(model_owner), HERE / "1-model-owner.ipynb")
nbf.write(nb(benchmark_owner), HERE / "2-benchmark-owner.ipynb")
print("wrote notebooks")

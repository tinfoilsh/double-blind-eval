#!/usr/bin/env bash
# Fetch a public LoRA adapter for google/gemma-4-31B-it to stand in as the model
# owner's private model: Kobarac/gemma4-31b-factual-tool-selector-lora (Apache-2.0,
# rank 8, trained on the exact base revision the enclave serves). Two files, ~32 MB.
set -euo pipefail

REPO="${ADAPTER_REPO:-Kobarac/gemma4-31b-factual-tool-selector-lora}"
REV="${ADAPTER_REV:-main}"
OUT="${1:-$(cd "$(dirname "$0")/.." && pwd)/demo-adapter}"

mkdir -p "$OUT"
for f in adapter_config.json adapter_model.safetensors; do
  curl -fsSL "https://huggingface.co/$REPO/resolve/$REV/$f" -o "$OUT/$f"
done
python3 - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
cfg = json.loads((out / "adapter_config.json").read_text())
size = (out / "adapter_model.safetensors").stat().st_size
print(f"adapter for {cfg.get('base_model_name_or_path')} (rank {cfg.get('r')}), {size/1e6:.1f} MB -> {out}")
PY

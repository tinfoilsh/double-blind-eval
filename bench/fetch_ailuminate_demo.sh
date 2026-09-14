#!/usr/bin/env bash
# Fetch the MLCommons AILuminate v1.0 demo prompt set (the set OpenMined's demo
# used) and cut the same deterministic sample: the first prompt of each hazard,
# at most ten rows. The data is not vendored here; it stays MLCommons'.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_URL="${AILUMINATE_URL:-https://raw.githubusercontent.com/mlcommons/ailuminate/main/airr_official_1.0_demo_en_us_prompt_set_release.csv}"
FULL="$HERE/ailuminate_demo_full.csv"
OUT="${1:-$HERE/ailuminate_demo_sample.csv}"
ROWS="${ROWS:-10}"

curl -fsSL "$SRC_URL" -o "$FULL"
python3 - "$FULL" "$OUT" "$ROWS" <<'PY'
import csv, sys
src, out, rows = sys.argv[1], sys.argv[2], int(sys.argv[3])
seen, kept = set(), []
with open(src, newline="", encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        hazard = row.get("hazard")
        if hazard in seen:
            continue
        seen.add(hazard)
        kept.append({
            "prompt_uid": row.get("prompt_uid") or row.get("release_prompt_id") or f"row-{len(kept):04d}",
            "hazard": hazard or "",
            "locale": row.get("locale", ""),
            "prompt_text": row.get("prompt_text", ""),
        })
        if len(kept) >= rows:
            break
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["prompt_uid", "hazard", "locale", "prompt_text"])
    w.writeheader()
    w.writerows(kept)
print(f"wrote {len(kept)} prompts ({len(seen)} hazards) to {out}")
PY

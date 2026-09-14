"""Human-readable view of where a run stands and what happens next.

Pure functions over the enclave's /api/status document, so they are easy to test and the
CLI, notebooks and any future UI tell the same story.
"""

from __future__ import annotations

from dbe.canonical import BENCHMARK_OWNER, MODEL_OWNER

PRETTY = {BENCHMARK_OWNER: "benchmark owner", MODEL_OWNER: "model owner"}


def _short(digest: str | None, n: int = 12) -> str:
    return f"{digest[:n]}…" if digest else "-"


def missing_uploads(status: dict) -> list[str]:
    missing = []
    if not status.get("benchmark"):
        missing.append(BENCHMARK_OWNER)
    if not status.get("adapter"):
        missing.append(MODEL_OWNER)
    return missing


def approved_parties(status: dict) -> list[str]:
    return [p for p, current in (status.get("approvals") or {}).items() if current]


def next_step(status: dict) -> str:
    run = status.get("run") or {}
    if run.get("status") == "running":
        return f"Running: {run.get('completed', 0)}/{run.get('total', '?')} prompts. Either party: dbe run --wait"
    if run.get("status") == "done":
        return "Run finished. Benchmark owner: dbe results. Model owner: dbe receipt get. Uploading a new asset starts a fresh round."
    if run.get("status") == "failed":
        return f"Run failed: {run.get('error')}. Re-upload an asset to start a fresh round."
    missing = missing_uploads(status)
    if BENCHMARK_OWNER in missing:
        return "Waiting for the benchmark owner to upload a prompt set (dbe benchmark upload)."
    if MODEL_OWNER in missing:
        return "Waiting for the model owner to upload an adapter (dbe model upload). Approve only after both uploads are in."
    approved = approved_parties(status)
    waiting = [p for p in (BENCHMARK_OWNER, MODEL_OWNER) if p not in approved]
    if len(waiting) == 2:
        return "Both assets are in. Each party reviews `dbe manifest`, then runs `dbe approve` (either order). The run starts on the second approval."
    if len(waiting) == 1:
        return f"Approval 1 of 2 recorded. Waiting for the {PRETTY[waiting[0]]} to run `dbe approve`; the run starts the moment it lands."
    return "Both approvals recorded; the run is starting."


def render_checklist(status: dict, enclave: str | None = None) -> str:
    bench = status.get("benchmark") or {}
    adapter = status.get("adapter") or {}
    approvals = status.get("approvals") or {}
    lines = []
    if enclave:
        lines.append(f"{enclave}  (phase: {status.get('phase', '?')})")
    lines.append(
        f"  [{'x' if bench else ' '}] prompt set uploaded      "
        + (f"{bench.get('count')} prompts, sha256 {_short(bench.get('sha256'))}" if bench else "benchmark owner has not uploaded yet")
    )
    lines.append(
        f"  [{'x' if adapter else ' '}] adapter uploaded         "
        + (f"content hash {_short(adapter.get('sha256'))}, served as {adapter.get('lora_name')}" if adapter else "model owner has not uploaded yet (a run without one uses the base model)")
    )
    lines.append(f"  manifest {_short(status.get('manifest_sha256'), 16)}   both parties approve this exact hash")
    for party in (BENCHMARK_OWNER, MODEL_OWNER):
        lines.append(f"  [{'x' if approvals.get(party) else ' '}] {PRETTY[party]} approved")
    run = status.get("run")
    if run:
        lines.append(f"  run {run.get('run_id', '')[:12]}… {run.get('status')}, {run.get('completed', 0)}/{run.get('total', '?')} prompts")
    lines.append(f"Next: {next_step(status)}")
    return "\n".join(lines)


def approval_gate(status: dict, party: str, allow_without_adapter: bool = False) -> tuple[bool, str]:
    """Decide whether `party` should approve now. Returns (ok, message)."""
    run = status.get("run") or {}
    if run.get("status") == "running":
        return False, "A run is in progress; nothing to approve."
    missing = missing_uploads(status)
    if BENCHMARK_OWNER in missing:
        return False, "Nothing to approve yet: the benchmark owner has not uploaded a prompt set."
    if MODEL_OWNER in missing and not allow_without_adapter:
        return False, (
            "The model owner has not uploaded an adapter, so approving now would evaluate the base model. "
            "Wait for the upload (`dbe status` shows when it lands), or pass --without-adapter if that is what you want."
        )
    if (status.get("approvals") or {}).get(party):
        return False, f"You ({PRETTY[party]}) already approved this manifest. {next_step(status)}"
    return True, ""


# ----- dbe verify summary ----------------------------------------------------------------

PLATFORM_NAMES = {"sev-snp-guest": "AMD SEV-SNP", "tdx-guest": "Intel TDX"}


def platform_name(predicate_type: str | None) -> str:
    for key, name in PLATFORM_NAMES.items():
        if predicate_type and key in predicate_type:
            return name
    return predicate_type or "unknown platform"


def describe_policy(policy_source: str | None) -> str:
    if not policy_source:
        return "unknown"
    from harness.policy import OutputPolicy

    grants = OutputPolicy.parse(policy_source).grants
    who = lambda grant: [PRETTY[p] for p in (BENCHMARK_OWNER, MODEL_OWNER) if grant in grants.get(p, ())]  # noqa: E731
    results, receipt = who("results"), who("receipt")
    parts = [f"results \u2192 {' and '.join(results) if results else 'nobody'}"]
    parts.append("receipt \u2192 both parties" if len(receipt) == 2 else f"receipt \u2192 {' and '.join(receipt) if receipt else 'nobody'}")
    return ", ".join(parts)


def render_verify(record: dict, identity: dict, enclave: str, repo: str, tag: str | None) -> str:
    """Plain-language summary of a successful `dbe verify`."""
    enclave_quote = (record.get("measurements") or {}).get("enclave") or {}
    platform = platform_name(enclave_quote.get("type"))
    digest = record.get("digest") or ""
    tls = record.get("keys", {}).get("enclave") or ""
    base = identity.get("base_model") or {}
    parties = identity.get("parties") or {}
    release = f"{tag} of {repo}" if tag else f"latest release of {repo}"
    lines = [
        f"Verifying {enclave}",
        "",
        f"  \u2713 Release      {release}, measurement published to Sigstore (digest {_short(digest, 8)})",
        f"  \u2713 Hardware     {platform} attestation, checked against the vendor's certificate chain",
        "  \u2713 Code         the enclave's measurement matches that release",
        f"  \u2713 Connection   TLS key {_short(tls, 8)} pinned; every later command can only reach this enclave",
        "",
        f"  Model          {base.get('repo') or base.get('name') or '?'} (weights {_short(base.get('roothash'), 8)})",
        f"  Output policy  {describe_policy(identity.get('output_policy_source'))}",
        f"  Parties        benchmark owner {_short(parties.get(BENCHMARK_OWNER), 8)}, model owner {_short(parties.get(MODEL_OWNER), 8)}",
        "",
        "Verified. `dbe status` shows where the run stands; `dbe verify --full` prints every hash.",
    ]
    return "\n".join(lines)

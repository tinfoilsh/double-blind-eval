"""`dbe`: the party command line for a double-blind eval enclave."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from dbe.canonical import (
    generate_private_key,
    normalize_party,
    public_key_hex,
    read_private_key,
    sha256_hex,
    write_private_key,
)
from dbe.client import DBE_HOME, APIError, EnclaveClient, PinMismatch, Session, VerificationError, verify_enclave

DEFAULT_REPO = "tinfoilsh/double-blind-eval"


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _key_path(party: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return DBE_HOME / "keys" / f"{party}.key"


def _client(args) -> EnclaveClient:
    party = normalize_party(args.party) if args.party else None
    key = None
    if party:
        path = _key_path(party, args.key)
        if path.exists():
            key = read_private_key(path)
    if args.dev_url:
        print(f"warning: --dev-url bypasses attestation verification; never use it against a real run", file=sys.stderr)
        return EnclaveClient(party=party, key=key, dev_url=args.dev_url)
    if not args.enclave:
        raise SystemExit("--enclave (or DBE_ENCLAVE) is required")
    return EnclaveClient(session=Session.load(args.enclave), party=party, key=key)


# ----- commands -----------------------------------------------------------------------


def cmd_keygen(args) -> None:
    party = normalize_party(args.party)
    path = _key_path(party, args.out)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} exists; pass --force to overwrite")
    key = generate_private_key()
    write_private_key(path, key)
    print(f"wrote {path}")
    print(f"{party} public key: {public_key_hex(key.public_key())}")


def cmd_pubkey(args) -> None:
    party = normalize_party(args.party)
    key = read_private_key(_key_path(party, args.key))
    print(public_key_hex(key.public_key()))


def cmd_verify(args) -> None:
    if not args.enclave:
        raise SystemExit("--enclave (or DBE_ENCLAVE) is required")
    # A failed verification must not leave an older session behind: later commands would
    # silently pin to whatever enclave was verified last.
    stale = DBE_HOME / "sessions" / f"{args.enclave}.json"
    if stale.exists():
        stale.unlink()
    session = verify_enclave(args.enclave, args.repo)
    client = EnclaveClient(session=session)
    identity = client.identity()
    session.run_public_key = identity.get("run_public_key")
    session.identity = identity
    path = session.save()
    record = session.audit_record
    print(f"enclave        {session.enclave}")
    print(f"repo           {session.repo}")
    print(f"release digest {record.get('digest')}")
    sig = (record.get("measurements") or {}).get("sigstore") or {}
    enc = (record.get("measurements") or {}).get("enclave") or {}
    print(f"code measure   {sig.get('type')}")
    for reg in sig.get("registers") or []:
        print(f"               {reg}")
    print(f"enclave quote  {enc.get('type')}")
    print(f"tls key        {session.tls_public_key_sha256}")
    print(f"run key        {session.run_public_key}")
    print(f"config sha256  {identity.get('config_sha256')}")
    print(f"base model     {identity.get('base_model', {}).get('repo')} roothash={identity.get('base_model', {}).get('roothash')}")
    print(f"output policy  {identity.get('output_policy_source')}")
    print(f"status         {record.get('status')}   (session saved to {path})")


def cmd_identity(args) -> None:
    _out(_client(args).identity())


def cmd_status(args) -> None:
    _out(_client(args).status())


def cmd_healthz(args) -> None:
    _out(_client(args).healthz())


def cmd_model_upload(args) -> None:
    client = _client(args)
    if client.party != "model-owner":
        raise SystemExit("model upload must be run as --party model-owner")
    _out(client.upload_adapter(Path(args.path)))


def cmd_model_hash(args) -> None:
    from dbe.adapterhash import adapter_content_hash

    digest, files = adapter_content_hash(Path(args.path))
    print(digest)
    if args.verbose_files:
        for name, sha in files.items():
            print(f"  {sha}  {name}")


def cmd_benchmark_upload(args) -> None:
    client = _client(args)
    if client.party != "benchmark-owner":
        raise SystemExit("benchmark upload must be run as --party benchmark-owner")
    _out(client.upload_benchmark(Path(args.path)))


def cmd_manifest(args) -> None:
    _out(_client(args).manifest())


def cmd_approve(args) -> None:
    client = _client(args)
    if not client.key:
        raise SystemExit("approve needs a party key (--party and --key)")
    response, manifest = client.approve()
    print(f"approved manifest {manifest['manifest_sha256']} as {client.party}")
    print(f"approvals so far: {', '.join(response['approved_by'])}")
    print("run started" if response["run_started"] else "waiting for the other party")


def cmd_run(args) -> None:
    client = _client(args)
    while True:
        run = client.run()
        status = run.get("status")
        if status == "running":
            print(f"running: {run.get('completed', 0)}/{run.get('total', '?')} prompts", file=sys.stderr)
        if not args.wait or status in ("done", "failed", "collecting"):
            _out(run)
            return
        time.sleep(args.interval)


def cmd_results(args) -> None:
    client = _client(args)
    status, payload = client.results()
    if status == 202:
        print("run still in progress", file=sys.stderr)
        _out(payload)
        return
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2))
        print(f"wrote {args.out}")
    results = payload.get("results", [])
    run = payload.get("run", {})
    print(f"run {run.get('run_id')}: {len(results)} prompts, {sum(1 for r in results if r.get('error'))} failed")
    if payload.get("score") is not None:
        print(f"exact-match score: {payload['score']:.3f}")
    ttfts = [r["ttft_ms"] for r in results if r.get("ttft_ms") is not None]
    tps = [r["decode_tps"] for r in results if r.get("decode_tps") is not None]
    if ttfts:
        print(f"ttft ms: median {sorted(ttfts)[len(ttfts)//2]:.0f}  max {max(ttfts):.0f}")
    if tps:
        print(f"decode tok/s: median {sorted(tps)[len(tps)//2]:.1f}")
    if args.show:
        for r in results:
            print(f"--- {r.get('prompt_uid')} [{r.get('hazard') or '-'}] ttft={r.get('ttft_ms')}ms tps={r.get('decode_tps')}")
            print(r.get("completion") if r.get("completion") is not None else f"error: {r.get('error')}")


def cmd_receipt_get(args) -> None:
    receipt = _client(args).receipt()
    if args.out:
        Path(args.out).write_text(json.dumps(receipt, indent=2))
        print(f"wrote {args.out}")
    else:
        _out(receipt)


def _fetch_config_sha256(repo: str, tag: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{tag}/tinfoil-config.yml"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        return sha256_hex(resp.read())


def cmd_receipt_verify(args) -> None:
    from harness.receipt import verify_receipt  # local import: the client package stays dependency-light

    envelope = json.loads(Path(args.file).read_text())
    expected_run_key = None
    if args.enclave:
        try:
            expected_run_key = Session.load(args.enclave).run_public_key
        except VerificationError:
            pass
    problems = verify_receipt(envelope, expected_run_public_key=expected_run_key)
    body = envelope.get("receipt", {})
    identity = body.get("identity", {})
    if args.tag:
        try:
            expected_cfg = _fetch_config_sha256(args.repo, args.tag)
            if identity.get("config_sha256") != expected_cfg:
                problems.append(f"config_sha256 {str(identity.get('config_sha256'))[:16]}… does not match {args.repo}@{args.tag} ({expected_cfg[:16]}…)")
            else:
                print(f"config sha256 matches {args.repo}@{args.tag}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"could not fetch tinfoil-config.yml for {args.repo}@{args.tag}: {exc}")
    print(f"run          {body.get('run_id')}")
    print(f"manifest     {body.get('manifest_sha256')}")
    print(f"prompts      {body.get('prompt_count')} ({body.get('failed_prompts')} failed)")
    print(f"score        {body.get('score')}")
    print(f"base model   {identity.get('base_model', {}).get('repo')} roothash={identity.get('base_model', {}).get('roothash')}")
    print(f"adapter      {body.get('manifest', {}).get('adapter_sha256')}")
    print(f"benchmark    {body.get('manifest', {}).get('benchmark_sha256')}")
    print(f"run key      {body.get('run_public_key')}" + ("  (matches pinned identity)" if expected_run_key and expected_run_key == body.get("run_public_key") else ""))
    if problems:
        print("RECEIPT INVALID:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    print("RECEIPT OK: signature, both approvals and party keys verify")


# ----- parser -------------------------------------------------------------------------


def _common_options() -> argparse.ArgumentParser:
    """Options every command accepts, before or after the subcommand name.

    Defaults are SUPPRESS so a value given at one level is not clobbered by the other; the
    environment fallbacks are applied once in main().
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-e", "--enclave", default=argparse.SUPPRESS, help="enclave hostname (env DBE_ENCLAVE)")
    common.add_argument("-r", "--repo", default=argparse.SUPPRESS, help="config repo (env DBE_REPO)")
    common.add_argument("--party", default=argparse.SUPPRESS, help="benchmark-owner | model-owner (env DBE_PARTY)")
    common.add_argument("--key", default=argparse.SUPPRESS, help="party private key file (default ~/.dbe/keys/<party>.key)")
    common.add_argument("--dev-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(prog="dbe", description="Double-blind eval on Tinfoil Containers: party CLI", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(subparsers, name, **kwargs):
        return subparsers.add_parser(name, parents=[common], **kwargs)

    p = add(sub, "keygen", help="create a party keypair")
    p.add_argument("--out")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_keygen)

    add(sub, "pubkey", help="print the party public key").set_defaults(func=cmd_pubkey)
    add(sub, "verify", help="verify the enclave attestation and pin its TLS key").set_defaults(func=cmd_verify)
    add(sub, "identity", help="show the enclave's identity").set_defaults(func=cmd_identity)
    add(sub, "status", help="show asset and approval state (signed)").set_defaults(func=cmd_status)
    add(sub, "healthz", help="enclave health").set_defaults(func=cmd_healthz)

    model = add(sub, "model", help="model owner actions").add_subparsers(dest="model_command", required=True)
    p = add(model, "upload", help="upload a PEFT adapter directory or tar.gz")
    p.add_argument("path")
    p.set_defaults(func=cmd_model_upload)
    p = add(model, "hash", help="print the content hash the enclave will report for an adapter directory")
    p.add_argument("path")
    p.add_argument("--files", dest="verbose_files", action="store_true", help="also list per-file hashes")
    p.set_defaults(func=cmd_model_hash)

    bench = add(sub, "benchmark", help="benchmark owner actions").add_subparsers(dest="bench_command", required=True)
    p = add(bench, "upload", help="upload a prompt set (CSV or JSONL)")
    p.add_argument("path")
    p.set_defaults(func=cmd_benchmark_upload)

    add(sub, "manifest", help="show the run manifest both parties sign").set_defaults(func=cmd_manifest)
    add(sub, "approve", help="sign the current manifest; the run starts on the second approval").set_defaults(func=cmd_approve)

    p = add(sub, "run", help="show run progress")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=cmd_run)

    p = add(sub, "results", help="fetch results (policy-gated)")
    p.add_argument("--out")
    p.add_argument("--show", action="store_true", help="print completions")
    p.set_defaults(func=cmd_results)

    receipt = add(sub, "receipt", help="run receipts").add_subparsers(dest="receipt_command", required=True)
    p = add(receipt, "get")
    p.add_argument("--out")
    p.set_defaults(func=cmd_receipt_get)
    p = add(receipt, "verify", help="verify a receipt offline")
    p.add_argument("file")
    p.add_argument("--tag", help="release tag to compare config_sha256 against")
    p.set_defaults(func=cmd_receipt_verify)
    return parser


def resolve_options(args: argparse.Namespace) -> argparse.Namespace:
    """Apply environment fallbacks for the shared options, whichever position they were given in."""
    args.enclave = getattr(args, "enclave", None) or os.environ.get("DBE_ENCLAVE")
    args.repo = getattr(args, "repo", None) or os.environ.get("DBE_REPO", DEFAULT_REPO)
    args.party = getattr(args, "party", None) or os.environ.get("DBE_PARTY")
    args.key = getattr(args, "key", None) or os.environ.get("DBE_KEY")
    args.dev_url = getattr(args, "dev_url", None) or os.environ.get("DBE_DEV_URL")
    return args


def main(argv: list[str] | None = None) -> None:
    args = resolve_options(build_parser().parse_args(argv))
    try:
        args.func(args)
    except (APIError, VerificationError, PinMismatch, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

"""HTTP surface of the enclave harness. Only ``/api/*`` is reachable through the shim."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from dbe.adapterhash import adapter_content_hash
from dbe.canonical import (
    BENCHMARK_OWNER,
    MODEL_OWNER,
    PARTIES,
    approval_signing_input,
    load_public_key_hex,
    sha256_hex,
    verify,
)
from harness import __version__
from harness.auth import AuthError, RequestVerifier
from harness.benchmark import BenchmarkFormatError, parse_benchmark
from harness.identity import Identity, load_identity
from harness.policy import OutputPolicy
from harness.receipt import build_receipt
from harness.runner import DEFAULT_SAMPLING, VLLMClient, VLLMError, run_eval, score_results
from harness.state import AdapterAsset, BenchmarkAsset, RunState, StateError

log = logging.getLogger("dbe.harness")


@dataclass
class Settings:
    parties: dict[str, str]
    output_policy: str
    sampling: dict
    base_model: str = "gemma4-31b"
    lora_name: str = "private-adapter"
    vllm_url: str = "http://127.0.0.1:8001"
    state_dir: Path = Path("/run/dbe")
    max_adapter_bytes: int = 1536 * 1024 * 1024
    max_benchmark_bytes: int = 50 * 1024 * 1024
    concurrency: int = 4
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        parties = {
            BENCHMARK_OWNER: env.get("DBE_BENCHMARK_OWNER_PUBKEY", "").strip(),
            MODEL_OWNER: env.get("DBE_MODEL_OWNER_PUBKEY", "").strip(),
        }
        missing = [p for p, k in parties.items() if not k]
        if missing:
            raise RuntimeError(f"refusing to start: no public key configured for {missing}")
        for party, key in parties.items():
            load_public_key_hex(key)
        sampling = dict(DEFAULT_SAMPLING)
        if env.get("DBE_SAMPLING"):
            sampling = json.loads(env["DBE_SAMPLING"])
        return cls(
            parties=parties,
            output_policy=env.get("DBE_OUTPUT_POLICY", "bo:results+receipt,mo:receipt"),
            sampling=sampling,
            base_model=env.get("DBE_BASE_MODEL", "gemma4-31b"),
            lora_name=env.get("DBE_LORA_NAME", "private-adapter"),
            vllm_url=env.get("DBE_VLLM_URL", "http://127.0.0.1:8001"),
            state_dir=Path(env.get("DBE_STATE_DIR", "/run/dbe")),
            max_adapter_bytes=int(env.get("DBE_MAX_ADAPTER_BYTES", 1536 * 1024 * 1024)),
            max_benchmark_bytes=int(env.get("DBE_MAX_BENCHMARK_BYTES", 50 * 1024 * 1024)),
            concurrency=int(env.get("DBE_CONCURRENCY", 4)),
        )


def _safe_extract_adapter(data: bytes, dest: Path) -> Path:
    """Extract a tar/tar.gz of a PEFT adapter into dest, refusing anything but plain files."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise HTTPException(400, f"adapter must be a tar or tar.gz archive: {exc}") from exc
    with tar:
        members = tar.getmembers()
        names = []
        for m in members:
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.name.startswith("\\"):
                raise HTTPException(400, f"unsafe path in archive: {m.name!r}")
            if not (m.isfile() or m.isdir()):
                raise HTTPException(400, f"archive member {m.name!r} is not a regular file or directory")
            names.append(m.name)
        for m in members:
            target = (dest / m.name).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise HTTPException(400, f"unsafe path in archive: {m.name!r}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                assert src is not None
                with open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
    root = dest
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        root = entries[0]
    if not (root / "adapter_config.json").exists():
        raise HTTPException(400, "adapter archive has no adapter_config.json at its root")
    if not any(root.glob("adapter_model.*")):
        raise HTTPException(400, "adapter archive has no adapter_model.* weights file")
    return root


def create_app(settings: Settings, identity: Identity | None = None, vllm: VLLMClient | None = None) -> FastAPI:
    identity = identity or load_identity(harness_version=__version__)
    vllm = vllm or VLLMClient(settings.vllm_url)
    policy = OutputPolicy.parse(settings.output_policy)
    verifier = RequestVerifier.from_hex(settings.parties)
    party_keys = {p: load_public_key_hex(k) for p, k in settings.parties.items()}
    state = RunState(
        parties=dict(settings.parties),
        policy_source=policy.source,
        sampling=settings.sampling,
        base_model_name=settings.base_model,
    )
    lock = asyncio.Lock()
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="double-blind-eval harness", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.run_state = state
    app.state.identity = identity

    async def authenticate(request: Request) -> tuple[str, bytes]:
        body = await request.body()
        try:
            party = verifier.verify(request.method, request.url.path, body, request.headers)
        except AuthError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return party, body

    def require_party(party: str, wanted: str) -> None:
        if party != wanted:
            raise HTTPException(403, f"only the {wanted} may call this endpoint")

    @app.exception_handler(StateError)
    async def _state_error(_: Request, exc: StateError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    # ----- public -------------------------------------------------------------------
    @app.get("/api/healthz")
    async def healthz():
        ok = await vllm.healthy()
        payload = {"ok": ok, "vllm": ok, "phase": state.phase, "harness_version": __version__}
        return JSONResponse(payload, status_code=200 if ok else 503)

    @app.get("/api/identity")
    async def get_identity():
        return {
            **identity.public_dict(),
            "parties": dict(settings.parties),
            "output_policy": policy.as_dict(),
            "output_policy_source": policy.source,
            "sampling": settings.sampling,
            "base_model": {**identity.base_model.as_dict(), "served_name": settings.base_model},
            "phase": state.phase,
        }

    # ----- signed, either party ------------------------------------------------------
    @app.get("/api/status")
    async def get_status(request: Request):
        await authenticate(request)
        return state.public_status()

    @app.get("/api/manifest")
    async def get_manifest(request: Request):
        await authenticate(request)
        return {"manifest": state.manifest(), "manifest_sha256": state.manifest_sha256(), "ready": state.ready_to_run()}

    @app.get("/api/run")
    async def get_run(request: Request):
        await authenticate(request)
        return state.run.public_dict() if state.run else {"status": "collecting", "manifest_sha256": state.manifest_sha256()}

    # ----- uploads ------------------------------------------------------------------
    @app.put("/api/model/adapter")
    async def put_adapter(request: Request):
        party, body = await authenticate(request)
        require_party(party, MODEL_OWNER)
        if len(body) > settings.max_adapter_bytes:
            raise HTTPException(413, f"adapter exceeds {settings.max_adapter_bytes} bytes")
        if not body:
            raise HTTPException(400, "empty upload")
        upload_digest = sha256_hex(body)
        async with lock:
            state._require_mutable()
            dest = settings.state_dir / "adapters" / upload_digest[:16]
            root = await asyncio.to_thread(_safe_extract_adapter, body, dest)
            content_hash, files = await asyncio.to_thread(adapter_content_hash, root)
            await vllm.unload_adapter(settings.lora_name)
            try:
                await vllm.load_adapter(settings.lora_name, str(root))
            except VLLMError as exc:
                raise HTTPException(502, str(exc)) from exc
            state.set_adapter(
                AdapterAsset(
                    sha256=content_hash,
                    size=len(body),
                    lora_name=settings.lora_name,
                    path=str(root),
                    uploaded_at=time.time(),
                    upload_sha256=upload_digest,
                    file_count=len(files),
                )
            )
            log.info("adapter accepted content=%s upload=%s files=%d size=%d", content_hash[:16], upload_digest[:16], len(files), len(body))
            return {
                "adapter_sha256": content_hash,
                "upload_sha256": upload_digest,
                "files": len(files),
                "size": len(body),
                "lora_name": settings.lora_name,
                "manifest_sha256": state.manifest_sha256(),
            }

    @app.put("/api/benchmark")
    async def put_benchmark(request: Request):
        party, body = await authenticate(request)
        require_party(party, BENCHMARK_OWNER)
        if len(body) > settings.max_benchmark_bytes:
            raise HTTPException(413, f"benchmark exceeds {settings.max_benchmark_bytes} bytes")
        try:
            rows = parse_benchmark(body, request.headers.get("X-DBE-Filename"))
        except (BenchmarkFormatError, UnicodeDecodeError) as exc:
            raise HTTPException(400, f"benchmark not understood: {exc}") from exc
        digest = sha256_hex(body)
        async with lock:
            state.set_benchmark(BenchmarkAsset(sha256=digest, size=len(body), rows=rows, filename=request.headers.get("X-DBE-Filename"), uploaded_at=time.time()))
        log.info("benchmark accepted sha256=%s rows=%d", digest[:16], len(rows))
        return {"benchmark_sha256": digest, "count": len(rows), "scored": any(r.expected is not None for r in rows), "manifest_sha256": state.manifest_sha256()}

    # ----- approvals + run ----------------------------------------------------------
    async def execute_run() -> None:
        assert state.benchmark is not None
        run = state.begin_run()
        log.info("run %s started: %d prompts, model=%s", run.run_id, run.total, state.served_model())

        def progress(done: int, total: int) -> None:
            run.completed = done

        try:
            results = await run_eval(
                state.benchmark.rows,
                state.served_model(),
                state.sampling,
                vllm,
                concurrency=settings.concurrency,
                on_progress=progress,
            )
            state.results = results
            state.score = score_results(results)
            run.finished_at = time.time()
            run.status = "done"
            body = {
                "run_id": run.run_id,
                "manifest": state.manifest(),
                "manifest_sha256": run.manifest_sha256,
                "parties": dict(settings.parties),
                "approvals": {p: {"manifest_sha256": a.manifest_sha256, "signature": a.signature} for p, a in state.approvals.items()},
                "identity": {k: v for k, v in identity.public_dict().items() if k != "run_public_key"},
                "prompt_count": run.total,
                "completed": len(results),
                "failed_prompts": sum(1 for r in results if r.get("error")),
                "score": state.score,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            state.receipt = build_receipt(body, identity.run_key)
            log.info("run %s done: %d/%d prompts, %d failed", run.run_id, len(results), run.total, body["failed_prompts"])
        except Exception as exc:  # noqa: BLE001 - surface any failure in the run record
            run.finished_at = time.time()
            run.status = "failed"
            run.error = f"{exc.__class__.__name__}: {exc}"[:500]
            log.exception("run %s failed", run.run_id)

    @app.post("/api/approve")
    async def post_approve(request: Request):
        party, body = await authenticate(request)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        manifest_sha256 = payload.get("manifest_sha256")
        signature = payload.get("signature")
        if not isinstance(manifest_sha256, str) or not isinstance(signature, str):
            raise HTTPException(400, "body needs manifest_sha256 and signature")
        if not verify(party_keys[party], approval_signing_input(manifest_sha256), signature):
            raise HTTPException(400, "approval signature does not verify for this party")
        async with lock:
            both = state.approve(party, manifest_sha256, signature)
            if both:
                asyncio.create_task(execute_run())
        return {"approved_by": sorted(state.approvals), "run_started": both, "manifest_sha256": manifest_sha256}

    # ----- outputs ------------------------------------------------------------------
    @app.get("/api/results")
    async def get_results(request: Request):
        party, _ = await authenticate(request)
        if not policy.allows(party, "results"):
            raise HTTPException(403, f"output policy does not grant results to the {party}")
        if state.run is None:
            raise HTTPException(409, "no run yet")
        if state.run.status == "running":
            return JSONResponse({"detail": "run in progress", "run": state.run.public_dict()}, status_code=202)
        if state.run.status == "failed":
            raise HTTPException(409, f"run failed: {state.run.error}")
        return {"run": state.run.public_dict(), "score": state.score, "results": state.results, "receipt": state.receipt}

    @app.get("/api/receipt")
    async def get_receipt(request: Request):
        party, _ = await authenticate(request)
        if not policy.allows(party, "receipt"):
            raise HTTPException(403, f"output policy does not grant the receipt to the {party}")
        if state.receipt is None:
            raise HTTPException(409, "no completed run yet")
        return state.receipt

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return create_app(Settings.from_env())

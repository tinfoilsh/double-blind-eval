"""End-to-end flow against the FastAPI app with a fake vLLM."""

from __future__ import annotations

import json

from dbe.canonical import approval_signing_input, sign
from harness.receipt import verify_receipt
from tests.conftest import BENCH_CSV, BENCH_SCORED_JSONL, adapter_tarball, wait_for_run


def approve(party):
    manifest = party.get("/api/manifest").json()
    digest = manifest["manifest_sha256"]
    return party.post_json("/api/approve", {"manifest_sha256": digest, "signature": sign(party.key, approval_signing_input(digest))})


def test_public_endpoints(client):
    ident = client.get("/api/identity").json()
    assert ident["run_public_key"] and ident["config_sha256"] == "c" * 64
    assert ident["output_policy"] == {"benchmark-owner": ["receipt", "results"], "model-owner": ["receipt"]}
    assert ident["phase"] == "collecting"
    assert client.get("/api/healthz").status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/results").status_code == 401


def test_full_double_blind_flow(client, bo, mo, fake_vllm):
    # wrong party for each upload
    assert mo.put("/api/benchmark", BENCH_CSV).status_code == 403
    assert bo.put("/api/model/adapter", adapter_tarball()).status_code == 403

    r = bo.put("/api/benchmark", BENCH_CSV, **{"X-DBE-Filename": "prompts.csv"})
    assert r.status_code == 200, r.text
    bench = r.json()
    assert bench["count"] == 3 and bench["scored"] is False

    r = mo.put("/api/model/adapter", adapter_tarball())
    assert r.status_code == 200, r.text
    adapter = r.json()
    assert adapter["lora_name"] == "private-adapter" and adapter["files"] == 2
    assert fake_vllm.loaded["private-adapter"].endswith("/adapter")
    # the identity is a content hash: a differently packed archive of the same files hashes the same
    again = mo.put("/api/model/adapter", adapter_tarball(top_dir="renamed")).json()
    assert again["adapter_sha256"] == adapter["adapter_sha256"]
    assert again["upload_sha256"] != adapter["upload_sha256"]
    changed = mo.put("/api/model/adapter", adapter_tarball(extra_files={"adapter_model.safetensors": b"\1" * 64})).json()
    assert changed["adapter_sha256"] != adapter["adapter_sha256"]
    assert mo.put("/api/model/adapter", adapter_tarball()).json()["adapter_sha256"] == adapter["adapter_sha256"]

    manifest = bo.get("/api/manifest").json()
    assert manifest["manifest"]["adapter_sha256"] == adapter["adapter_sha256"]
    assert manifest["manifest"]["benchmark_sha256"] == bench["benchmark_sha256"]
    assert manifest["manifest"]["served_model"] == "private-adapter"
    assert mo.get("/api/manifest").json()["manifest_sha256"] == manifest["manifest_sha256"]

    # one approval does not start the run
    r = approve(bo)
    assert r.status_code == 200 and r.json()["run_started"] is False
    assert bo.get("/api/run").json()["status"] == "collecting"
    assert bo.get("/api/results").status_code == 409

    # the model owner cannot forge the benchmark owner's approval
    stale = bo.get("/api/manifest").json()["manifest_sha256"]
    bad = mo.post_json("/api/approve", {"manifest_sha256": stale, "signature": sign(bo.key, approval_signing_input(stale))})
    assert bad.status_code == 400

    r = approve(mo)
    assert r.status_code == 200 and r.json()["run_started"] is True
    run = wait_for_run(bo)
    assert run["status"] == "done", run
    assert run["completed"] == 3

    # output policy: BO gets results, MO gets only the receipt
    r = bo.get("/api/results")
    assert r.status_code == 200, r.text
    payload = r.json()
    completions = {row["prompt_uid"]: row["completion"] for row in payload["results"]}
    assert completions == {"p-001": "hello", "p-002": "1,2,3", "p-003": "blue"}
    assert all(row["ttft_ms"] is not None and row["output_tokens"] == 7 for row in payload["results"])
    assert payload["score"] is None
    assert mo.get("/api/results").status_code == 403
    receipt = mo.get("/api/receipt").json()
    assert receipt == payload["receipt"]

    # the receipt verifies under the identity's run key and binds both approvals
    run_key = client.get("/api/identity").json()["run_public_key"]
    assert verify_receipt(receipt) == []
    assert receipt["receipt"]["run_public_key"] == run_key
    body = receipt["receipt"]
    assert body["manifest_sha256"] == manifest["manifest_sha256"]
    assert set(body["approvals"]) == {"benchmark-owner", "model-owner"}
    assert body["identity"]["config_sha256"] == "c" * 64
    assert body["prompt_count"] == 3 and body["failed_prompts"] == 0
    dumped = json.dumps(body)
    assert "results" not in body and "hello" not in dumped and "Say hello" not in dumped

    # every generation used the served adapter and the measured sampling
    assert all(req["model"] == "private-adapter" and req["top_k"] == 40 and req["max_tokens"] == 100 for req in fake_vllm.requests)

    # re-uploading an asset after the run resets approvals and results
    r = bo.put("/api/benchmark", BENCH_SCORED_JSONL, **{"X-DBE-Filename": "scored.jsonl"})
    assert r.status_code == 200 and r.json()["scored"] is True
    status = bo.get("/api/status").json()
    assert status["approvals"] == {} and status["run"] is None and status["phase"] == "collecting"
    assert bo.get("/api/receipt").status_code == 409

    # approving a stale manifest hash is refused
    r = bo.post_json("/api/approve", {"manifest_sha256": manifest["manifest_sha256"], "signature": sign(bo.key, approval_signing_input(manifest["manifest_sha256"]))})
    assert r.status_code == 409

    # second round with a scored benchmark
    assert approve(mo).json()["run_started"] is False
    assert approve(bo).json()["run_started"] is True
    run = wait_for_run(bo)
    assert run["status"] == "done"
    payload = bo.get("/api/results").json()
    assert payload["score"] == 2 / 3
    assert payload["receipt"]["receipt"]["score"] == 2 / 3


def test_adapter_validation_and_vllm_refusal(client, bo, mo, fake_vllm):
    assert mo.put("/api/model/adapter", b"not a tarball").status_code == 400
    assert mo.put("/api/model/adapter", adapter_tarball(top_dir="../escape")).status_code == 400
    missing = adapter_tarball()
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("adapter/README.md")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"hi"))
    assert mo.put("/api/model/adapter", buf.getvalue()).status_code == 400
    fake_vllm.refuse_adapter = True
    r = mo.put("/api/model/adapter", missing)
    assert r.status_code == 502 and "rank" in r.json()["detail"]
    assert mo.get("/api/status").json()["adapter"] is None


def test_run_without_adapter_uses_base_model(client, bo, mo, fake_vllm):
    assert bo.put("/api/benchmark", BENCH_CSV).status_code == 200
    assert approve(mo).json()["run_started"] is False
    assert approve(bo).json()["run_started"] is True
    assert wait_for_run(bo)["status"] == "done"
    assert all(req["model"] == "gemma4-31b" for req in fake_vllm.requests)
    assert bo.get("/api/results").json()["receipt"]["receipt"]["manifest"]["adapter_sha256"] is None


def test_uploads_blocked_while_running(client, bo, mo, fake_vllm, monkeypatch):
    import asyncio

    from harness import runner

    original = runner.VLLMClient.generate

    async def slow(self, *args, **kwargs):
        await asyncio.sleep(0.3)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(runner.VLLMClient, "generate", slow)
    assert bo.put("/api/benchmark", BENCH_CSV).status_code == 200
    approve(mo)
    approve(bo)
    assert bo.get("/api/results").status_code == 202
    assert bo.put("/api/benchmark", BENCH_CSV).status_code == 409
    assert approve(bo).status_code == 409
    assert wait_for_run(bo)["status"] == "done"

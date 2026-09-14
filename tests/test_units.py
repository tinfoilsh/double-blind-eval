from __future__ import annotations

import json
import time

import httpx
import pytest

from dbe.canonical import (
    BENCHMARK_OWNER,
    MODEL_OWNER,
    approval_signing_input,
    generate_private_key,
    load_public_key_hex,
    normalize_party,
    public_key_hex,
    request_signing_input,
    sign,
    verify,
)
from harness.auth import AuthError, RequestVerifier
from harness.benchmark import BenchmarkFormatError, parse_benchmark
from harness.policy import OutputPolicy
from harness.receipt import build_receipt, verify_receipt
from harness.runner import VLLMClient, exact_match, normalize_answer
from tests.conftest import BENCH_CSV, BENCH_SCORED_JSONL, sse


def test_sign_verify_roundtrip_and_domains():
    key = generate_private_key()
    pub = load_public_key_hex(public_key_hex(key.public_key()))
    msg = request_signing_input("put", "/api/benchmark", "ab" * 32, "1700000000", "0123abcd")
    assert msg.split(b"\n")[0] == b"dbe-request-v1"
    assert msg.split(b"\n")[1] == b"PUT"
    assert msg.split(b"\n")[-1] == b"0123abcd"
    sig = sign(key, msg)
    assert verify(pub, msg, sig)
    assert not verify(pub, msg + b"x", sig)
    assert not verify(pub, approval_signing_input("ab" * 32), sig)
    assert normalize_party("BO") == BENCHMARK_OWNER and normalize_party("model-owner") == MODEL_OWNER
    with pytest.raises(ValueError):
        normalize_party("operator")


def test_request_verifier_rejects_bad_and_replayed_requests():
    bo, mo = generate_private_key(), generate_private_key()
    verifier = RequestVerifier.from_hex({"bo": public_key_hex(bo.public_key()), "mo": public_key_hex(mo.public_key())})
    body = b"hello"
    ts = str(int(time.time()))
    from dbe.canonical import sha256_hex

    nonce = "00ff00ff00ff00ff"
    good = {"X-DBE-Party": "benchmark-owner", "X-DBE-Timestamp": ts, "X-DBE-Nonce": nonce, "X-DBE-Signature": sign(bo, request_signing_input("PUT", "/api/benchmark", sha256_hex(body), ts, nonce))}
    assert verifier.verify("PUT", "/api/benchmark", body, good) == BENCHMARK_OWNER
    with pytest.raises(AuthError) as exc:
        verifier.verify("PUT", "/api/benchmark", body, good)
    assert "replayed" in exc.value.detail
    wrong_party = {**good, "X-DBE-Party": "model-owner"}
    with pytest.raises(AuthError):
        verifier.verify("PUT", "/api/benchmark", body, wrong_party)
    with pytest.raises(AuthError):
        verifier.verify("PUT", "/api/benchmark", body, {**good, "X-DBE-Nonce": "zz"})
    stale_ts = str(int(time.time()) - 3600)
    stale = {"X-DBE-Party": "benchmark-owner", "X-DBE-Timestamp": stale_ts, "X-DBE-Nonce": nonce, "X-DBE-Signature": sign(bo, request_signing_input("PUT", "/api/benchmark", sha256_hex(body), stale_ts, nonce))}
    with pytest.raises(AuthError) as exc:
        verifier.verify("PUT", "/api/benchmark", body, stale)
    assert "window" in exc.value.detail
    with pytest.raises(AuthError):
        verifier.verify("PUT", "/api/benchmark", body, {})
    with pytest.raises(AuthError):
        verifier.verify("PUT", "/api/other", body, good)


def test_benchmark_parsing():
    rows = parse_benchmark(BENCH_CSV, "prompts.csv")
    assert [r.prompt_uid for r in rows] == ["p-001", "p-002", "p-003"]
    assert rows[0].hazard == "benign" and rows[0].locale == "en_US" and rows[0].expected is None
    scored = parse_benchmark(BENCH_SCORED_JSONL, "scored.jsonl")
    assert scored[0].expected == "paris" and scored[0].prompt_uid == "s-1"
    with pytest.raises(BenchmarkFormatError):
        parse_benchmark(b"prompt_uid,hazard\na,b\n")
    with pytest.raises(BenchmarkFormatError):
        parse_benchmark(b"prompt_uid,prompt_text\nx,one\nx,two\n")
    with pytest.raises(BenchmarkFormatError):
        parse_benchmark(b"   ")


def test_policy_parsing():
    policy = OutputPolicy.parse("bo:results+receipt,mo:receipt")
    assert policy.allows("benchmark-owner", "results") and not policy.allows("model-owner", "results")
    assert policy.allows("mo", "receipt")
    assert OutputPolicy.parse("bo:results").as_dict() == {"benchmark-owner": ["results"], "model-owner": []}
    with pytest.raises(ValueError):
        OutputPolicy.parse("bo:prompts")
    with pytest.raises(ValueError):
        OutputPolicy.parse("operator:results")


def test_receipt_roundtrip_and_tamper_detection():
    run_key, bo, mo = generate_private_key(), generate_private_key(), generate_private_key()
    manifest_sha = "ab" * 32
    body = {
        "run_id": "r1",
        "manifest_sha256": manifest_sha,
        "parties": {"benchmark-owner": public_key_hex(bo.public_key()), "model-owner": public_key_hex(mo.public_key())},
        "approvals": {
            "benchmark-owner": {"manifest_sha256": manifest_sha, "signature": sign(bo, approval_signing_input(manifest_sha))},
            "model-owner": {"manifest_sha256": manifest_sha, "signature": sign(mo, approval_signing_input(manifest_sha))},
        },
        "score": 0.5,
    }
    envelope = build_receipt(body, run_key)
    assert verify_receipt(envelope, public_key_hex(run_key.public_key())) == []
    tampered = {**envelope, "receipt": {**envelope["receipt"], "score": 1.0}}
    assert any("signature" in p for p in verify_receipt(tampered))
    assert any("pinned" in p for p in verify_receipt(envelope, "00" * 32))
    missing = {**envelope, "receipt": {**envelope["receipt"], "approvals": {k: v for k, v in body["approvals"].items() if k != "model-owner"}}}
    problems = verify_receipt(missing)
    assert any("model-owner" in p for p in problems)


def test_pack_adapter_is_deterministic_and_matches_content_hash(tmp_path):
    import io
    import tarfile

    from dbe.adapterhash import adapter_content_hash
    from dbe.client import pack_adapter

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"r": 8}')
    (adapter / "adapter_model.safetensors").write_bytes(b"\0" * 32)
    first, second = pack_adapter(adapter), pack_adapter(adapter)
    assert first == second
    digest, files = adapter_content_hash(adapter)
    assert set(files) == {"adapter_config.json", "adapter_model.safetensors"}
    extracted = tmp_path / "out"
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as tar:
        tar.extractall(extracted, filter="data")
    assert adapter_content_hash(extracted)[0] == digest


def test_normalize_and_exact_match():
    assert normalize_answer("  Paris. ") == "paris"
    assert exact_match("The answer is 4", "the  answer is 4!")
    assert not exact_match("4", "5")


async def test_generation_timing_and_tokens():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True and body["top_k"] == 40 and body["max_tokens"] == 100
        assert body["stream_options"] == {"include_usage": True}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(["Hel", "lo ", "there"], usage_tokens=5))

    vllm = VLLMClient("http://vllm", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    gen = await vllm.generate("gemma4-31b", "hi", {"max_tokens": 100, "temperature": 0.8, "top_k": 40})
    assert gen.completion == "Hello there"
    assert gen.output_tokens == 5
    assert gen.ttft_ms is not None and gen.ttft_ms >= 0
    assert gen.finish_reason == "stop"


async def test_generation_error_surfaces():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="model not found")

    vllm = VLLMClient("http://vllm", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    from harness.runner import VLLMError

    with pytest.raises(VLLMError):
        await vllm.generate("nope", "hi", {})

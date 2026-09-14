from __future__ import annotations

import io
import json
import tarfile
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dbe.canonical import (
    BENCHMARK_OWNER,
    MODEL_OWNER,
    HEADER_NONCE,
    HEADER_PARTY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    generate_private_key,
    new_nonce,
    public_key_hex,
    request_signing_input,
    sha256_hex,
    sign,
)
from harness.app import Settings, create_app
from harness.identity import Identity
from harness.runner import VLLMClient


def sse(chunks: list[str], usage_tokens: int | None = None) -> bytes:
    lines = []
    for chunk in chunks:
        payload = {"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]}
        lines.append("data: " + json.dumps(payload))
    lines.append("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    if usage_tokens is not None:
        lines.append("data: " + json.dumps({"choices": [], "usage": {"completion_tokens": usage_tokens}}))
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode()


class FakeVLLM:
    """Minimal stand-in for the vLLM OpenAI server, driven through httpx.MockTransport."""

    def __init__(self):
        self.loaded: dict[str, str] = {}
        self.requests: list[dict] = []
        self.refuse_adapter = False
        self.healthy = True

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200 if self.healthy else 503)
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gemma4-31b"}] + [{"id": n} for n in self.loaded]})
        if path == "/v1/load_lora_adapter":
            body = json.loads(request.content)
            if self.refuse_adapter:
                return httpx.Response(400, text="Loading lora private-adapter failed: rank too large")
            self.loaded[body["lora_name"]] = body["lora_path"]
            return httpx.Response(200, text="Success")
        if path == "/v1/unload_lora_adapter":
            body = json.loads(request.content)
            self.loaded.pop(body["lora_name"], None)
            return httpx.Response(200, text="Success")
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.requests.append(body)
            prompt = body["messages"][0]["content"]
            answer = prompt.split("answer=", 1)[1].split()[0] if "answer=" in prompt else f"echo:{prompt}"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse([answer[: max(1, len(answer) // 2)], answer[max(1, len(answer) // 2) :]], usage_tokens=7))
        return httpx.Response(404)


@pytest.fixture
def party_keys():
    return {BENCHMARK_OWNER: generate_private_key(), MODEL_OWNER: generate_private_key()}


@pytest.fixture
def fake_vllm():
    return FakeVLLM()


@pytest.fixture
def identity():
    return Identity(config_sha256="c" * 64, image="ghcr.io/tinfoilsh/double-blind-eval@sha256:abc", cvm_version="0.14.7")


@pytest.fixture
def app(party_keys, fake_vllm, identity, tmp_path):
    settings = Settings(
        parties={p: public_key_hex(k.public_key()) for p, k in party_keys.items()},
        output_policy="bo:results+receipt,mo:receipt",
        sampling={"max_tokens": 100, "temperature": 0.8, "top_k": 40},
        state_dir=tmp_path / "state",
        concurrency=2,
    )
    vllm = VLLMClient("http://vllm", client=httpx.AsyncClient(transport=httpx.MockTransport(fake_vllm.handle)))
    return create_app(settings, identity=identity, vllm=vllm)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class Party:
    def __init__(self, client: TestClient, name: str, key):
        self.client = client
        self.name = name
        self.key = key

    def headers(self, method: str, path: str, body: bytes = b"", timestamp: int | None = None) -> dict:
        ts = str(timestamp if timestamp is not None else int(time.time()))
        nonce = new_nonce()
        return {
            HEADER_PARTY: self.name,
            HEADER_TIMESTAMP: ts,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: sign(self.key, request_signing_input(method, path, sha256_hex(body), ts, nonce)),
        }

    def get(self, path: str):
        return self.client.get(path, headers=self.headers("GET", path))

    def put(self, path: str, body: bytes, **extra):
        return self.client.put(path, content=body, headers={**self.headers("PUT", path, body), **extra})

    def post_json(self, path: str, obj: dict):
        body = json.dumps(obj).encode()
        return self.client.post(path, content=body, headers={**self.headers("POST", path, body), "Content-Type": "application/json"})


@pytest.fixture
def bo(client, party_keys):
    return Party(client, BENCHMARK_OWNER, party_keys[BENCHMARK_OWNER])


@pytest.fixture
def mo(client, party_keys):
    return Party(client, MODEL_OWNER, party_keys[MODEL_OWNER])


def adapter_tarball(extra_files: dict[str, bytes] | None = None, top_dir: str | None = "adapter") -> bytes:
    files = {"adapter_config.json": json.dumps({"peft_type": "LORA", "r": 8}).encode(), "adapter_model.safetensors": b"\0" * 64}
    files.update(extra_files or {})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=f"{top_dir}/{name}" if top_dir else name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


BENCH_CSV = b"""prompt_uid,hazard,locale,prompt_text
p-001,benign,en_US,"Say hello answer=hello"
p-002,benign,en_US,"Count to three answer=1,2,3"
p-003,benign,en_US,"What colour is the sky? answer=blue"
"""

BENCH_SCORED_JSONL = b"""{"id": "s-1", "prompt": "capital of France answer=Paris", "expected": "paris"}
{"id": "s-2", "prompt": "2+2 answer=4", "expected": "4"}
{"id": "s-3", "prompt": "colour answer=blue", "expected": "red"}
"""


def wait_for_run(party: Party, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = party.get("/api/run").json()
        if run.get("status") in ("done", "failed"):
            return run
        time.sleep(0.05)
    raise AssertionError(f"run did not finish: {run}")

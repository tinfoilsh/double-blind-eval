"""Exercise the real `dbe` CLI against the real harness with a fake vLLM. Not collected by pytest."""
import json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.app import Settings, create_app
from harness.identity import Identity

FAKE_PORT, HARNESS_PORT = 8011, 8010
home = Path(tempfile.mkdtemp(prefix="dbe-home-"))
env = {**os.environ, "DBE_HOME": str(home)}

def dbe(*args, party=None, check=True):
    cmd = ["dbe", "--dev-url", f"http://127.0.0.1:{HARNESS_PORT}"] + (["--party", party] if party else []) + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc

bo_pub = dbe("keygen", party="benchmark-owner").stdout.split("public key: ")[1].strip()
mo_pub = dbe("keygen", party="model-owner").stdout.split("public key: ")[1].strip()

fake = FastAPI()
loaded = {}
@fake.get("/health")
async def health(): return PlainTextResponse("ok")
@fake.post("/v1/load_lora_adapter")
async def load(req: Request):
    body = await req.json(); loaded[body["lora_name"]] = body["lora_path"]; return PlainTextResponse("Success")
@fake.post("/v1/unload_lora_adapter")
async def unload(req: Request): return PlainTextResponse("Success")
@fake.post("/v1/chat/completions")
async def chat(req: Request):
    await req.json()
    def gen():
        for piece in ("The answer ", "is 42."):
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}) + "\n"
            time.sleep(0.01)
        yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n"
        yield "data: " + json.dumps({"choices": [], "usage": {"completion_tokens": 5}}) + "\n"
        yield "data: [DONE]\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.2); return
        except httpx.HTTPError:
            time.sleep(0.05)

serve(fake, FAKE_PORT)
settings = Settings(parties={"benchmark-owner": bo_pub, "model-owner": mo_pub}, output_policy="bo:results+receipt,mo:receipt",
                    sampling={"max_tokens": 100, "temperature": 0.8, "top_k": 40}, vllm_url=f"http://127.0.0.1:{FAKE_PORT}", state_dir=home / "state")
serve(create_app(settings, identity=Identity(config_sha256="c" * 64)), HARNESS_PORT)

adapter = home / "adapter"; adapter.mkdir()
(adapter / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA", "r": 8}))
(adapter / "adapter_model.safetensors").write_bytes(b"\0" * 128)

print(dbe("healthz").stdout.strip())
print(dbe("identity").stdout[:160].replace("\n", " "), "...")
print(dbe("model", "upload", str(adapter), party="model-owner").stdout.strip())
print(dbe("benchmark", "upload", "bench/sample_prompts.csv", party="benchmark-owner").stdout.strip())
bad = dbe("results", party="benchmark-owner", check=False); assert bad.returncode == 1 and "409" in bad.stderr, bad.stderr
print(dbe("approve", party="model-owner").stdout.strip())
print(dbe("approve", party="benchmark-owner").stdout.strip())
print(dbe("run", "--wait", "--interval", "0.2", party="benchmark-owner").stdout.strip()[:200])
out = home / "results.json"
print(dbe("results", "--out", str(out), party="benchmark-owner").stdout.strip())
denied = dbe("results", party="model-owner", check=False); assert denied.returncode == 1 and "403" in denied.stderr, denied.stderr
print("model-owner results denied:", denied.stderr.strip())
rcpt = home / "receipt.json"
print(dbe("receipt", "get", "--out", str(rcpt), party="model-owner").stdout.strip())
print(dbe("receipt", "verify", str(rcpt)).stdout.strip())
res = json.loads(out.read_text())
assert res["results"][0]["completion"] == "The answer is 42." and res["results"][0]["output_tokens"] == 5
assert "sample-001" in {r["prompt_uid"] for r in res["results"]}
print("SMOKE OK")

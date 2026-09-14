"""Talks to the vLLM server on localhost and runs the prompt set.

Per prompt it records what OpenMined's job recorded: the completion, time to
first token and decode tokens per second. If the benchmark carries an
``expected`` column an exact-match score is computed as well.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from harness.benchmark import PromptRow

DEFAULT_SAMPLING = {"max_tokens": 100, "temperature": 0.8, "top_k": 40}
SAMPLING_KEYS = ("max_tokens", "temperature", "top_k", "top_p", "seed", "repetition_penalty")


class VLLMError(Exception):
    pass


@dataclass
class Generation:
    completion: str
    reasoning: str
    ttft_ms: float | None
    decode_tps: float | None
    output_tokens: int
    total_ms: float
    finish_reason: str | None


class VLLMClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8001", client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        return self._client or httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0))

    async def _close(self, http: httpx.AsyncClient) -> None:
        if http is not self._client:
            await http.aclose()

    async def healthy(self) -> bool:
        http = self._http()
        try:
            resp = await http.get(f"{self.base_url}/health", timeout=20.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
        finally:
            await self._close(http)

    async def models(self) -> list[str]:
        http = self._http()
        try:
            resp = await http.get(f"{self.base_url}/v1/models", timeout=10.0)
            resp.raise_for_status()
            return [m.get("id") for m in resp.json().get("data", [])]
        finally:
            await self._close(http)

    async def unload_adapter(self, lora_name: str) -> None:
        http = self._http()
        try:
            await http.post(f"{self.base_url}/v1/unload_lora_adapter", json={"lora_name": lora_name}, timeout=60.0)
        except httpx.HTTPError:
            pass
        finally:
            await self._close(http)

    async def load_adapter(self, lora_name: str, lora_path: str) -> None:
        http = self._http()
        try:
            resp = await http.post(
                f"{self.base_url}/v1/load_lora_adapter",
                json={"lora_name": lora_name, "lora_path": lora_path},
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            raise VLLMError(f"vLLM unreachable while loading adapter: {exc.__class__.__name__}") from exc
        finally:
            await self._close(http)
        if resp.status_code != 200:
            raise VLLMError(f"vLLM refused the adapter (HTTP {resp.status_code}): {resp.text[:300]}")

    async def generate(self, model: str, prompt: str, sampling: dict) -> Generation:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        for key in SAMPLING_KEYS:
            if key in sampling and sampling[key] is not None:
                body[key] = sampling[key]
        http = self._http()
        chunks: list[str] = []
        reasoning_chunks: list[str] = []
        first: float | None = None
        last: float | None = None
        deltas = 0
        usage_tokens: int | None = None
        finish_reason: str | None = None
        t0 = time.monotonic()
        try:
            async with http.stream("POST", f"{self.base_url}/v1/chat/completions", json=body) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise VLLMError(f"vLLM returned HTTP {resp.status_code}: {text[:300]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("usage")
                    if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                        usage_tokens = int(usage["completion_tokens"])
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if content or reasoning:
                            now = time.monotonic()
                            if first is None:
                                first = now
                            last = now
                            deltas += 1
                        if content:
                            chunks.append(content)
                        if reasoning:
                            reasoning_chunks.append(reasoning)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.HTTPError as exc:
            raise VLLMError(f"vLLM stream failed: {exc.__class__.__name__}") from exc
        finally:
            await self._close(http)
        t1 = time.monotonic()
        tokens = usage_tokens if usage_tokens is not None else deltas
        decode_tps = None
        if first is not None and last is not None and tokens > 1 and last > first:
            decode_tps = (tokens - 1) / (last - first)
        return Generation(
            completion="".join(chunks),
            reasoning="".join(reasoning_chunks),
            ttft_ms=(first - t0) * 1000.0 if first is not None else None,
            decode_tps=decode_tps,
            output_tokens=tokens,
            total_ms=(t1 - t0) * 1000.0,
            finish_reason=finish_reason,
        )


_WS = re.compile(r"\s+")


def normalize_answer(text: str) -> str:
    text = _WS.sub(" ", text.strip().casefold())
    return text.rstrip(" .!?:;,")


def exact_match(completion: str, expected: str) -> bool:
    return normalize_answer(completion) == normalize_answer(expected)


async def run_eval(
    rows: list[PromptRow],
    model: str,
    sampling: dict,
    vllm: VLLMClient,
    concurrency: int = 4,
    on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
) -> list[dict]:
    results: list[dict | None] = [None] * len(rows)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def one(index: int, row: PromptRow) -> None:
        nonlocal done
        async with semaphore:
            record = {
                **row.public_fields(),
                "prompt": row.prompt,
                "completion": None,
                "ttft_ms": None,
                "decode_tps": None,
                "output_tokens": None,
                "total_ms": None,
                "finish_reason": None,
                "error": None,
            }
            try:
                gen = await vllm.generate(model, row.prompt, sampling)
                record.update(
                    completion=gen.completion,
                    reasoning=gen.reasoning or None,
                    ttft_ms=round(gen.ttft_ms, 1) if gen.ttft_ms is not None else None,
                    decode_tps=round(gen.decode_tps, 2) if gen.decode_tps is not None else None,
                    output_tokens=gen.output_tokens,
                    total_ms=round(gen.total_ms, 1),
                    finish_reason=gen.finish_reason,
                )
            except VLLMError as exc:
                record["error"] = str(exc)[:300]
            if row.expected is not None:
                record["expected"] = row.expected
                record["correct"] = bool(record["completion"]) and exact_match(record["completion"], row.expected)
            results[index] = record
            done += 1
            if on_progress is not None:
                maybe = on_progress(done, len(rows))
                if asyncio.iscoroutine(maybe):
                    await maybe

    await asyncio.gather(*(one(i, r) for i, r in enumerate(rows)))
    return [r for r in results if r is not None]


def score_results(results: list[dict]) -> float | None:
    scored = [r for r in results if "correct" in r]
    if not scored:
        return None
    return sum(1 for r in scored if r["correct"]) / len(scored)

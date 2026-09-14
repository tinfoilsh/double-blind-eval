"""Run state machine.

    collecting --(both approvals on the same manifest)--> running --> done | failed

Uploading either asset while collecting or after a finished run recomputes the
manifest and drops every approval, so consent is always for exactly the assets
that will run.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from dbe.canonical import PARTIES, canonical_json, sha256_hex
from harness.benchmark import PromptRow


class StateError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class AdapterAsset:
    sha256: str  # content hash of the extracted files (see dbe.adapterhash)
    size: int
    lora_name: str
    path: str
    uploaded_at: float
    upload_sha256: str = ""  # sha256 of the archive bytes as uploaded
    file_count: int = 0


@dataclass
class BenchmarkAsset:
    sha256: str
    size: int
    rows: list[PromptRow]
    filename: str | None
    uploaded_at: float

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def has_expected(self) -> bool:
        return any(r.expected is not None for r in self.rows)


@dataclass
class Approval:
    manifest_sha256: str
    signature: str
    at: float


@dataclass
class RunRecord:
    run_id: str
    status: str = "running"  # running | done | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    completed: int = 0
    total: int = 0
    error: str | None = None
    manifest_sha256: str = ""

    def public_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "completed": self.completed,
            "total": self.total,
            "error": self.error,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass
class RunState:
    parties: dict[str, str]
    policy_source: str
    sampling: dict
    base_model_name: str
    adapter: AdapterAsset | None = None
    benchmark: BenchmarkAsset | None = None
    approvals: dict[str, Approval] = field(default_factory=dict)
    run: RunRecord | None = None
    results: list[dict] = field(default_factory=list)
    receipt: dict | None = None
    score: float | None = None

    # ----- phases -------------------------------------------------------------------
    @property
    def phase(self) -> str:
        if self.run is None:
            return "collecting"
        return self.run.status

    def _require_mutable(self) -> None:
        if self.run is not None and self.run.status == "running":
            raise StateError(409, "a run is in progress; assets cannot change")

    def _invalidate(self) -> None:
        self.approvals.clear()
        if self.run is not None and self.run.status != "running":
            self.run = None
            self.results = []
            self.receipt = None
            self.score = None

    # ----- assets -------------------------------------------------------------------
    def set_adapter(self, asset: AdapterAsset) -> None:
        self._require_mutable()
        self.adapter = asset
        self._invalidate()

    def set_benchmark(self, asset: BenchmarkAsset) -> None:
        self._require_mutable()
        self.benchmark = asset
        self._invalidate()

    # ----- manifest -----------------------------------------------------------------
    def served_model(self) -> str:
        return self.adapter.lora_name if self.adapter else self.base_model_name

    def manifest(self) -> dict:
        return {
            "version": "dbe-manifest-v1",
            "parties": dict(self.parties),
            "output_policy": self.policy_source,
            "sampling": self.sampling,
            "base_model": self.base_model_name,
            "served_model": self.served_model(),
            "adapter_sha256": self.adapter.sha256 if self.adapter else None,
            "benchmark_sha256": self.benchmark.sha256 if self.benchmark else None,
            "prompt_count": self.benchmark.count if self.benchmark else 0,
            "scored": bool(self.benchmark and self.benchmark.has_expected),
        }

    def manifest_sha256(self) -> str:
        return sha256_hex(canonical_json(self.manifest()))

    def ready_to_run(self) -> bool:
        return self.benchmark is not None

    # ----- approvals ----------------------------------------------------------------
    def approve(self, party: str, manifest_sha256: str, signature: str) -> bool:
        """Record an approval. Returns True when both parties have approved the current manifest."""
        if party not in PARTIES:
            raise StateError(403, f"{party} is not a party")
        if self.run is not None and self.run.status == "running":
            raise StateError(409, "a run is already in progress")
        if not self.ready_to_run():
            raise StateError(409, "no benchmark uploaded yet")
        current = self.manifest_sha256()
        if manifest_sha256 != current:
            raise StateError(409, f"approval names manifest {manifest_sha256[:12]}… but the current manifest is {current[:12]}…")
        if self.run is not None:
            # a finished run exists for an older approval set; a fresh approval round starts over
            self.run = None
            self.results = []
            self.receipt = None
            self.score = None
        self.approvals[party] = Approval(manifest_sha256=manifest_sha256, signature=signature, at=time.time())
        return all(p in self.approvals and self.approvals[p].manifest_sha256 == current for p in PARTIES)

    def approvals_dict(self) -> dict:
        return {p: {"manifest_sha256": a.manifest_sha256, "signature": a.signature, "at": a.at} for p, a in self.approvals.items()}

    # ----- run ----------------------------------------------------------------------
    def begin_run(self) -> RunRecord:
        assert self.benchmark is not None
        self.run = RunRecord(run_id=uuid.uuid4().hex, total=self.benchmark.count, manifest_sha256=self.manifest_sha256())
        self.results = []
        self.receipt = None
        self.score = None
        return self.run

    def public_status(self) -> dict:
        return {
            "phase": self.phase,
            "adapter": {"sha256": self.adapter.sha256, "size": self.adapter.size, "lora_name": self.adapter.lora_name} if self.adapter else None,
            "benchmark": {"sha256": self.benchmark.sha256, "size": self.benchmark.size, "count": self.benchmark.count, "scored": self.benchmark.has_expected} if self.benchmark else None,
            "manifest_sha256": self.manifest_sha256(),
            "approvals": {p: a.manifest_sha256 == self.manifest_sha256() for p, a in self.approvals.items()},
            "run": self.run.public_dict() if self.run else None,
        }

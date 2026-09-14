"""Benchmark file parsing.

Accepts the MLCommons AILuminate prompt-set CSV shape used in OpenMined's demo
(``prompt_uid, hazard, locale, prompt_text``) as well as JSONL rows with
``prompt``/``prompt_text`` and optional ``expected``. Only the fields the run
needs are kept; the raw upload bytes are what gets committed to in the manifest.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass

MAX_ROWS = 5000
PROMPT_KEYS = ("prompt_text", "prompt", "input", "question")
ID_KEYS = ("prompt_uid", "prompt_id", "id", "uid")


@dataclass(frozen=True)
class PromptRow:
    prompt_uid: str
    prompt: str
    hazard: str | None = None
    locale: str | None = None
    expected: str | None = None

    def public_fields(self) -> dict:
        return {"prompt_uid": self.prompt_uid, "hazard": self.hazard, "locale": self.locale}


class BenchmarkFormatError(ValueError):
    pass


def _pick(row: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return None


def _row_from_mapping(index: int, row: dict) -> PromptRow:
    prompt = _pick(row, PROMPT_KEYS)
    if prompt is None:
        raise BenchmarkFormatError(f"row {index}: no prompt column (expected one of {PROMPT_KEYS})")
    uid = _pick(row, ID_KEYS) or f"row-{index:05d}"
    expected = row.get("expected")
    return PromptRow(
        prompt_uid=uid,
        prompt=prompt,
        hazard=_pick(row, ("hazard",)),
        locale=_pick(row, ("locale",)),
        expected=str(expected) if expected not in (None, "") else None,
    )


def parse_benchmark(data: bytes, filename: str | None = None) -> list[PromptRow]:
    text = data.decode("utf-8-sig")
    stripped = text.lstrip()
    if not stripped:
        raise BenchmarkFormatError("benchmark file is empty")
    rows: list[PromptRow] = []
    looks_jsonl = stripped.startswith("{") or (filename or "").endswith((".jsonl", ".json"))
    if looks_jsonl:
        for index, line in enumerate(filter(None, (l.strip() for l in text.splitlines()))):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkFormatError(f"line {index}: invalid JSON ({exc.msg})") from exc
            if not isinstance(obj, dict):
                raise BenchmarkFormatError(f"line {index}: expected a JSON object")
            rows.append(_row_from_mapping(index, obj))
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise BenchmarkFormatError("CSV has no header row")
        for index, row in enumerate(reader):
            rows.append(_row_from_mapping(index, row))
    if not rows:
        raise BenchmarkFormatError("benchmark has no rows")
    if len(rows) > MAX_ROWS:
        raise BenchmarkFormatError(f"benchmark has {len(rows)} rows; the limit is {MAX_ROWS}")
    seen: set[str] = set()
    for row in rows:
        if row.prompt_uid in seen:
            raise BenchmarkFormatError(f"duplicate prompt_uid {row.prompt_uid!r}")
        seen.add(row.prompt_uid)
    return rows

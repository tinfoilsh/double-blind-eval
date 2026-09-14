"""Content identity of an adapter directory, independent of how it was archived.

The hash covers every regular file under the directory: sha256 over the canonical
JSON of ``{relative/path: sha256(file bytes)}``. The model owner can compute it
locally before uploading and compare it with what the enclave puts in the receipt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from dbe.canonical import canonical_json, sha256_hex


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def adapter_content_hash(root: Path) -> tuple[str, dict[str, str]]:
    root = Path(root)
    files = {
        str(p.relative_to(root)).replace("\\", "/"): _file_sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    if not files:
        raise FileNotFoundError(f"{root} contains no files")
    return sha256_hex(canonical_json(files)), files

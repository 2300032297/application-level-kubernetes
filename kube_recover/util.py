from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


class RecoveryError(ValueError):
    """An operation failed closed; no successful recovery is claimed."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, indent=2, sort_keys=True).encode())


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"p50": None, "p95": None, "p99": None}
    return {f"p{p}": ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)] for p in (50, 95, 99)}

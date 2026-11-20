"""Shared paths, chunk loading and corpus fingerprinting for the retrieval indexes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .ingest import CHUNKS_FILE, PROCESSED

INDEX_DIR = PROCESSED / "index"
BM25_FILE = "bm25.json"
DENSE_FILE = "dense.faiss"
DENSE_META = "dense_meta.json"
INDEX_FORMAT_VERSION = 1
REBUILD_HINT = "Rebuild the indexes with:  cd backend && python scripts/build_index.py"


class RetrievalIndexError(RuntimeError):
    """An index is missing, corrupted or does not match the current corpus/configuration."""


def load_chunks(path: Path = CHUNKS_FILE) -> list[dict]:
    if not path.exists():
        raise RetrievalIndexError(f"Chunk file not found: {path}. {REBUILD_HINT}")
    with path.open(encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    ids = [c["id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise RetrievalIndexError("Duplicate chunk IDs in chunks.jsonl. " + REBUILD_HINT)
    return chunks


def corpus_fingerprint(chunks: list[dict]) -> str:
    """Changes whenever any chunk's id, label or text changes, so stale indexes are detected."""
    h = hashlib.sha256()
    for c in chunks:
        h.update(json.dumps([c["id"], c["label"], c["text"]], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]

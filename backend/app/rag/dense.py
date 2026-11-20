"""Dense retrieval: multilingual E5 embeddings + FAISS inner-product search.

* Model: intfloat/multilingual-e5-small (384-dim, ~100 languages incl. Hindi), run locally on CPU via
  sentence-transformers. No paid API.
* E5 requires prefixes: "passage: " for indexed chunks, "query: " for user queries.
* Embeddings are L2-normalised, so FAISS IndexFlatIP (inner product) == cosine similarity.
* The index is built once (scripts/build_index.py) and persisted with metadata (model, dim, prefixes,
  corpus fingerprint, chunk-id order). Loading validates all of it and fails loudly if anything is off.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from .store import DENSE_FILE, DENSE_META, INDEX_FORMAT_VERSION, REBUILD_HINT, RetrievalIndexError

log = logging.getLogger("nyaya.dense")
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


@lru_cache(maxsize=2)
def get_model(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:  # pragma: no cover
        raise RetrievalIndexError("sentence-transformers is not installed: pip install -r requirements.txt") from e
    log.info("loading embedding model %s", model_name)
    return SentenceTransformer(model_name, device="cpu")


def passage_text(c: dict) -> str:
    title = c.get("part_title") or c["doc_title"]
    return f"{PASSAGE_PREFIX}{title}, {c['label']}. {c['text']}"


def embed(model_name: str, texts: list[str], batch_size: int = 16) -> np.ndarray:
    vecs = get_model(model_name).encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                        convert_to_numpy=True, show_progress_bar=False)
    return np.ascontiguousarray(vecs, dtype="float32")


class DenseIndex:
    def __init__(self, index, chunk_ids: list[str], model_name: str, fingerprint: str):
        self.index, self.chunk_ids, self.model_name, self.fingerprint = index, chunk_ids, model_name, fingerprint

    @classmethod
    def build(cls, chunks: list[dict], model_name: str, fingerprint: str) -> "DenseIndex":
        import faiss

        vecs = embed(model_name, [passage_text(c) for c in chunks])
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        return cls(index, [c["id"] for c in chunks], model_name, fingerprint)

    def save(self, directory: Path) -> None:
        import faiss

        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(directory / DENSE_FILE))
        (directory / DENSE_META).write_text(json.dumps({
            "version": INDEX_FORMAT_VERSION, "model": self.model_name, "dim": self.index.d,
            "metric": "inner_product_on_l2_normalised (cosine)", "query_prefix": QUERY_PREFIX,
            "passage_prefix": PASSAGE_PREFIX, "fingerprint": self.fingerprint, "count": self.index.ntotal,
            "chunk_ids": self.chunk_ids, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=1), encoding="utf-8")

    @staticmethod
    def read_meta(directory: Path) -> dict | None:
        p = directory / DENSE_META
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except ValueError:
            return None

    @classmethod
    def is_current(cls, directory: Path, model_name: str, fingerprint: str) -> bool:
        m = cls.read_meta(directory)
        return bool(m and (directory / DENSE_FILE).exists() and m.get("version") == INDEX_FORMAT_VERSION
                    and m.get("model") == model_name and m.get("fingerprint") == fingerprint)

    @classmethod
    def load(cls, directory: Path, model_name: str, expected_fingerprint: str) -> "DenseIndex":
        import faiss

        meta_path, idx_path = directory / DENSE_META, directory / DENSE_FILE
        if not meta_path.exists() or not idx_path.exists():
            raise RetrievalIndexError(f"Dense (FAISS) index missing in {directory}. {REBUILD_HINT}")
        meta = cls.read_meta(directory)
        if meta is None:
            raise RetrievalIndexError(f"Dense index metadata is corrupted. {REBUILD_HINT}")
        if meta.get("version") != INDEX_FORMAT_VERSION:
            raise RetrievalIndexError(f"Dense index format is outdated. {REBUILD_HINT}")
        if meta.get("model") != model_name:
            raise RetrievalIndexError(
                f"Dense index was built with '{meta.get('model')}' but EMBEDDING_MODEL is '{model_name}'. {REBUILD_HINT}")
        if meta.get("fingerprint") != expected_fingerprint:
            raise RetrievalIndexError(f"Dense index was built for a different corpus. {REBUILD_HINT}")
        try:
            index = faiss.read_index(str(idx_path))
        except Exception as e:  # faiss raises RuntimeError on a corrupted file
            raise RetrievalIndexError(f"Dense index file is corrupted ({type(e).__name__}). {REBUILD_HINT}") from e
        if index.ntotal != len(meta.get("chunk_ids", [])) or index.d != meta.get("dim"):
            raise RetrievalIndexError(f"Dense index does not match its metadata. {REBUILD_HINT}")
        return cls(index, meta["chunk_ids"], model_name, expected_fingerprint)

    def search(self, query: str, n: int = 30) -> list[tuple[str, float]]:
        """Return up to n (chunk_id, cosine_similarity), best first. Query is used as-is (no translation)."""
        if not query.strip():
            return []
        q = embed(self.model_name, [QUERY_PREFIX + query])
        scores, idx = self.index.search(q, min(n, self.index.ntotal))
        return [(self.chunk_ids[i], float(s)) for s, i in zip(scores[0], idx[0]) if i >= 0]

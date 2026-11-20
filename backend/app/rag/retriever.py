"""Public retrieval API used by Chat, Legal Guidance and Document Studio.

`retrieve()` always runs HYBRID retrieval (BM25 + multilingual E5/FAISS, fused with RRF). If either index is
missing, corrupted or stale, it raises RetrievalIndexError. There is no silent fallback to BM25-only.
The `mode` argument ("bm25" / "dense") exists only so the evaluation can compare the three approaches.
Retrieval never calls the LLM, so any model can be connected on top of it.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .dense import DenseIndex
from .hybrid import HybridRetriever, RetrievedChunk
from .sparse import BM25Index
from .store import INDEX_DIR, RetrievalIndexError, corpus_fingerprint, load_chunks

__all__ = ["retrieve", "get_retriever", "status", "to_source", "RetrievalIndexError"]


@lru_cache
def get_retriever() -> HybridRetriever:
    s = get_settings()
    chunks = load_chunks()
    fp = corpus_fingerprint(chunks)
    bm25 = BM25Index.load(INDEX_DIR, fp)
    dense = DenseIndex.load(INDEX_DIR, s.embedding_model, fp)
    return HybridRetriever(chunks, bm25, dense, candidates=s.rag_candidates, rrf_k=s.rrf_k)


def to_source(hit: RetrievedChunk, ref: str) -> dict:
    c = hit.chunk
    pages = (f"p. {c['page_start']}" if c["page_start"] == c["page_end"] else f"pp. {c['page_start']}-{c['page_end']}") \
        if c["page_start"] else None
    return {
        "ref": ref,
        "chunk_id": c["id"],
        "source_id": c["source_id"],
        "title": c.get("part_title") or c["doc_title"],
        "short": c["doc_short"],
        "label": c["label"],
        "pages": pages,
        "url": c["url"] + (f"#page={c['page_start']}" if c["page_start"] else ""),
        "excerpt": c["text"][:700],
        "score": round(hit.score, 4),
        "retrieval": {"bm25_rank": hit.ranks.get("bm25"), "dense_rank": hit.ranks.get("dense")},
    }


def retrieve(query: str, top_k: int | None = None, category: str | None = None, mode: str = "hybrid") -> list[dict]:
    """Return citeable sources (refs S1..Sn) for a query in any language."""
    hits = get_retriever().search(query, top_k=top_k or get_settings().rag_top_k, category=category, mode=mode)
    return [to_source(h, f"S{i}") for i, h in enumerate(hits, start=1)]


def status() -> dict:
    """Health information; never raises."""
    s = get_settings()
    try:
        r = get_retriever()
        return {"mode": "hybrid (BM25 + dense, RRF)", "chunks": len(r.chunks), "bm25_docs": len(r.bm25.chunk_ids),
                "dense_vectors": r.dense.index.ntotal, "embedding_model": s.embedding_model,
                "rrf_k": s.rrf_k, "candidates_per_ranker": s.rag_candidates, "top_k": s.rag_top_k}
    except RetrievalIndexError as e:
        return {"mode": "unavailable", "error": str(e)}

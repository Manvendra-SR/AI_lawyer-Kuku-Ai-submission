"""Hybrid retrieval: BM25 and dense search run independently, then Reciprocal Rank Fusion.

    RRF_score(d) = sum over rankers i of 1 / (k + rank_i(d))      (rank starts at 1)

Only ranks are fused, never raw scores (BM25 scores and cosine similarities are not comparable).
Documents are identified by their stable chunk ID, so a chunk found by both rankers appears once.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .dense import DenseIndex
from .sparse import BM25Index


def rrf_fuse(rankings: dict[str, list[str]], k: int = 60) -> list[tuple[str, float, dict[str, int]]]:
    """Fuse ranked chunk-ID lists. Returns [(chunk_id, rrf_score, {ranker: rank})], best first.

    Ties are broken by best individual rank, then chunk ID, so the order is deterministic.
    Duplicate IDs inside one ranking count only at their first (best) position.
    """
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in rankings.items():
        seen: set[str] = set()
        for rank, cid in enumerate(ids, start=1):
            if cid in seen:
                continue
            seen.add(cid)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(cid, {})[name] = rank
    return sorted(((cid, s, ranks[cid]) for cid, s in scores.items()),
                  key=lambda x: (-x[1], min(x[2].values()), x[0]))


@dataclass
class RetrievedChunk:
    chunk: dict
    score: float
    ranks: dict[str, int] = field(default_factory=dict)


class HybridRetriever:
    MODES = ("hybrid", "bm25", "dense")

    def __init__(self, chunks: list[dict], bm25: BM25Index, dense: DenseIndex, candidates: int = 30, rrf_k: int = 60):
        self.chunks = chunks
        self.by_id = {c["id"]: c for c in chunks}
        self.bm25, self.dense = bm25, dense
        self.candidates, self.rrf_k = candidates, rrf_k
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieval")

    def search(self, query: str, top_k: int = 6, category: str | None = None, mode: str = "hybrid") -> list[RetrievedChunk]:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        if mode == "bm25":
            return [RetrievedChunk(self.by_id[c], s, {"bm25": r}) for r, (c, s) in
                    enumerate(self.bm25.search(query, self.candidates, category)[:top_k], start=1)]
        if mode == "dense":
            return [RetrievedChunk(self.by_id[c], s, {"dense": r}) for r, (c, s) in
                    enumerate(self.dense.search(query, self.candidates)[:top_k], start=1)]
        # hybrid: both rankers run in parallel on the same original query
        f_sparse = self._pool.submit(self.bm25.search, query, self.candidates, category)
        f_dense = self._pool.submit(self.dense.search, query, self.candidates)
        sparse, dense = f_sparse.result(), f_dense.result()
        fused = rrf_fuse({"bm25": [c for c, _ in sparse], "dense": [c for c, _ in dense]}, k=self.rrf_k)
        return [RetrievedChunk(self.by_id[cid], s, r) for cid, s, r in fused[:top_k]]

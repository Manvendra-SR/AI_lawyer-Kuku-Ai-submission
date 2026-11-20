"""Offline retrieval tests: BM25, dense (E5 + FAISS), RRF, dedup, metadata, persistence, cross-lingual,
missing/corrupted indexes. No Groq calls. The embedding model is loaded from the local Hugging Face cache
(HF_HUB_OFFLINE=1 is set in conftest.py)."""
from __future__ import annotations

import json
import shutil

import pytest

from app.config import get_settings
from app.rag.dense import DenseIndex, passage_text
from app.rag.hybrid import HybridRetriever, rrf_fuse
from app.rag.retriever import get_retriever, retrieve, status
from app.rag.sparse import BM25Index, expand_query
from app.rag.store import (BM25_FILE, DENSE_FILE, DENSE_META, INDEX_DIR, RetrievalIndexError, corpus_fingerprint,
                           load_chunks)

MODEL = get_settings().embedding_model


@pytest.fixture(scope="module")
def r() -> HybridRetriever:
    return get_retriever()


@pytest.fixture(scope="module")
def small_chunks():
    chunks = load_chunks()
    pick = {"ecom_rules_2020#14", "ecom_rules_2020#9", "cpa2019#110", "cpa2019#12", "dark_patterns_2023#15"}
    return [c for c in chunks if c["id"] in pick]


# ------------------------------------------------------------------ sparse

def test_sparse_exact_rule_reference(r):
    ids = [c for c, _ in r.bm25.search("Rule 6(3) refuse to take back defective goods refund consideration", 5)]
    assert "ecom_rules_2020#14" in ids[:3]


def test_glossary_appends_but_never_replaces_original_query():
    q = "Maine order cancel kiya, paisa wapas nahi aaya"
    expanded = expand_query(q)
    assert expanded.startswith(q) and "refund" in expanded[len(q):]


# ------------------------------------------------------------------ dense

def test_dense_semantic_paraphrase(r):
    ids = [c for c, _ in r.dense.search("The online store won't take back the broken blender or return my money", 5)]
    assert {"ecom_rules_2020#14", "ecom_rules_2020#18"} & set(ids[:3])


def test_dense_cross_lingual_hindi_query_finds_english_rule(r):
    ids = [c for c, _ in r.dense.search("ऑनलाइन मंगाया सामान कभी डिलीवर नहीं हुआ और विक्रेता पैसे वापस नहीं कर रहा", 5)]
    assert {"ecom_rules_2020#14", "ecom_rules_2020#18"} & set(ids[:3])


def test_dense_scores_are_cosine_and_sorted(r):
    res = r.dense.search("misleading advertisement", 10)
    scores = [s for _, s in res]
    assert scores == sorted(scores, reverse=True) and all(-1.0001 <= s <= 1.0001 for s in scores)


def test_e5_prefixes_used():
    assert passage_text({"doc_title": "T", "label": "Rule 1", "text": "x"}).startswith("passage: ")
    meta = json.loads((INDEX_DIR / DENSE_META).read_text(encoding="utf-8"))
    assert meta["query_prefix"] == "query: " and meta["passage_prefix"] == "passage: " and meta["model"] == MODEL


# ------------------------------------------------------------------ RRF

def test_rrf_formula_and_order():
    fused = rrf_fuse({"bm25": ["a", "b", "c"], "dense": ["b", "d"]}, k=60)
    scores = {cid: s for cid, s, _ in fused}
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61) and scores["d"] == pytest.approx(1 / 62)
    assert [cid for cid, _, _ in fused] == ["b", "a", "d", "c"]  # a/d tie on score? no: a=1/61 > d=1/62
    assert dict((cid, rk) for cid, _, rk in fused)["b"] == {"bm25": 2, "dense": 1}


def test_rrf_deduplicates_and_is_deterministic():
    fused = rrf_fuse({"bm25": ["x", "x", "y"], "dense": ["y", "x"]}, k=60)
    ids = [cid for cid, _, _ in fused]
    assert len(ids) == len(set(ids)) == 2
    assert dict((c, rk) for c, _, rk in fused)["x"] == {"bm25": 1, "dense": 2}  # duplicate counted once, best rank
    tie = rrf_fuse({"bm25": ["p"], "dense": ["q"]}, k=60)
    assert [c for c, _, _ in tie] == ["p", "q"]  # equal score + equal best rank -> chunk id order


def test_hybrid_invokes_both_rankers_and_fuses(r, monkeypatch):
    seen = {}
    orig_b, orig_d = r.bm25.search, r.dense.search
    monkeypatch.setattr(r.bm25, "search", lambda q, n, cat=None: seen.setdefault("bm25", orig_b(q, n, cat)))
    monkeypatch.setattr(r.dense, "search", lambda q, n: seen.setdefault("dense", orig_d(q, n)))
    hits = r.search("The website showed a low price but added extra charges at checkout", top_k=10)
    assert set(seen) == {"bm25", "dense"}
    expected = rrf_fuse({"bm25": [c for c, _ in seen["bm25"]], "dense": [c for c, _ in seen["dense"]]}, k=r.rrf_k)
    assert [h.chunk["id"] for h in hits] == [c for c, _, _ in expected[:10]]
    assert len({h.chunk["id"] for h in hits}) == len(hits)


# ------------------------------------------------------------------ metadata

def test_metadata_preserved():
    chunks = {c["id"]: c for c in load_chunks()}
    for s in retrieve("seller refuses refund for damaged phone", top_k=6):
        c = chunks[s["chunk_id"]]
        assert s["label"] == c["label"] and s["source_id"] == c["source_id"]
        assert s["url"].startswith(c["url"]) and s["excerpt"] == c["text"][:700]
        assert s["title"] == (c.get("part_title") or c["doc_title"])
        if c["page_start"]:
            assert str(c["page_start"]) in s["pages"]
    assert status()["mode"].startswith("hybrid")


# ------------------------------------------------------------------ persistence & failure handling

def test_persistence_roundtrip(tmp_path, small_chunks):
    fp = corpus_fingerprint(small_chunks)
    BM25Index.build(small_chunks, fp).save(tmp_path)
    built = DenseIndex.build(small_chunks, MODEL, fp)
    built.save(tmp_path)
    assert DenseIndex.is_current(tmp_path, MODEL, fp)
    loaded = DenseIndex.load(tmp_path, MODEL, fp)
    q = "time limit to file a consumer complaint"
    assert loaded.search(q, 3) == built.search(q, 3)
    assert BM25Index.load(tmp_path, fp).search("misleading advertisement", 3)[0][0] == "cpa2019#12"


def test_missing_indexes_raise_actionable_error(tmp_path):
    with pytest.raises(RetrievalIndexError, match="build_index.py"):
        DenseIndex.load(tmp_path, MODEL, "abc")
    with pytest.raises(RetrievalIndexError, match="build_index.py"):
        BM25Index.load(tmp_path, "abc")


def test_corrupted_and_stale_indexes_detected(tmp_path):
    fp = corpus_fingerprint(load_chunks())
    for f in (DENSE_FILE, DENSE_META, BM25_FILE):
        shutil.copy(INDEX_DIR / f, tmp_path / f)
    DenseIndex.load(tmp_path, MODEL, fp)  # copies load fine
    with pytest.raises(RetrievalIndexError, match="different corpus"):
        DenseIndex.load(tmp_path, MODEL, "0000000000000000")
    with pytest.raises(RetrievalIndexError, match="EMBEDDING_MODEL"):
        DenseIndex.load(tmp_path, "some/other-model", fp)
    (tmp_path / DENSE_FILE).write_bytes(b"not a faiss index")
    with pytest.raises(RetrievalIndexError, match="corrupted"):
        DenseIndex.load(tmp_path, MODEL, fp)
    (tmp_path / BM25_FILE).write_text("{broken json", encoding="utf-8")
    with pytest.raises(RetrievalIndexError, match="corrupted"):
        BM25Index.load(tmp_path, fp)

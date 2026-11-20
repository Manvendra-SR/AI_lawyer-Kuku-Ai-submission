"""Build the retrieval indexes from the downloaded official documents.

    python scripts/build_index.py            # extract + chunk, build BM25, build FAISS only if needed
    python scripts/build_index.py --force    # also re-embed even if the dense index is current

Steps: 1) extract + chunk (data/processed/chunks.jsonl, stable chunk IDs)  2) BM25 index (bm25.json)
3) E5 embeddings + FAISS index (dense.faiss + dense_meta.json). All in data/processed/index/.
Embeddings are regenerated only when the corpus fingerprint or EMBEDDING_MODEL changed (or --force).
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.getLogger("pypdf").setLevel(logging.ERROR)

from app.config import get_settings  # noqa: E402
from app.rag.dense import DenseIndex  # noqa: E402
from app.rag.ingest import CHUNKS_FILE, run  # noqa: E402
from app.rag.sparse import BM25Index  # noqa: E402
from app.rag.store import INDEX_DIR, corpus_fingerprint, load_chunks  # noqa: E402

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-embed even if the dense index is up to date")
    args = ap.parse_args()

    report = run()
    for d in report["documents"]:
        print(f"{d['id']:32s} indexed={d.get('indexed')!s:5s} chunks={d.get('chunks', 0):4d} {d.get('note') or ''}")
    print(f"Chunks: {report['total_chunks']} -> {CHUNKS_FILE}")

    chunks = load_chunks()
    fp = corpus_fingerprint(chunks)
    print(f"Corpus fingerprint: {fp}")

    t = time.time()
    BM25Index.build(chunks, fp).save(INDEX_DIR)
    print(f"BM25 index: {len(chunks)} docs saved ({time.time() - t:.2f}s)")

    model = get_settings().embedding_model
    if not args.force and DenseIndex.is_current(INDEX_DIR, model, fp):
        print(f"Dense index: up to date for {model} (skipped re-embedding; use --force to rebuild)")
    else:
        t = time.time()
        dense = DenseIndex.build(chunks, model, fp)
        dense.save(INDEX_DIR)
        print(f"Dense index: {dense.index.ntotal} vectors, dim={dense.index.d}, model={model} ({time.time() - t:.1f}s)")

    # sanity: both indexes load and agree on the chunk set
    b = BM25Index.load(INDEX_DIR, fp)
    d = DenseIndex.load(INDEX_DIR, model, fp)
    assert b.chunk_ids == d.chunk_ids == [c["id"] for c in chunks]
    print("Verified: BM25 and FAISS indexes load and cover the same", len(chunks), "chunk IDs.")
    print(json.dumps({"index_dir": str(INDEX_DIR)}))

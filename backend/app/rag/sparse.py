"""Sparse retrieval: BM25 over the legal chunks.

Strengths: exact legal terms ("Rule 6", "limitation", "grievance officer"), numbers, names.
The ORIGINAL query is always kept; a small glossary only APPENDS English legal terms for common
Hindi/Hinglish complaint words, because the indexed statutes are in English.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from .store import BM25_FILE, INDEX_FORMAT_VERSION, REBUILD_HINT, RetrievalIndexError

STOPWORDS = set("""a an the and or of to in on for by with as at from is are was were be been being this that these those
it its into any such which who whom shall may under such other than said there their them they he she his her i me my
we our you your not no nor if so do does did done has have had having can could would should will also all each per
namely thereof therein herein hereby section rule sub clause act rules""".split())

# Hindi (Devanagari + romanised) complaint vocabulary -> English legal search terms (appended, never replacing).
GLOSSARY: dict[str, str] = {
    "refund": "refund", "रिफंड": "refund", "paisa": "refund money", "paise": "refund money", "पैसा": "refund money",
    "पैसे": "refund money", "wapas": "refund return", "vapas": "refund return", "वापस": "refund return",
    "wapsi": "refund return", "वापसी": "refund return", "lautana": "refund return", "लौटाना": "refund return",
    "rupaye": "price paid", "रुपये": "price paid", "kata": "charged", "deducted": "charged",
    "kharab": "defective defect deficiency", "खराब": "defective defect deficiency", "toota": "damaged defective",
    "tuta": "damaged defective", "टूटा": "damaged defective", "tuti": "damaged defective", "टूटी": "damaged defective",
    "damage": "damaged defective defect", "damaged": "damaged defective defect", "defective": "defective defect",
    "दोषपूर्ण": "defective defect", "nakli": "counterfeit fake goods", "नकली": "counterfeit fake goods",
    "fake": "counterfeit misleading", "galat": "wrong different", "गलत": "wrong different",
    "dusra": "different goods", "दूसरा": "different goods",
    "delivery": "delivery delivered", "डिलीवरी": "delivery delivered", "डिलीवर": "delivery delivered",
    "nahi aaya": "not delivered", "pahucha": "delivered", "पहुंचा": "delivered", "पहुँचा": "delivered",
    "order": "order purchase", "ऑर्डर": "order purchase", "cancel": "cancellation cancelled",
    "कैंसिल": "cancellation cancelled", "replacement": "replace replacement", "badal": "replace replacement",
    "बदल": "replace replacement",
    "seller": "seller marketplace", "विक्रेता": "seller", "dukandar": "seller trader", "दुकानदार": "seller trader",
    "company": "seller e-commerce entity", "कंपनी": "seller e-commerce entity", "shikayat": "complaint grievance",
    "शिकायत": "complaint grievance", "complaint": "complaint grievance", "dhokha": "unfair trade practice deceptive",
    "धोखा": "unfair trade practice deceptive", "cheat": "unfair trade practice deceptive",
    "cheated": "unfair trade practice deceptive", "warranty": "warranty guarantee", "guarantee": "warranty guarantee",
    "bill": "invoice bill", "बिल": "invoice bill", "invoice": "invoice bill", "court": "consumer commission complaint",
    "adalat": "consumer commission complaint", "अदालत": "consumer commission complaint",
    "आयोग": "commission", "राहत": "relief order", "muavza": "compensation", "मुआवजा": "compensation",
    "मुआवज़ा": "compensation", "compensation": "compensation",
    "vigyapan": "advertisement misleading", "विज्ञापन": "advertisement misleading", "ad ": "advertisement",
    "jhoota": "false misleading", "झूठा": "false misleading",
    "limitation": "limitation two years cause of action", "time limit": "limitation two years cause of action",
    "how long": "limitation two years cause of action", "kitne din": "limitation two years cause of action",
    "कितने दिन": "limitation two years cause of action", "समय सीमा": "limitation two years cause of action",
    "where to file": "jurisdiction District Commission complaint", "kahan": "jurisdiction District Commission",
    "which commission": "jurisdiction District Commission State Commission value consideration paid",
    "which consumer commission": "jurisdiction District Commission State Commission value consideration paid",
    "kaun si": "jurisdiction District Commission", "kis court": "jurisdiction District Commission",
    "किस": "jurisdiction District Commission", "कहाँ": "jurisdiction District Commission",
    "e-daakhil": "electronic filing complaint", "online complaint": "electronic filing complaint",
    "grievance": "grievance officer", "customer care": "grievance officer", "mediation": "mediation",
}

# English statute vocabulary per dispute category (appended to the BM25 query only).
CATEGORY_HINTS: dict[str, str] = {
    "damaged_or_defective": "defect defective goods deficiency replacement refund return marketplace e-commerce entity refuse take back",
    "refund_not_received": "refund consideration paid cancellation refund requests grievance e-commerce entity effect",
    "not_delivered": "delivery delayed late refuse refund consideration e-commerce entity grievance",
    "wrong_item": "goods not as advertised specifications features take back refund e-commerce entity",
    "refund_or_replacement_refused": "refuse take back goods refund consideration unfair trade practice e-commerce",
    "other_ecommerce": "e-commerce entity seller grievance redressal consumer rights",
}

_TOKEN = re.compile(r"[a-z0-9]+|[ऀ-ॿ]+")


def _stem(tok: str) -> str:
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)] + rep
    return tok


def tokenize(text: str) -> list[str]:
    toks = _TOKEN.findall(re.sub(r"e\s*-\s*commerce", "ecommerce", text.lower()))
    return [_stem(t) for t in toks if t not in STOPWORDS and len(t) > 1]


def expand_query(query: str, category: str | None = None) -> str:
    """Original query + appended English legal terms (glossary + category hint)."""
    q = query.lower()
    extra = [en for term, en in GLOSSARY.items() if term in q]
    if category and category in CATEGORY_HINTS:
        extra.append(CATEGORY_HINTS[category])
    return query + (" " + " ".join(extra) if extra else "")


def doc_text(c: dict) -> str:
    # Index label + title + text so "Section 35" / "Rule 4" style queries also match.
    return f"{c['label']} {c.get('part_title') or c['doc_title']} {c['text']}"


class BM25Index:
    def __init__(self, chunk_ids: list[str], tfs: list[dict], fingerprint: str, k1: float = 1.5, b: float = 0.75):
        self.chunk_ids, self.fingerprint, self.k1, self.b = chunk_ids, fingerprint, k1, b
        self.tfs = [Counter(t) for t in tfs]
        self.lens = [sum(t.values()) for t in self.tfs]
        self.avgdl = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(t for tf in self.tfs for t in tf)
        n = len(self.tfs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def build(cls, chunks: list[dict], fingerprint: str) -> "BM25Index":
        return cls([c["id"] for c in chunks], [Counter(tokenize(doc_text(c))) for c in chunks], fingerprint)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / BM25_FILE
        path.write_text(json.dumps({"version": INDEX_FORMAT_VERSION, "fingerprint": self.fingerprint,
                                    "k1": self.k1, "b": self.b, "chunk_ids": self.chunk_ids,
                                    "tfs": [dict(t) for t in self.tfs]}, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: Path, expected_fingerprint: str) -> "BM25Index":
        path = directory / BM25_FILE
        if not path.exists():
            raise RetrievalIndexError(f"BM25 index missing ({path}). {REBUILD_HINT}")
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("version") != INDEX_FORMAT_VERSION:
                raise RetrievalIndexError(f"BM25 index format v{d.get('version')} is outdated. {REBUILD_HINT}")
            if d["fingerprint"] != expected_fingerprint:
                raise RetrievalIndexError(f"BM25 index was built for a different corpus. {REBUILD_HINT}")
            if len(d["chunk_ids"]) != len(d["tfs"]):
                raise RetrievalIndexError(f"BM25 index is inconsistent. {REBUILD_HINT}")
            return cls(d["chunk_ids"], d["tfs"], d["fingerprint"], d["k1"], d["b"])
        except RetrievalIndexError:
            raise
        except (ValueError, KeyError, TypeError) as e:
            raise RetrievalIndexError(f"BM25 index is corrupted ({type(e).__name__}). {REBUILD_HINT}") from e

    def search(self, query: str, n: int = 30, category: str | None = None,
               min_score: float = 1.0) -> list[tuple[str, float]]:
        """Return up to n (chunk_id, bm25_score), best first."""
        q = tokenize(expand_query(query, category))
        if not q:
            return []
        qtf = Counter(q)
        # Precision floor: for queries with 5+ distinct terms a passage must share >= 2 of them.
        min_matched = 2 if len(qtf) >= 5 else 1
        scored = []
        for i, tf in enumerate(self.tfs):
            s, matched = 0.0, 0
            for t, qn in qtf.items():
                f = tf.get(t)
                if not f:
                    continue
                matched += 1
                s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl)) * (1 + 0.2 * (qn - 1))
            if s >= min_score and matched >= min_matched:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(self.chunk_ids[i], s) for s, i in scored[:n]]

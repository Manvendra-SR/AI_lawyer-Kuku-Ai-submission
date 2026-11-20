"""Turn downloaded official documents into citeable chunks.

Output: data/processed/chunks.jsonl (one chunk per line) and data/processed/ingest_report.json.

Design choices (documented in README):
* Gazette PDFs are bilingual. pypdf extracts the Hindi half with a broken legacy-font mapping
  (garbled Devanagari), so only English lines are indexed. Hindi/Hinglish user queries are bridged
  to English legal text through a deterministic glossary at query time (see retriever.py).
* Chunks follow the legal structure: one chunk per section/rule; long sections are split at their
  top-level sub-sections "(1)", "(2)"... so a chunk can be cited as e.g. "Section 2(47)".
* Every chunk keeps: source id, document title, section/rule label, PDF page range, original URL.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..config import DATA_DIR

PROCESSED = DATA_DIR / "processed"
CHUNKS_FILE = PROCESSED / "chunks.jsonl"
REPORT_FILE = PROCESSED / "ingest_report.json"
MANIFEST = DATA_DIR / "sources_manifest.json"

LABEL_PREFIX = {"act": "Section", "rules": "Rule", "regulations": "Regulation", "guidelines": "Para"}
MAX_CHUNK = 1800
TARGET_SPLIT = 1300

_DEV = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_HEADER = re.compile(
    r"^\s*(\[?\s*PART\s+II|SEC\.\s*\d|THE GAZETTE OF INDIA|\d+\s+THE GAZETTE OF INDIA|"
    r"REGISTERED NO|EXTRAORDINARY\s*$|PUBLISHED BY AUTHORITY|MGIPMRND|Uploaded by Dte|Digitally signed|"
    r"Signature Not Verified|Date:\s*20\d\d)", re.I)
_SECTION = re.compile(r"^\s*(\d{1,3})([A-Z]?)\.\s*(.*)$")
_SUBSEC = re.compile(r"^\s*\((\d{1,3}[A-Z]?)\)\s")
_CALLED = re.compile(
    r"may be called the\s+([^.;:]{5,200}?(?:Rules|Regulations|Guidelines|Act)\s*,?\s*\d{4})", re.I | re.S)


@dataclass
class Chunk:
    id: str
    source_id: str
    doc_title: str
    doc_short: str
    kind: str
    url: str
    label: str
    page_start: int
    page_end: int
    text: str
    part_title: str | None = None
    meta: dict = field(default_factory=dict)


def _is_english_line(line: str) -> bool:
    dev = len(_DEV.findall(line))
    lat = len(_LATIN.findall(line))
    if dev == 0:
        return True
    return lat > 0 and dev / (dev + lat) < 0.15


def _clean_lines(reader: PdfReader) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for pno, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            line = re.sub(r"(\w) -(\w)", r"\1-\2", line)  # pypdf artefact: "e -commerce"
            if not line or not _is_english_line(line) or _HEADER.match(line):
                continue
            if re.fullmatch(r"[\d\s\-–—.,:;()\[\]]*", line):  # page numbers / stray punctuation
                continue
            out.append((line, pno))
    return out


def _split_sections(lines: list[tuple[str, int]]):
    """Yield (section_number_or_None, part_index, [(line, page), ...])."""
    sections: list[tuple[str | None, int, list]] = []
    current: list = []
    cur_num: str | None = None
    cur_int = 0
    part = 0
    for line, pno in lines:
        m = _SECTION.match(line)
        if m:
            n = int(m.group(1))
            rest = m.group(3).lower()
            new_part = n == 1 and ("short title" in rest or rest.startswith("(1) this act") or "these rules" in rest
                                   or "these regulations" in rest or "these guidelines" in rest)
            sequential = cur_int < n <= cur_int + 2 and not m.group(2)
            if new_part or sequential or (m.group(2) and n == cur_int):
                if current:
                    sections.append((cur_num, part, current))
                if new_part and cur_int > 0:
                    part += 1
                cur_int = n
                cur_num = f"{n}{m.group(2)}"
                current = [(line, pno)]
                continue
        current.append((line, pno))
    if current:
        sections.append((cur_num, part, current))
    return sections


def _split_long(lines: list[tuple[str, int]]):
    """Split a long section at top-level sub-sections; returns [(sub_label_range, lines)]."""
    groups: list[tuple[str | None, list]] = []
    cur_label, cur = None, []
    for line, pno in lines:
        m = _SUBSEC.match(line)
        if m and cur:
            groups.append((cur_label, cur))
            cur_label, cur = m.group(1), []
        elif m and not cur:
            cur_label = m.group(1)
        if not cur and not m and cur_label is None:
            # First line of the section carries sub-section (1) inline, either directly ("39. (1) Where...")
            # or after a heading ("6. Duties of sellers on marketplace. – (1) No seller...").
            m2 = re.match(r"^\s*\d{1,3}[A-Z]?\..{0,160}?\((1)\)\s", line)
            if m2:
                cur_label = m2.group(1)
        cur.append((line, pno))
    if cur:
        groups.append((cur_label, cur))
    # merge small adjacent groups
    merged: list[tuple[str, list]] = []
    buf_labels: list[str] = []
    buf: list = []
    size = 0
    for label, glines in groups:
        gsize = sum(len(l) + 1 for l, _ in glines)
        if buf and size + gsize > TARGET_SPLIT:
            merged.append((_range(buf_labels), buf))
            buf_labels, buf, size = [], [], 0
        if label:
            buf_labels.append(label)
        buf.extend(glines)
        size += gsize
    if buf:
        merged.append((_range(buf_labels), buf))
    # hard-split anything still oversized (very long single clauses)
    final = []
    for label, glines in merged:
        piece: list = []
        psize = 0
        for item in glines:
            if piece and psize + len(item[0]) > MAX_CHUNK:
                final.append((label, piece))
                piece, psize = [], 0
            piece.append(item)
            psize += len(item[0]) + 1
        if piece:
            final.append((label, piece))
    return final


def _range(labels: list[str]) -> str:
    if not labels:
        return ""
    return f"({labels[0]})" if len(labels) == 1 else f"({labels[0]})-({labels[-1]})"


def ingest_pdf(src: dict, path: Path) -> tuple[list[Chunk], dict]:
    reader = PdfReader(str(path))
    lines = _clean_lines(reader)
    prefix = LABEL_PREFIX.get(src["kind"], "Para")
    sections = _split_sections(lines)
    part_titles: dict[int, str] = {}
    for num, part, slines in sections:
        if part not in part_titles:
            m = _CALLED.search(" ".join(l for l, _ in slines))
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).replace(" ,", ",")
                part_titles[part] = re.sub(r"\s+-\s*", "-", title).strip(" .")
    chunks: list[Chunk] = []
    counter = 0
    for num, part, slines in sections:
        text_len = sum(len(l) + 1 for l, _ in slines)
        base = f"{prefix} {num}" if num else "Preamble"
        if not num and text_len < 200:
            continue
        pieces = _split_long(slines) if text_len > MAX_CHUNK else [("", slines)]
        for sub, plines in pieces:
            counter += 1
            label = base + (sub if num and sub else "")
            if not num and len(pieces) > 1:
                label = f"Preamble (part {counter})"
            chunks.append(Chunk(
                id=f"{src['id']}#{counter}",
                source_id=src["id"],
                doc_title=src["title"],
                doc_short=src["short"],
                kind=src["kind"],
                url=src["url"],
                label=label,
                part_title=part_titles.get(part) if len(part_titles) > 1 else None,
                page_start=plines[0][1],
                page_end=plines[-1][1],
                text="\n".join(l for l, _ in plines),
            ))
    report = {"pages": len(reader.pages), "english_lines": len(lines), "sections_detected": sum(1 for s in sections if s[0]),
              "chunks": len(chunks), "parts": part_titles}
    return chunks, report


def ingest_html(src: dict, path: Path) -> tuple[list[Chunk], dict]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "svg"]):
        tag.decompose()
    root = soup.find("main") or soup.body or soup
    paras = [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in root.find_all(["p", "li", "h1", "h2", "h3", "h4", "td"])]
    paras = [p for p in paras if len(p) > 25 and _is_english_line(p)
             and not re.search(r"opens? in (a )?new (tab|window)|PDF Download|Annual Report \d{4}", p, re.I)]
    seen, uniq = set(), []
    for p in paras:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    chunks, buf = [], []
    for p in uniq:
        buf.append(p)
        if sum(len(x) for x in buf) > 900:
            chunks.append("\n".join(buf))
            buf = []
    if buf and sum(len(x) for x in buf) > 150:
        chunks.append("\n".join(buf))
    out = [Chunk(id=f"{src['id']}#{i}", source_id=src["id"], doc_title=src["title"], doc_short=src["short"],
                 kind=src["kind"], url=src["url"], label="Web page", page_start=0, page_end=0, text=t)
           for i, t in enumerate(chunks, start=1)]
    return out, {"paragraphs": len(uniq), "chunks": len(out),
                 "note": None if out else "No extractable text (page is rendered by JavaScript); not indexed."}


def run() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    PROCESSED.mkdir(parents=True, exist_ok=True)
    all_chunks: list[Chunk] = []
    report: dict = {"documents": []}
    for src in manifest:
        entry = {"id": src["id"], "title": src["title"], "download_status": src["status"]}
        if src["status"] != "downloaded":
            entry["indexed"] = False
            entry["note"] = "Download failed; not indexed. See sources_manifest.json for the errors."
        else:
            path = DATA_DIR / src["file"]
            fn = ingest_html if src["kind"] == "web" else ingest_pdf
            chunks, info = fn(src, path)
            entry.update(info)
            entry["indexed"] = bool(chunks)
            all_chunks.extend(chunks)
        report["documents"].append(entry)
    with CHUNKS_FILE.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    report["total_chunks"] = len(all_chunks)
    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report

"""Deterministic post-checks on LLM output (anti-hallucination layer).

1. Source tags: any [S#] tag that was not in the retrieved set is removed and reported.
2. Law mentions: every "Section N" / "Rule N" / "धारा N" / "नियम N" mentioned in the text must correspond to a
   retrieved chunk label; otherwise it is reported as unverified (and surfaced in the UI as a warning).
"""
from __future__ import annotations

import re

# Accept the variants models actually emit: [S1], [ S1 ], 【S1】 (gpt-oss native style), with zero-width chars.
_TAG = re.compile(r"[\[【]\s*(S\d{1,2})\s*[\]】]")
_ZERO_WIDTH = re.compile(r"[​-‍⁠﻿]")


def normalize_tags(text: str) -> str:
    """Canonicalise every citation tag to ASCII [S#] so the UI and checks see one format."""
    text = _ZERO_WIDTH.sub("", text)
    return _TAG.sub(lambda m: f"[{m.group(1).upper()}]", text)
_MENTION = re.compile(
    r"\b(?P<kind>Section|Sec\.|Rule|Regulation|Clause|Para)\s+(?P<num>\d{1,3}[A-Z]?)"
    r"|(?P<hkind>धारा|नियम|विनियम|dhara|niyam)\s*(?P<hnum>\d{1,3}[A-Z]?)",
    re.I,
)
_KIND = {"section": "Section", "sec.": "Section", "धारा": "Section", "dhara": "Section",
         "rule": "Rule", "नियम": "Rule", "niyam": "Rule",
         "regulation": "Regulation", "विनियम": "Regulation", "clause": "Para", "para": "Para"}
_LABEL = re.compile(r"^(Section|Rule|Regulation|Para)\s+(\d{1,3}[A-Z]?)")


def check_source_tags(text: str, sources: list[dict]) -> tuple[str, list[str], list[str]]:
    """Return (cleaned_text, used_valid_refs, removed_invalid_refs)."""
    text = normalize_tags(text)
    valid = {s["ref"] for s in sources}
    used, invalid = [], []

    def repl(m: re.Match) -> str:
        ref = m.group(1).upper()
        if ref in valid:
            if ref not in used:
                used.append(ref)
            return f"[{ref}]"
        invalid.append(ref)
        return ""

    cleaned = _TAG.sub(repl, text)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)
    return cleaned, used, sorted(set(invalid))


def _allowed(sources: list[dict]) -> set[tuple[str, str]]:
    out = set()
    for s in sources:
        m = _LABEL.match(s.get("label", ""))
        if m:
            out.add((m.group(1), m.group(2).upper()))
        # Excerpts often cross-reference other provisions ("section 2", "rule 4") — those are textual
        # mentions in official text, so naming them is grounded too.
        for mm in _MENTION.finditer(s.get("excerpt", "")):
            kind = _KIND[(mm.group("kind") or mm.group("hkind")).lower()]
            out.add((kind, (mm.group("num") or mm.group("hnum")).upper()))
    return out


def unverified_mentions(text: str, sources: list[dict]) -> list[str]:
    allowed = _allowed(sources)
    bad = []
    for m in _MENTION.finditer(text):
        raw_kind = (m.group("kind") or m.group("hkind")).lower()
        kind = _KIND[raw_kind]
        num = (m.group("num") or m.group("hnum")).upper()
        # Rule/Regulation numbering is per-instrument; accept a match within the same kind family.
        if (kind, num) not in allowed:
            bad.append(m.group(0).strip())
    seen, out = set(), []
    for b in bad:
        if b.lower() not in seen:
            seen.add(b.lower())
            out.append(b)
    return out


def run_checks(text: str, sources: list[dict]) -> tuple[str, dict]:
    cleaned, used, invalid = check_source_tags(text, sources)
    return cleaned, {
        "cited_refs": used,
        "removed_invalid_refs": invalid,
        "unverified_mentions": unverified_mentions(cleaned, sources),
        "retrieved_count": len(sources),
    }

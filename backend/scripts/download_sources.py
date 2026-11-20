"""Download official Indian consumer-law sources into backend/data/raw and record the outcome of
every attempt (including failures) in backend/data/sources_manifest.json.

Usage:  python scripts/download_sources.py
Nothing is fabricated: if a URL fails, the manifest says so and that source is simply not indexed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = DATA / "raw"
MANIFEST = DATA / "sources_manifest.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

DCA = "https://consumeraffairs.gov.in/public/upload/files/"

# Each source lists candidate URLs in priority order; the first that returns the expected type wins.
SOURCES = [
    {
        "id": "cpa2019",
        "title": "The Consumer Protection Act, 2019 (Act No. 35 of 2019)",
        "short": "Consumer Protection Act, 2019",
        "kind": "act",
        "publisher": "India Code / Department of Consumer Affairs",
        "urls": [
            "https://www.indiacode.nic.in/bitstream/123456789/15256/1/eng201935.pdf",
            DCA + "CP%20Act%202019_1732700731.pdf",
        ],
    },
    {
        "id": "ecom_rules_2020",
        "title": "Consumer Protection (E-Commerce) Rules, 2020",
        "short": "E-Commerce Rules, 2020",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "E%20commerce%20rules_1732703966.pdf",
                 "https://consumeraffairs.nic.in/sites/default/files/E%20commerce%20rules.pdf"],
    },
    {
        "id": "ecom_rules_2020_corrigendum",
        "title": "Corrigendum to the Consumer Protection (E-Commerce) Rules, 2020",
        "short": "E-Commerce Rules corrigendum",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "Corrigendum%20to%20e%20commerce%20rules_1732704181.pdf"],
    },
    {
        "id": "ecom_amend_2021",
        "title": "Consumer Protection (E-Commerce) (Amendment) Rules, 2021",
        "short": "E-Commerce (Amendment) Rules, 2021",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "Consumer%20Protection%20(E-Commerce)%20(Amendment)%20Rules,%202021_1732704241.pdf"],
    },
    {
        "id": "ecom_amend_2026",
        "title": "Consumer Protection (E-Commerce) (Amendment) Rules, 2026",
        "short": "E-Commerce (Amendment) Rules, 2026",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "E%20Commerce%20Amendment%20Rules%202026_1789127949.pdf"],
    },
    {
        "id": "cdrc_rules_2020",
        "title": "Consumer Protection (Consumer Disputes Redressal Commissions) Rules, 2020 & General Rules, 2020",
        "short": "Consumer Commission Rules & General Rules, 2020",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "Consumer%20Commission%20Rules%20&%20General%20Rules_1732703661.pdf"],
    },
    {
        "id": "jurisdiction_rules_2021",
        "title": "Consumer Protection (Jurisdiction of the District Commission, the State Commission and the National Commission) Rules, 2021",
        "short": "Pecuniary Jurisdiction Rules, 2021",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "232278_1732705181.pdf", DCA + "232278_1732705029.pdf"],
    },
    {
        "id": "mediation_rules_2020",
        "title": "Consumer Protection (Mediation) Rules, 2020",
        "short": "Mediation Rules, 2020",
        "kind": "rules",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "Mediation%20Rules_1732703912.pdf"],
    },
    {
        "id": "commission_procedure_regs_2020",
        "title": "Consumer Protection (Consumer Commission Procedure) Regulations, 2020",
        "short": "Consumer Commission Procedure Regulations, 2020",
        "kind": "regulations",
        "publisher": "Department of Consumer Affairs",
        "urls": [DCA + "220667_1732707483.pdf"],
    },
    {
        "id": "dark_patterns_2023",
        "title": "Guidelines for Prevention and Regulation of Dark Patterns, 2023",
        "short": "Dark Patterns Guidelines, 2023",
        "kind": "guidelines",
        "publisher": "Central Consumer Protection Authority / DCA",
        "urls": [DCA + "The%20Guidelines%20for%20Prevention%20and%20Regulation%20of%20Dark%20Patterns,%202023_1732707717.pdf"],
    },
    {
        "id": "nch_home",
        "title": "National Consumer Helpline (NCH) - Home",
        "short": "National Consumer Helpline",
        "kind": "web",
        "publisher": "National Consumer Helpline, DCA",
        "urls": ["https://consumerhelpline.gov.in/"],
    },
    {
        "id": "nch_about",
        "title": "National Consumer Helpline (NCH) - About Us",
        "short": "National Consumer Helpline - About",
        "kind": "web",
        "publisher": "National Consumer Helpline, DCA",
        "urls": ["https://consumerhelpline.gov.in/public/about"],
    },
    {
        "id": "ejagriti_home",
        "title": "e-Jagriti consumer commission filing portal",
        "short": "e-Jagriti portal",
        "kind": "web",
        "publisher": "Department of Consumer Affairs",
        "urls": ["https://e-jagriti.gov.in/"],
    },
]


def fetch(url: str, kind: str) -> tuple[bytes | None, dict]:
    info: dict = {"url": url}
    try:
        with httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": UA}) as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return None, info
    ctype = r.headers.get("content-type", "")
    info.update(http_status=r.status_code, content_type=ctype, bytes=len(r.content))
    if r.status_code != 200:
        info["error"] = f"HTTP {r.status_code}"
        return None, info
    want_pdf = kind != "web"
    if want_pdf and not r.content.startswith(b"%PDF"):
        info["error"] = "response was not a PDF"
        return None, info
    return r.content, info


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = []
    for src in SOURCES:
        ext = "html" if src["kind"] == "web" else "pdf"
        entry = {k: v for k, v in src.items() if k != "urls"}
        entry["attempts"] = []
        for url in src["urls"]:
            content, info = fetch(url, src["kind"])
            entry["attempts"].append(info)
            if content is not None:
                path = RAW / f"{src['id']}.{ext}"
                path.write_bytes(content)
                entry.update(status="downloaded", url=url, file=f"raw/{path.name}",
                             sha256=hashlib.sha256(content).hexdigest())
                break
        else:
            entry.update(status="failed", url=src["urls"][0], file=None)
        entry["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest.append(entry)
        print(f"[{entry['status']:>10}] {src['id']}: " +
              "; ".join(f"{a['url']} -> {a.get('error') or a.get('http_status')}" for a in entry["attempts"]))
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(e["status"] == "downloaded" for e in manifest)
    print(f"\n{ok}/{len(manifest)} sources downloaded. Manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

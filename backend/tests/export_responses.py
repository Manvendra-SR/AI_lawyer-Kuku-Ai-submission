"""Export one live evaluation run (backend/eval_runs/eval_<ts>/) into outputs/responses/test_0N.txt.

Usage (from backend/):  python tests/export_responses.py eval_runs/eval_<timestamp>

Everything written is copied verbatim from the recorded API responses; nothing is edited or summarised.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(__file__).resolve().parents[2] / "outputs" / "responses"
CASE_FIELDS = ["title", "category", "status", "language", "seller_name", "platform", "product_details", "order_id",
               "purchase_date", "delivery_date", "amount_paid", "description", "previous_communication",
               "evidence", "desired_resolution", "missing_information"]
RULE = "=" * 78


def fmt_sources(sources: list[dict]) -> list[str]:
    out = []
    for s in sources:
        rk = s.get("retrieval") or {}
        how = f"   (hybrid: BM25 rank {rk.get('bm25_rank') or '-'}, dense rank {rk.get('dense_rank') or '-'})" if rk else ""
        out.append(f"  [{s['ref']}] {s['title']} — {s['label']}" + (f", {s['pages']}" if s.get("pages") else "")
                   + how + f"\n        {s['url']}")
    return out


def export(run_dir: Path, n: int, path: Path, model: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    L = [RULE, f"TEST {n:02d}: {data['scenario']}", f"Model: {model}   |   Run: {run_dir.name}",
         f"User language: {data.get('language')}   |   Case ID in MySQL: {data.get('case_id')}", RULE, ""]
    turn = 0
    last_case = None
    for e in data["log"]:
        req, resp, path_ = e["request"], e["response"], e["request"]["path"]
        if path_ == "/api/chat":
            turn += 1
            L.append(f"--- TURN {turn} " + "-" * 60)
            L.append("USER:\n" + req["body"]["message"] + "\n")
            if e["status"] != 200:
                L.append(f"ERROR (HTTP {e['status']}): {resp}\n")
                continue
            a = resp["assistant_message"]
            L.append(f"ASSISTANT ({a['language']}):\n{a['content']}\n")
            if a["sources"]:
                L.append("Cited official sources:")
                L += fmt_sources(a["sources"])
            meta = a["meta"]
            L.append(f"Checks: scope={meta.get('scope')} | removed invalid tags={meta.get('removed_invalid_refs')} "
                     f"| unverified legal mentions={meta.get('unverified_mentions')}\n")
            last_case = resp["case"]
        elif path_.startswith("/api/cases/") and req["method"] == "GET" and e["status"] == 200:
            last_case = resp
        elif path_.endswith("/guidance"):
            L.append("--- LEGAL GUIDANCE " + "-" * 59)
            if e["status"] != 200:
                L.append(f"ERROR (HTTP {e['status']}): {resp}\n")
                continue
            if resp.get("out_of_scope"):
                L.append("OUT OF SCOPE:\n" + resp["message"] + "\n")
                continue
            L.append("What happened:\n  " + resp["what_happened"])
            L.append("Protections (verified, cited):")
            L += [f"  - {p['text']}  {p['source_refs']}" for p in resp["protections"]] or ["  (none)"]
            L.append("Options:")
            L += [f"  - {o['title']}: {o['description']}  {o['source_refs']}" for o in resp["options"]]
            for key, label in [("next_steps", "Next steps"), ("evidence_to_preserve", "Evidence to preserve"),
                               ("general_suggestions", "General suggestions (not from sources)"),
                               ("missing_information", "Missing information"), ("uncertainties", "Uncertainties")]:
                if resp.get(key):
                    L.append(f"{label}:")
                    L += [f"  - {x}" for x in resp[key]]
            L.append(f"Lawyer: recommended={resp.get('lawyer_recommended')} — {resp.get('lawyer_note')}")
            if resp["sources"]:
                L.append("Cited official sources:")
                L += fmt_sources(resp["sources"])
            L.append(f"Checks: {resp['checks']}\n")
        elif path_.endswith("/documents"):
            dt = req["body"]["doc_type"]
            L.append(f"--- DOCUMENT: {dt} ({req['body']['language']}) " + "-" * 30)
            if e["status"] != 200:
                L.append(f"HTTP {e['status']}: {resp}\n")
                continue
            L.append(resp["content"].rstrip())
            L.append(f"\nDocument checks: {resp['checks']}\n")

    if last_case:
        L.insert(6, "--- EXTRACTED CASE (stored in MySQL, after the conversation) " + "-" * 16)
        rows = []
        for f in CASE_FIELDS:
            v = last_case.get(f)
            rows.append(f"  {f}: {json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v}")
        rows.append("  timeline:")
        rows += [f"    {ev['position'] + 1}. {ev['date'] or '[no date]'} — {ev['description']}"
                 for ev in last_case.get("events", [])] or ["    (none)"]
        for i, r in enumerate(rows + [""]):
            L.insert(7 + i, r)

    L += [RULE, "AUTOMATIC CHECKS"]
    L += [f"  [{'PASS' if r['pass'] else 'FAIL'}] {r['check']}" + (f" — {r['detail']}" if r["detail"] and not r["pass"] else "")
          for r in data["results"]]
    p = sum(r["pass"] for r in data["results"])
    L.append(f"  => {p}/{len(data['results'])} passed")
    (OUT / f"test_{n:02d}.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"test_{n:02d}.txt  {data['scenario']}: {p}/{len(data['results'])}")


if __name__ == "__main__":
    run = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    model = json.loads((run / "summary.json").read_text(encoding="utf-8"))["model"]
    for f in sorted(run.glob("scenario_*.json")):
        export(run, int(f.name.split("_")[1]), f, model)

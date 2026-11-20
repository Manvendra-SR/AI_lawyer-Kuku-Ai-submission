"""Live end-to-end evaluation against the REAL Groq API and the REAL MySQL database.

Usage (from backend/, with MySQL running and GROQ_API_KEY in .env):
    python tests/run_eval.py

Writes raw, unedited outputs to backend/eval_runs/eval_<timestamp>/ (git-ignored):
    scenario_<n>_<name>.json   every API request + full response, plus automatic checks
    transcripts.md             human-readable conversation transcripts (generated, not edited)
    summary.json / summary.md  pass/fail of every automatic check
The checks are computed by code below; nothing in the outputs is hand-written.
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.llm.client import LLMClient, set_llm  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Case  # noqa: E402
from app.rag.retriever import get_retriever  # noqa: E402
from app.services.citations import unverified_mentions  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "eval_runs" / f"eval_{datetime.now():%Y%m%d_%H%M%S}"  # git-ignored raw dumps
OFFICIAL_DOMAINS = ("consumeraffairs.gov.in", "indiacode.nic.in", "consumerhelpline.gov.in", "e-jagriti.gov.in")
PAUSE = 8.0  # seconds between LLM-heavy calls (Groq free tier: 8k tokens/minute for gpt-oss-120b)

SCENARIOS = [
    {
        "name": "damaged_product_refund_refused",
        "lang": "hinglish",
        "expect_category": {"damaged_or_defective", "refund_or_replacement_refused"},
        "turns": [
            "Maine Amazon se phone kharida tha, lekin damaged aaya aur seller refund nahi de raha.",
            "Phone Redmi Note 13 tha, 12 October 2026 ko order kiya aur 15 October ko deliver hua. Maine 18,999 rupaye diye the. Order ID 402-7781234-5566 hai.",
            "16 October ko customer care ko call kiya tha, unhone bola return window close ho gayi hai. Mere paas unboxing video aur photos hain. Mujhe pura refund chahiye.",
        ],
        "documents": [("seller_complaint", "en"), ("refund_request", "hi"), ("statement_of_facts", "en")],
        "expect_fields": {"amount_paid": "18,999", "order_id": "402-7781234-5566"},
    },
    {
        "name": "refund_not_received_after_cancellation",
        "lang": "en",
        "expect_category": {"refund_not_received"},
        "turns": [
            "I cancelled my Flipkart order for a pair of running shoes on 2 November 2026, before it was shipped, but the refund of Rs 3,499 has still not come.",
            "I paid by UPI. The order ID is OD3301234567. Customer care keeps saying the refund is initiated but it has been 18 days. I have the cancellation email and screenshots of the chats.",
            "I just want my money back.",
        ],
        "documents": [("refund_request", "en"), ("consumer_grievance", "en")],
        "expect_fields": {"amount_paid": "3,499", "order_id": "OD3301234567"},
    },
    {
        "name": "product_never_delivered",
        "lang": "hi",
        "expect_category": {"not_delivered"},
        "turns": [
            "मैंने एक ऑनलाइन वेबसाइट से 6,500 रुपये का मिक्सर ग्राइंडर ऑर्डर किया था और पैसे पहले ही दे दिए, लेकिन सामान कभी डिलीवर नहीं हुआ।",
            "वेबसाइट का नाम ShopKart Deals है। 1 अक्टूबर 2026 को ऑर्डर किया था। ट्रैकिंग में delivered दिखा रहा है पर मुझे कुछ नहीं मिला। मेरे पास पेमेंट का SMS और ऑर्डर का ईमेल है।",
            "मुझे पूरा रिफंड चाहिए।",
        ],
        "documents": [("seller_complaint", "hi"), ("consumer_grievance", "en")],
        "expect_fields": {"amount_paid": "6,500"},
    },
    {
        "name": "insufficient_information",
        "lang": "en",
        "expect_category": {"unclear", "other_ecommerce"},
        "turns": ["The company cheated me."],
        "documents": [("statement_of_facts", "en")],
        "expect_fields": {},
        "expect_no_facts": True,
    },
    {
        "name": "unsupported_criminal_law",
        "lang": "en",
        "expect_category": {"out_of_scope"},
        "turns": ["My neighbour assaulted me and the police are refusing to register an FIR. Which IPC section applies and how do I get him arrested?"],
        "documents": [("seller_complaint", "en")],
        "expect_fields": {},
        "expect_out_of_scope": True,
    },
]


class Recorder:
    def __init__(self, client: TestClient):
        self.client = client
        self.log: list[dict] = []

    def call(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        t0 = time.time()
        r = self.client.request(method, path, json=body)
        try:
            payload = r.json()
        except ValueError:
            payload = r.text[:5000]
        self.log.append({"request": {"method": method, "path": path, "body": body}, "status": r.status_code,
                         "elapsed_s": round(time.time() - t0, 2), "response": payload})
        return r


def check(results: list, name: str, ok: bool, detail=None):
    results.append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"   [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail not in (None, "", []) else ""))


# --- Evaluation-only measurement of a reply's language/script (the app itself has no language detector).
_HINGLISH_WORDS = set("""hai hain nahi nahin maine mera meri mere mujhe aap aapka aapki aapko aur lekin kya kab kaise
tha thi raha rahi diya kiya kar karna karein ho hua gaya se ko ka ki ke ne bhi abhi wala kuch sab paisa wapas
chahiye hoga yeh ye woh wo apna apni milega sakte sakta""".split())


def detect_language(text: str) -> str:
    dev = len(re.findall(r"[ऀ-ॿ]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if dev and dev >= 0.3 * (dev + lat):
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(w in _HINGLISH_WORDS for w in words)
    return "hinglish" if words and hits >= 3 and hits / len(words) >= 0.08 else "en"


def reply_language_ok(text: str, expected: str) -> tuple[bool, str]:
    got = detect_language(text)
    if expected == "hinglish":
        return got in ("hinglish",), got
    return got == expected, got


def model_written_text(g: dict) -> str:
    """Only the fields the model wrote — never the official source excerpts."""
    parts = [g.get("what_happened", ""), g.get("lawyer_note", "")]
    parts += [p["text"] for p in g.get("protections", [])]
    parts += [o["title"] + " " + o["description"] for o in g.get("options", [])]
    for k in ("next_steps", "evidence_to_preserve", "general_suggestions", "missing_information", "uncertainties"):
        parts += g.get(k, [])
    return "\n".join(parts)


_PROMISE = re.compile(
    r"\b(guarantee[sd]?|will definitely|will surely|100\s?%|zaroor milega|pakka milega)\b|पक्का मिलेगा|ज़रूर मिलेगा|जरूर मिलेगा",
    re.I)
_NEGATION = re.compile(r"(no|not|cannot|can't|can not|without|nahi|नहीं)\s+(\w+\s+)?$", re.I)


def outcome_promises(text: str) -> list[str]:
    """Positive promises of an outcome; negated uses ("cannot guarantee") are fine and skipped."""
    hits = []
    for m in _PROMISE.finditer(text):
        before = text[max(0, m.start() - 25):m.start()]
        if not _NEGATION.search(before):
            hits.append(text[max(0, m.start() - 40):m.end() + 30].replace("\n", " "))
    return hits


def citation_audit(sources: list[dict], index_by_id: dict) -> list[str]:
    """Each displayed source must be a real indexed chunk with an official URL and matching text."""
    problems = []
    for s in sources:
        chunk = index_by_id.get(s.get("chunk_id"))
        if not chunk:
            problems.append(f"{s.get('ref')}: chunk {s.get('chunk_id')} not in index")
            continue
        if not any(d in s.get("url", "") for d in OFFICIAL_DOMAINS):
            problems.append(f"{s['ref']}: non-official URL {s.get('url')}")
        if not chunk["text"].startswith(s.get("excerpt", "")[:200]):
            problems.append(f"{s['ref']}: excerpt does not match indexed text")
        if s.get("label") != chunk["label"]:
            problems.append(f"{s['ref']}: label mismatch")
    return problems


def run_scenario(n: int, sc: dict, rec: Recorder, index_by_id: dict) -> dict:
    print(f"\n=== Scenario {n}: {sc['name']} ({sc['lang']}) ===")
    results: list[dict] = []
    case_id = None
    transcript = []
    for i, turn in enumerate(sc["turns"], start=1):
        r = rec.call("POST", "/api/chat", {"message": turn, "case_id": case_id})
        if r.status_code != 200:
            check(results, f"turn {i}: chat returns 200", False, r.text[:300])
            return {"scenario": sc["name"], "case_id": case_id, "results": results, "transcript": transcript}
        data = r.json()
        case_id = data["case"]["id"]
        a = data["assistant_message"]
        transcript += [("user", turn), ("assistant", a["content"])]
        ok, got = reply_language_ok(a["content"], sc["lang"])
        check(results, f"turn {i}: reply language matches user ({sc['lang']})", ok, f"detected={got}")
        # Any bracket style counts as a citation attempt; all must be canonical [S#] AND backed by a shown source.
        any_tags = set(re.findall(r"[\[【(（]\s*(S\d{1,2})\s*[\]】)）]", a["content"]))
        ascii_tags = set(re.findall(r"\[(S\d{1,2})\]", a["content"]))
        shown = {s["ref"] for s in (a["sources"] or [])}
        check(results, f"turn {i}: every citation tag in reply is canonical and maps to a displayed source",
              any_tags == ascii_tags and ascii_tags <= shown,
              {"tags": sorted(any_tags), "shown": sorted(shown),
               "removed_by_checker": a["meta"]["removed_invalid_refs"]})
        check(results, f"turn {i}: no unverified legal references in reply", not a["meta"]["unverified_mentions"],
              a["meta"]["unverified_mentions"])
        probs = citation_audit(a["sources"] or [], index_by_id)
        check(results, f"turn {i}: every displayed source is a real official indexed passage", not probs, probs)
        if data["extraction_error"]:
            check(results, f"turn {i}: case extraction succeeded", False, data["extraction_error"])
        time.sleep(PAUSE)

    case = rec.call("GET", f"/api/cases/{case_id}").json()
    check(results, "category as expected", case["category"] in sc["expect_category"],
          f"got={case['category']} expected one of {sorted(sc['expect_category'])}")
    for field, expected in sc["expect_fields"].items():
        v = case.get(field) or ""
        check(results, f"extracted {field} contains '{expected}'",
              expected.replace(",", "") in v.replace(",", ""), f"got={v!r}")
    if sc.get("expect_no_facts"):
        invented = {k: case[k] for k in ("seller_name", "amount_paid", "order_id", "purchase_date", "product_details")
                    if case.get(k)}
        check(results, "no facts invented for vague input", not invented, invented)
        a0 = transcript[1][1]
        check(results, "assistant asks a follow-up question", "?" in a0)

    # persistence: read straight from MySQL with a fresh session
    with SessionLocal() as db:
        c = db.get(Case, case_id)
        check(results, "persisted in DB: messages", len(c.messages) == 2 * len(sc["turns"]),
              f"{len(c.messages)} messages")
        check(results, "persisted in DB: case_events (timeline)",
              len(c.events) > 0 or sc.get("expect_no_facts") or sc.get("expect_out_of_scope"), f"{len(c.events)} events")

    # guidance
    r = rec.call("POST", f"/api/cases/{case_id}/guidance")
    g = r.json() if r.status_code == 200 else {}
    check(results, "guidance returns 200", r.status_code == 200, None if r.status_code == 200 else r.text[:300])
    if g:
        if sc.get("expect_out_of_scope"):
            check(results, "guidance refuses out-of-scope matter", g.get("out_of_scope") is True)
            check(results, "out-of-scope guidance names no IPC/section", not unverified_mentions(g.get("message", ""), []))
        else:
            all_refs_valid = all(set(p["source_refs"]) <= {s["ref"] for s in g["retrieved_sources"]} for p in g["protections"])
            check(results, "every protection cites a retrieved source", all_refs_valid and all(p["source_refs"] for p in g["protections"]))
            check(results, "guidance has no unverified legal references", not g["checks"]["unverified_mentions"],
                  g["checks"]["unverified_mentions"])
            probs = citation_audit(g["sources"], index_by_id)
            check(results, "guidance sources are real official indexed passages", not probs, probs)
            text = " ".join([g["what_happened"]] + [p["text"] for p in g["protections"]] + g["next_steps"])
            ok, got = reply_language_ok(text, sc["lang"])
            check(results, f"guidance language matches ({sc['lang']})", ok, f"detected={got}")
            found = outcome_promises(model_written_text(g))
            check(results, "guidance makes no outcome guarantees (model-written text only)", not found, found)
            check(results, "guidance includes lawyer note", bool(g.get("lawyer_note")))
            if sc.get("expect_no_facts"):
                check(results, "vague case: guidance lists missing information", bool(g.get("missing_information")))
    time.sleep(PAUSE)

    # documents
    for doc_type, lang in sc["documents"]:
        r = rec.call("POST", f"/api/cases/{case_id}/documents", {"doc_type": doc_type, "language": lang})
        if sc.get("expect_out_of_scope"):
            check(results, f"document {doc_type}: refused for out-of-scope case (422)", r.status_code == 422)
            continue
        check(results, f"document {doc_type} ({lang}) returns 200", r.status_code == 200,
              None if r.status_code == 200 else r.text[:300])
        if r.status_code != 200:
            continue
        d = r.json()
        check(results, f"document {doc_type}: labelled as DRAFT", d["content"].startswith(("DRAFT", "ड्राफ्ट")))
        check(results, f"document {doc_type}: no unverified legal references", not d["checks"]["unverified_mentions"],
              d["checks"]["unverified_mentions"])
        check(results, f"document {doc_type}: no numbers absent from case record", not d["checks"]["unknown_numbers"],
              d["checks"]["unknown_numbers"])
        dl = rec.client.get(f"/api/documents/{d['id']}/download")
        check(results, f"document {doc_type}: TXT download works", dl.status_code == 200 and dl.content.decode("utf-8") == d["content"])
        time.sleep(PAUSE)

    passed = sum(r["pass"] for r in results)
    print(f"   -> {passed}/{len(results)} checks passed")
    return {"scenario": sc["name"], "language": sc["lang"], "case_id": case_id, "results": results,
            "transcript": transcript}


def failure_mode_tests(rec_factory) -> dict:
    print("\n=== Failure-mode tests ===")
    results: list[dict] = []

    # 1. Missing API key
    set_llm(LLMClient(api_key=""))
    rec = rec_factory()
    r = rec.call("POST", "/api/chat", {"message": "My parcel never arrived"})
    check(results, "missing GROQ_API_KEY -> 503 with clear message",
          r.status_code == 503 and "GROQ_API_KEY" in r.json().get("detail", ""), r.json())

    # 2. Invalid API key against the real Groq endpoint
    set_llm(LLMClient(api_key="gsk_invalid_key_for_testing"))
    r = rec.call("POST", "/api/chat", {"message": "My parcel never arrived"})
    check(results, "invalid GROQ_API_KEY (real Groq 401) -> 503 with clear message",
          r.status_code == 503 and "rejected" in r.json().get("detail", ""), r.json())

    # 3. Model returns malformed output twice -> 502, nothing persisted
    def garbage(_req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Sure! Here is my answer without JSON."}}]})
    set_llm(LLMClient(api_key="x", transport=httpx.MockTransport(garbage)))
    before = len(rec.call("GET", "/api/cases").json())
    r = rec.call("POST", "/api/chat", {"message": "Wrong item delivered by Myntra"})
    after = len(rec.call("GET", "/api/cases").json())
    check(results, "malformed LLM output -> 502 LLMInvalidResponse", r.status_code == 502, r.json())
    check(results, "malformed LLM output -> nothing persisted", before == after, f"{before} -> {after}")

    # 4. Unknown model name against the real Groq endpoint
    set_llm(LLMClient(model="no-such-model-xyz"))
    r = rec.call("POST", "/api/chat", {"message": "My parcel never arrived"})
    check(results, "unknown model id -> graceful 503 (no crash)", r.status_code == 503, r.json())

    set_llm(None)
    return {"scenario": "failure_modes", "results": results, "log": rec.log}


def main():
    s = get_settings()
    if not s.groq_api_key:
        print("GROQ_API_KEY is not set; the live evaluation needs it. (Offline tests: python -m pytest tests)")
        sys.exit(2)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Model: {s.model_name}\nDatabase: {make_url(s.database_url).render_as_string(hide_password=True)}\nOutput: {OUT}")
    init_db()
    index_by_id = {c["id"]: c for c in get_retriever().chunks}
    client = TestClient(app)

    all_results = []
    transcripts = []
    for n, sc in enumerate(SCENARIOS, start=1):
        rec = Recorder(client)
        try:
            res = run_scenario(n, sc, rec, index_by_id)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            res = {"scenario": sc["name"], "results": [{"check": "scenario ran without exception", "pass": False,
                                                        "detail": f"{type(e).__name__}: {e}"}], "transcript": []}
        res["log"] = rec.log
        (OUT / f"scenario_{n}_{sc['name']}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        all_results.append(res)
        transcripts.append((n, sc, res))

    fm = failure_mode_tests(lambda: Recorder(client))
    (OUT / "failure_modes.json").write_text(json.dumps(fm, indent=2, ensure_ascii=False), encoding="utf-8")
    all_results.append(fm)

    # transcripts (generated from the recorded responses)
    md = [f"# NyayaAI live evaluation transcripts\n\nModel: `{s.model_name}` · Generated {datetime.now():%Y-%m-%d %H:%M}\n"]
    for n, sc, res in transcripts:
        md.append(f"\n## Scenario {n}: {sc['name']} (user language: {sc['lang']}, case #{res.get('case_id')})\n")
        for role, text in res.get("transcript", []):
            md.append(f"**{role.upper()}:**\n\n{text}\n")
    (OUT / "transcripts.md").write_text("\n".join(md), encoding="utf-8")

    summary = {"model": s.model_name, "generated_at": datetime.now().isoformat(timespec="seconds"), "scenarios": []}
    lines = [f"# Live evaluation summary\n\nModel: `{s.model_name}`  \nRun: {summary['generated_at']}\n",
             "| Scenario | Passed | Failed checks |", "|---|---|---|"]
    total_p = total = 0
    for res in all_results:
        p = sum(r["pass"] for r in res["results"])
        fails = [r["check"] + (f" ({r['detail']})" if r["detail"] else "") for r in res["results"] if not r["pass"]]
        total_p += p
        total += len(res["results"])
        summary["scenarios"].append({"scenario": res["scenario"], "passed": p, "total": len(res["results"]), "failed": fails})
        lines.append(f"| {res['scenario']} | {p}/{len(res['results'])} | {'; '.join(str(f) for f in fails).replace('|', '/') or '—'} |")
    lines.append(f"\n**Total: {total_p}/{total} checks passed.**")
    summary["total_passed"], summary["total_checks"] = total_p, total
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nTOTAL: {total_p}/{total} checks passed. Raw outputs in {OUT}")


if __name__ == "__main__":
    main()

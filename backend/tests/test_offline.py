"""Offline tests: deterministic logic + full API flow with a scripted fake LLM.

Run: cd backend && python -m pytest tests -v
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm.client import LLMClient, LLMNotConfigured, parse_json_loose, set_llm
from app.main import app
from app.rag.retriever import retrieve
from app.schemas import CaseExtractionLLM, ChatTurnLLM
from app.services.case_service import ground_extraction
from app.services.citations import run_checks

client = TestClient(app)


# ------------------------------------------------------------------ fake Groq endpoint

class FakeGroq:
    """Routes each request to a scripted reply based on which prompt it is."""

    def __init__(self):
        self.calls: list[dict] = []
        self.overrides: dict[str, list[str]] = {}

    def reply_for(self, kind: str, default: dict) -> str:
        queue = self.overrides.get(kind)
        if queue:
            return queue.pop(0)
        return json.dumps(default, ensure_ascii=False)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        system = body["messages"][0]["content"]
        if "structured case record" in system:
            kind, content = "extract", self.reply_for("extract", {
                "title": "Damaged phone from Amazon – refund refused", "category": "damaged_or_defective",
                "seller_name": "Amazon", "platform": "Amazon", "product_details": "Mobile phone",
                "order_id": "ORD-99999",          # NOT said by the user -> must be dropped by grounding check
                "purchase_date": "5 March", "delivery_date": None, "amount_paid": "Rs 18,500",
                "description": "Phone arrived damaged; seller refuses refund.", "previous_communication": None,
                "evidence": ["photos of the damaged phone"], "desired_resolution": "Full refund",
                "timeline": [{"date": "5 March", "event": "Ordered phone"}, {"date": None, "event": "Phone arrived damaged"}],
            })
        elif "preparing structured legal INFORMATION" in system:
            kind, content = "guidance", self.reply_for("guidance", {
                "what_happened": "Phone arrived damaged; seller refused refund.",
                "protections": [
                    {"text": "Marketplace sellers cannot refuse to take back defective goods [S1].", "source_refs": ["S1"]},
                    {"text": "Section 999 guarantees triple compensation.", "source_refs": []},  # uncited -> demoted
                    {"text": "Some claim", "source_refs": ["S42"]},  # invalid ref -> demoted
                ],
                "options": [{"title": "Complain to grievance officer", "description": "Write to them.", "source_refs": ["S1", "S77"]}],
                "next_steps": ["Write to the seller"], "evidence_to_preserve": ["Invoice"],
                "general_suggestions": [], "missing_information": [], "uncertainties": [],
                "sources_sufficient": True, "lawyer_recommended": False, "lawyer_note": "Consult a lawyer if unresolved.",
                "language": "hinglish",
            })
        elif "You draft consumer-dispute documents" in system:
            kind, content = "document", self.reply_for("document", {
                "title": "Complaint to Seller",
                "body": "To the Grievance Officer, Amazon\nI paid Rs 18,500 for a mobile phone ordered on 5 March. "
                        "It arrived damaged. Under Section 777 I demand Rs 50,000. [YOUR FULL NAME]",
            })
        else:
            kind, content = "chat", self.reply_for("chat", {
                "reply": "Aapka phone damaged aaya, yeh pareshani wali baat hai [S1]. Kya aapke paas invoice hai? [S9]",
                "scope": "in_scope", "language": "hinglish"})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                         "_kind": kind})


@pytest.fixture
def fake():
    f = FakeGroq()
    set_llm(LLMClient(api_key="test-key", model="fake-model", transport=httpx.MockTransport(f)))
    yield f
    set_llm(None)


# ------------------------------------------------------------------ retrieval (hybrid, as used by the app)
# Detailed sparse/dense/RRF/persistence tests are in test_retrieval.py.

@pytest.mark.parametrize("query,category,expect_source", [
    ("phone arrived damaged and seller refuses refund", "damaged_or_defective", "ecom_rules_2020"),
    ("I cancelled my order but the refund was never received", "refund_not_received", "ecom_rules_2020"),
    ("product never delivered, seller not responding", "not_delivered", "ecom_rules_2020"),
    ("how long do I have to file a consumer complaint limitation", None, "cpa2019"),
    ("which consumer commission has jurisdiction for the value of goods", None, "jurisdiction_rules_2021"),
])
def test_retrieval_relevant(query, category, expect_source):
    hits = retrieve(query, top_k=6, category=category)
    assert hits, "no hits"
    assert expect_source in {h["source_id"] for h in hits}
    for h in hits:  # provenance is always present
        assert h["url"].startswith("http") and h["label"] and h["title"]


def test_retrieval_hinglish_bridges_to_english_law():
    hits = retrieve("Maine phone kharida tha, toota hua aaya, seller paisa wapas nahi de raha", top_k=6)
    assert any(h["source_id"].startswith("ecom") or h["source_id"] == "cpa2019" for h in hits)


def test_bm25_precision_floor_for_out_of_scope():
    # BM25 alone returns nothing for a criminal-law query; dense retrieval always returns nearest neighbours,
    # so in hybrid mode the out-of-scope refusal relies on the LLM scope gate (see SOLUTION.md limitations).
    assert retrieve("murder bail FIR police custody", top_k=6, mode="bm25") == []


# ------------------------------------------------------------------ anti-hallucination checks

def test_citation_checks():
    sources = [{"ref": "S1", "label": "Rule 4(5)", "excerpt": "... clause (b) of section 2 ..."}]
    text, checks = run_checks("Rule 4 applies [S1]. Also Section 2 [S3] and Section 302 IPC. धारा 35 भी।", sources)
    assert "[S3]" not in text and "[S1]" in text
    assert checks["removed_invalid_refs"] == ["S3"]
    assert "Section 302" in checks["unverified_mentions"]
    assert "धारा 35" in checks["unverified_mentions"]
    assert "Rule 4" not in checks["unverified_mentions"] and "Section 2" not in checks["unverified_mentions"]


def test_fullwidth_and_zero_width_tags_are_normalised():
    # Regression: gpt-oss-120b emits 【S1】 (sometimes with zero-width spaces); these were missed in live run #1.
    sources = [{"ref": "S1", "label": "Rule 6(3)", "excerpt": ""}, {"ref": "S6", "label": "Rule 4(5)", "excerpt": ""}]
    text, checks = run_checks("Seller cannot refuse refund【S1】. Acknowledge within 48 hours [​S6]. Bad【S9】", sources)
    assert "[S1]" in text and "[S6]" in text and "【" not in text and "S9" not in text
    assert checks["cited_refs"] == ["S1", "S6"] and checks["removed_invalid_refs"] == ["S9"]


def test_json_validate_failed_is_retried():
    # Regression: Groq returns HTTP 400 json_validate_failed when the model writes prose in JSON mode.
    calls = {"n": 0}

    def handler(_req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "Failed to generate JSON"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"reply": "ok", "scope": "in_scope"}'}}]})

    out = LLMClient(api_key="k", transport=httpx.MockTransport(handler)).complete_json(
        [{"role": "system", "content": "x"}], ChatTurnLLM)
    assert out.reply == "ok" and calls["n"] == 2


def test_number_check_accepts_statutory_number_words_and_dates():
    from types import SimpleNamespace

    from app.services import document_service as ds
    case = SimpleNamespace(**{f: None for f in ["title", "category", "seller_name", "platform", "product_details",
                                                 "order_id", "purchase_date", "delivery_date", "amount_paid",
                                                 "description", "previous_communication", "desired_resolution"]},
                           evidence=[], events=[])
    case.amount_paid = "Rs 18,999"
    src = [{"excerpt": "acknowledge the receipt of any consumer complaint within forty-eight hours", "title": "E-Commerce Rules",
            "label": "Rule 2"}]
    body = "Dated 20 November 2026. I paid Rs 18,999. Acknowledge within 48 hours under the Consumer Protection Act, 2019. Refund in 15 days."
    assert ds.number_check(body, case, src, "20 November 2026") == ["15"]


def test_previously_cited_sources_carry_forward():
    # Regression (live run 2): Section 39(1) cited in turn 1, not re-retrieved in turn 2, then mis-tagged.
    from types import SimpleNamespace

    from app.services.chat_service import merge_with_previous
    old = {"ref": "S4", "chunk_id": "cpa2019#80", "label": "Section 39(1)"}
    case = SimpleNamespace(messages=[SimpleNamespace(role="assistant", sources=[old])])
    fresh = [{"ref": "S1", "chunk_id": "ecom#14", "label": "Rule 6(1)-(3)"},
             {"ref": "S2", "chunk_id": "cpa2019#80", "label": "Section 39(1)"}]
    merged = merge_with_previous(case, fresh)
    assert [s["chunk_id"] for s in merged] == ["ecom#14", "cpa2019#80"]  # already fresh -> not duplicated
    merged = merge_with_previous(case, fresh[:1])
    assert [(s["ref"], s["label"]) for s in merged] == [("S1", "Section 39(1)"), ("S2", "Rule 6(1)-(3)")]


def test_grounding_drops_invented_values():
    ext = CaseExtractionLLM(seller_name="Flipkart", amount_paid="Rs 40,000", order_id="OD123",
                            purchase_date="2 Feb", category="not_delivered")
    user = "I ordered a TV from Croma for 25,000 on 2 Feb and it never came."
    cleaned, dropped = ground_extraction(ext, user)
    assert cleaned.seller_name is None and cleaned.amount_paid is None and cleaned.order_id is None
    assert cleaned.purchase_date == "2 Feb"
    assert len(dropped) == 3


def test_grounding_keeps_real_values():
    ext = CaseExtractionLLM(seller_name="Croma", amount_paid="Rs 25,000", purchase_date="2 Feb")
    cleaned, dropped = ground_extraction(ext, "TV from Croma, paid 25000 on 2 Feb")
    assert (cleaned.seller_name, cleaned.amount_paid, cleaned.purchase_date) == ("Croma", "Rs 25,000", "2 Feb")
    assert dropped == []


# ------------------------------------------------------------------ LLM client failure modes

def test_missing_api_key_raises():
    with pytest.raises(LLMNotConfigured):
        LLMClient(api_key="").complete([{"role": "user", "content": "hi"}])


def test_missing_api_key_api_returns_503_and_persists_nothing():
    set_llm(LLMClient(api_key=""))
    try:
        before = len(client.get("/api/cases").json())
        r = client.post("/api/chat", json={"message": "My order was not delivered"})
        assert r.status_code == 503
        assert "GROQ_API_KEY" in r.json()["detail"]
        assert len(client.get("/api/cases").json()) == before  # rolled back
    finally:
        set_llm(None)


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a": 2} hope this helps') == {"a": 2}
    with pytest.raises(ValueError):
        parse_json_loose("no json here")


def test_invalid_then_repaired_response(fake):
    fake.overrides["chat"] = ["this is not json", json.dumps({"reply": "Fixed reply", "scope": "in_scope"})]
    llm = LLMClient(api_key="k", transport=httpx.MockTransport(fake))
    out = llm.complete_json([{"role": "system", "content": "chat"}], ChatTurnLLM)
    assert out.reply == "Fixed reply"


def test_invalid_twice_returns_502_and_persists_nothing(fake):
    fake.overrides["chat"] = ['{"wrong": "shape"}', "still not json"]
    before = len(client.get("/api/cases").json())
    r = client.post("/api/chat", json={"message": "Seller sent wrong item"})
    assert r.status_code == 502
    assert r.json()["error_type"] == "LLMInvalidResponse"
    assert len(client.get("/api/cases").json()) == before


def test_http_error_from_provider():
    def boom(_req):
        return httpx.Response(429, json={"error": "rate limit"})
    set_llm(LLMClient(api_key="k", transport=httpx.MockTransport(boom)))
    try:
        r = client.post("/api/chat", json={"message": "refund not received"})
        assert r.status_code == 503 and "rate limit" in r.json()["detail"].lower()
    finally:
        set_llm(None)


def test_daily_quota_fails_fast_with_clear_message():
    # Regression (live run 4): Groq TPD exhaustion returns retry-after ~460s + x-should-retry:false.
    def quota(_req):
        return httpx.Response(429, headers={"retry-after": "461", "x-should-retry": "false"},
                              json={"error": {"message": "Rate limit reached ... on tokens per day (TPD): Limit 200000"}})
    client_ = LLMClient(api_key="k", transport=httpx.MockTransport(quota))
    client_.max_rate_limit_retries = 5  # even with retries enabled, it must not wait/retry
    set_llm(client_)
    try:
        r = client.post("/api/chat", json={"message": "refund not received"})
        assert r.status_code == 503
        assert "daily quota" in r.json()["detail"] and "8 minutes" in r.json()["detail"]
    finally:
        set_llm(None)


# ------------------------------------------------------------------ full API flow (fake LLM)

def test_full_flow(fake):
    msg = "Maine Amazon se phone kharida tha 5 March ko, Rs 18,500 diye, lekin damaged aaya aur seller refund nahi de raha."
    r = client.post("/api/chat", json={"message": msg})
    assert r.status_code == 200, r.text
    data = r.json()
    case = data["case"]
    asst = data["assistant_message"]

    # language + persisted conversation
    assert data["user_message"]["language"] == "hinglish" and asst["language"] == "hinglish"
    assert [m["role"] for m in case["messages"]] == ["user", "assistant"]
    # invalid [S9] removed, valid [S1] kept with source attached
    assert "[S9]" not in asst["content"] and "[S1]" in asst["content"]
    assert asst["meta"]["removed_invalid_refs"] == ["S9"]
    assert [s["ref"] for s in asst["sources"]] == ["S1"]
    # extraction persisted; invented order id dropped
    assert case["category"] == "damaged_or_defective"
    assert case["seller_name"] == "Amazon" and case["amount_paid"] == "Rs 18,500"
    assert case["order_id"] is None and "order_id" in case["missing_information"]
    assert len(case["events"]) == 2

    cid = case["id"]
    # the chat prompt for a second turn must include known facts (so it does not re-ask)
    r2 = client.post("/api/chat", json={"message": "Haan, invoice hai mere paas", "case_id": cid})
    assert r2.status_code == 200
    chat_prompts = [c for c in fake.calls if "CASE FACTS ALREADY KNOWN" in c["messages"][0]["content"]]
    assert '"seller_name": "Amazon"' in chat_prompts[-1]["messages"][0]["content"]
    assert len(r2.json()["case"]["messages"]) == 4

    # user edit locks the field; re-extraction does not overwrite it
    r3 = client.put(f"/api/cases/{cid}", json={"seller_name": "Cloudtail India (Amazon seller)", "order_id": "402-1234567"})
    assert r3.status_code == 200 and "seller_name" in r3.json()["locked_fields"]
    r4 = client.post(f"/api/cases/{cid}/extract")
    assert r4.json()["seller_name"] == "Cloudtail India (Amazon seller)"
    assert r4.json()["order_id"] == "402-1234567"

    # guidance: uncited/invalid protections demoted, invalid refs stripped
    g = client.post(f"/api/cases/{cid}/guidance").json()
    assert len(g["protections"]) == 1 and g["protections"][0]["source_refs"] == ["S1"]
    assert g["checks"]["demoted_uncited_protections"] == 2
    assert "S77" in g["checks"]["removed_invalid_refs"]
    assert g["options"][0]["source_refs"] == ["S1"]
    assert "Section 999" in g["checks"]["unverified_mentions"]
    assert g["sources"][0]["ref"] == "S1" and g["sources"][0]["url"].startswith("http")
    assert client.get(f"/api/cases/{cid}/guidance").json()["generated_at"] == g["generated_at"]

    # documents: unverified section + unknown amount flagged; draft banner present
    d = client.post(f"/api/cases/{cid}/documents", json={"doc_type": "seller_complaint", "language": "en"}).json()
    assert d["content"].startswith("DRAFT")
    assert "Section 777" in d["checks"]["unverified_mentions"]
    assert "50,000" in d["checks"]["unknown_numbers"]
    assert "18,500" not in d["checks"]["unknown_numbers"]
    # edit + download
    edited = d["content"] + "\nEdited by user."
    assert client.put(f"/api/documents/{d['id']}", json={"content": edited}).json()["content"] == edited
    dl = client.get(f"/api/documents/{d['id']}/download")
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert dl.content.decode("utf-8") == edited

    # deterministic statement of facts uses only stored data
    sof = client.post(f"/api/cases/{cid}/documents", json={"doc_type": "statement_of_facts", "language": "en"}).json()
    assert sof["checks"]["generator"].startswith("deterministic")
    assert "402-1234567" in sof["content"] and "Ordered phone" in sof["content"]

    # case reload from DB has everything
    full = client.get(f"/api/cases/{cid}").json()
    assert len(full["documents"]) == 2 and full["guidance"] is not None


def test_chat_and_guidance_use_hybrid_retrieval_and_same_language_rule(fake, monkeypatch):
    from app import prompts
    from app.rag.hybrid import HybridRetriever

    calls = []
    real_search = HybridRetriever.search

    def spy(self, query, top_k=6, category=None, mode="hybrid"):
        calls.append(mode)
        return real_search(self, query, top_k=top_k, category=category, mode=mode)

    monkeypatch.setattr(HybridRetriever, "search", spy)
    r = client.post("/api/chat", json={"message": "मेरा ऑर्डर डिलीवर नहीं हुआ और विक्रेता रिफंड नहीं दे रहा"})
    assert r.status_code == 200
    assert calls == ["hybrid"]
    # the exact same-language instruction is in the chat prompt; no pre-detected language is injected
    system = [c for c in fake.calls if "CASE FACTS ALREADY KNOWN" in c["messages"][0]["content"]][-1]["messages"][0]["content"]
    assert prompts.LANGUAGE_RULE in system and "The user is writing in" not in system
    # sources shown to the user come from the hybrid result and carry both ranker positions
    src = r.json()["assistant_message"]["sources"]
    assert src and "retrieval" in src[0] and set(src[0]["retrieval"]) == {"bm25_rank", "dense_rank"}

    cid = r.json()["case"]["id"]
    client.post(f"/api/cases/{cid}/guidance")
    assert calls[-1] == "hybrid" and len(calls) == 2
    g_prompt = [c for c in fake.calls if "preparing structured legal INFORMATION" in c["messages"][0]["content"]][-1]
    assert "मेरा ऑर्डर डिलीवर नहीं हुआ" in g_prompt["messages"][1]["content"]  # latest user message passed verbatim


def test_broken_index_returns_clear_503_not_silent_fallback(fake, monkeypatch):
    from app.rag import retriever as retriever_module
    from app.rag.store import RetrievalIndexError

    def broken():
        raise RetrievalIndexError("Dense (FAISS) index missing. Rebuild the indexes with: python scripts/build_index.py")

    monkeypatch.setattr(retriever_module, "get_retriever", broken)
    r = client.post("/api/chat", json={"message": "refund not received"})
    assert r.status_code == 503 and r.json()["error_type"] == "RetrievalIndexError"
    assert "build_index.py" in r.json()["detail"]
    h = client.get("/api/health").json()
    assert h["status"] == "degraded" and h["retrieval"]["mode"] == "unavailable"


def test_out_of_scope_guidance_is_templated(fake):
    fake.overrides["chat"] = [json.dumps({"reply": "Yeh NyayaAI ke scope se bahar hai.", "scope": "out_of_scope"})]
    fake.overrides["extract"] = [json.dumps({"category": "out_of_scope", "title": None, "timeline": []})]
    r = client.post("/api/chat", json={"message": "Mere bhai ko police ne arrest kar liya, bail kaise milegi?"})
    case = r.json()["case"]
    assert case["status"] == "out_of_scope"
    n_calls = len(fake.calls)
    g = client.post(f"/api/cases/{case['id']}/guidance").json()
    assert g["out_of_scope"] is True and "lawyer" in g["message"].lower()
    assert len(fake.calls) == n_calls  # no LLM call for out-of-scope guidance
    d = client.post(f"/api/cases/{case['id']}/documents", json={"doc_type": "seller_complaint", "language": "en"})
    assert d.status_code == 422


def test_health_and_sources():
    h = client.get("/api/health").json()
    assert h["database"] == "ok" and h["legal_index_chunks"] > 100
    src = client.get("/api/sources").json()
    assert any(s["id"] == "cpa2019" and s["status"] == "downloaded" for s in src)


def test_search_endpoint_is_llm_free():
    r = client.get("/api/search", params={"q": "refund not received after cancellation", "k": 3})
    assert r.status_code == 200 and len(r.json()) == 3

"""Structured, source-backed legal guidance for a case."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import prompts
from ..llm.client import LLMClient
from ..models import Case
from ..rag.retriever import retrieve
from ..schemas import GuidanceLLM
from . import case_service
from .citations import check_source_tags, normalize_tags, unverified_mentions

OUT_OF_SCOPE = {
    "en": "NyayaAI only covers consumer disputes about products bought (damaged/defective goods, refunds, "
          "non-delivery, wrong items, seller refusals). Your matter appears to be outside this scope, so NyayaAI "
          "cannot provide legal guidance on it. Please consult a qualified lawyer or the appropriate authority; "
          "in an emergency or if a crime is involved, contact the police.",
    "hi": "NyayaAI केवल खरीदे गए सामान से जुड़े उपभोक्ता विवादों (खराब/टूटा सामान, रिफंड, डिलीवरी न होना, गलत सामान, "
          "विक्रेता का मना करना) में मदद करता है। आपका मामला इस दायरे से बाहर लगता है, इसलिए NyayaAI इस पर कानूनी मार्गदर्शन "
          "नहीं दे सकता। कृपया किसी योग्य वकील या संबंधित प्राधिकरण से संपर्क करें; आपात स्थिति या अपराध के मामले में पुलिस से संपर्क करें।",
    "hinglish": "NyayaAI sirf kharide gaye saamaan se jude consumer disputes (damaged/kharab product, refund, delivery "
                "na hona, galat item, seller ka mana karna) mein madad karta hai. Aapka maamla is scope se bahar lagta "
                "hai, isliye NyayaAI is par legal guidance nahi de sakta. Kripya kisi qualified lawyer ya sahi authority "
                "se baat karein; emergency ya crime ke case mein police se sampark karein.",
}


def latest_user_message(case: Case) -> str:
    return next((m.content for m in reversed(case.messages) if m.role == "user"), "")


def guidance_query(case: Case) -> str:
    """Hybrid-retrieval query for a whole case: structured facts + the user's own latest words (original language)."""
    parts = [case.description, case.product_details, case.previous_communication, case.desired_resolution,
             case.platform, latest_user_message(case)]
    return "\n".join(p for p in parts if p)


def _filter_refs(refs: list[str], valid: set[str]) -> list[str]:
    return [r.strip().upper() for r in refs if r and r.strip().upper() in valid]


def generate_guidance(db: Session, case: Case, llm: LLMClient) -> dict:
    lang = case.language if case.language in OUT_OF_SCOPE else "en"  # language the model last replied in
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if case.category == "out_of_scope":
        result = {"out_of_scope": True, "message": OUT_OF_SCOPE[lang], "language": lang, "generated_at": now,
                  "sources": [], "checks": {}}
        case.guidance = result
        db.commit()
        return result

    category = case.category if case.category not in (None, "unclear") else None
    sources = retrieve(guidance_query(case), top_k=8, category=category)
    valid = {s["ref"] for s in sources}
    missing = case_service.missing_labels(case)
    g = llm.complete_json(
        prompts.guidance_messages(case_service.case_facts(case), case_service.timeline(case), latest_user_message(case),
                                  sources, missing),
        GuidanceLLM, temperature=0.1, max_tokens=2500,
    )

    if g.language in OUT_OF_SCOPE:
        lang = g.language

    # Canonicalise citation tags everywhere (models emit 【S1】 etc.) before any check runs.
    g.what_happened = normalize_tags(g.what_happened)
    g.lawyer_note = normalize_tags(g.lawyer_note)
    for field in ("next_steps", "evidence_to_preserve", "general_suggestions", "missing_information", "uncertainties"):
        setattr(g, field, [normalize_tags(x) for x in getattr(g, field)])

    # --- deterministic enforcement: a "protection" must be backed by a retrieved source
    checks = {"demoted_uncited_protections": 0, "removed_invalid_refs": [], "unverified_mentions": []}
    protections = []
    for p in g.protections:
        refs = _filter_refs(p.source_refs, valid)
        invalid = [r for r in p.source_refs if r.strip().upper() not in valid]
        checks["removed_invalid_refs"] += invalid
        text, _, inv2 = check_source_tags(p.text, sources)
        checks["removed_invalid_refs"] += inv2
        if refs:
            protections.append({"text": text, "source_refs": refs})
        else:
            checks["demoted_uncited_protections"] += 1
            g.general_suggestions.append(text)
    options = []
    for o in g.options:
        refs = _filter_refs(o.source_refs, valid)
        checks["removed_invalid_refs"] += [r for r in o.source_refs if r.strip().upper() not in valid]
        options.append({"title": o.title, "description": check_source_tags(o.description, sources)[0],
                        "source_refs": refs})

    all_text = " ".join([g.what_happened, *[p["text"] for p in protections],
                         *[o["title"] + " " + o["description"] for o in options], *g.next_steps,
                         *g.general_suggestions, *g.uncertainties, g.lawyer_note])
    checks["unverified_mentions"] = unverified_mentions(all_text, sources)
    checks["removed_invalid_refs"] = sorted(set(checks["removed_invalid_refs"]))

    used = {r for p in protections for r in p["source_refs"]} | {r for o in options for r in o["source_refs"]}
    result = {
        "out_of_scope": False,
        "language": lang,
        "generated_at": now,
        "what_happened": g.what_happened,
        "protections": protections,
        "options": options,
        "next_steps": g.next_steps,
        "evidence_to_preserve": g.evidence_to_preserve,
        "general_suggestions": g.general_suggestions,
        "missing_information": g.missing_information,
        "uncertainties": g.uncertainties,
        "sources_sufficient": bool(g.sources_sufficient and protections),
        "lawyer_recommended": g.lawyer_recommended,
        "lawyer_note": g.lawyer_note,
        "sources": [s for s in sources if s["ref"] in used],
        "retrieved_sources": sources,
        "checks": checks,
    }
    if not sources or not protections:
        result["uncertainties"] = list(result["uncertainties"]) + [{
            "en": "The retrieved official sources did not clearly cover this issue, so no legal protection is stated as verified.",
            "hi": "प्राप्त आधिकारिक स्रोत इस मुद्दे को स्पष्ट रूप से कवर नहीं करते, इसलिए किसी कानूनी सुरक्षा को सत्यापित नहीं बताया गया है।",
            "hinglish": "Mile hue official sources is issue ko clearly cover nahi karte, isliye koi legal protection verified nahi bataya gaya.",
        }[lang]]
    case.guidance = result
    db.commit()
    return result

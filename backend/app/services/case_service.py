"""Case building: LLM extraction + deterministic grounding checks + persistence."""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from .. import prompts
from ..llm.client import LLMClient, LLMError
from ..models import Case, CaseEvent, Message
from ..schemas import CASE_TEXT_FIELDS, CaseExtractionLLM, CaseUpdate

log = logging.getLogger("nyaya.case")

# Facts we want before a complaint can be drafted well, in priority order.
REQUIRED_FIELDS = [
    "seller_name", "product_details", "purchase_date", "amount_paid", "description", "desired_resolution",
    "order_id", "previous_communication", "evidence",
]
CORE_FIELDS = ["seller_name", "product_details", "description"]
FIELD_LABELS = {
    "seller_name": "seller / company name", "platform": "platform (website/app)", "product_details": "product details",
    "purchase_date": "purchase date", "delivery_date": "delivery date", "amount_paid": "amount paid",
    "description": "what went wrong", "desired_resolution": "what outcome you want (refund/replacement/etc.)",
    "order_id": "order ID / invoice number", "previous_communication": "what the seller/customer care said so far",
    "evidence": "evidence you have (invoice, photos, chats, emails)",
}


def case_facts(case: Case) -> dict:
    facts = {f: getattr(case, f) for f in CASE_TEXT_FIELDS}
    facts["evidence"] = case.evidence or []
    return facts


def timeline(case: Case) -> list[dict]:
    return [{"date": e.date, "event": e.description} for e in case.events]


def compute_missing(case: Case) -> list[str]:
    if case.category == "out_of_scope":
        return []
    missing = []
    for f in REQUIRED_FIELDS:
        v = getattr(case, f)
        if v in (None, "", []):
            missing.append(f)
    return missing


def missing_labels(case: Case) -> list[str]:
    return [FIELD_LABELS[f] for f in compute_missing(case)]


def refresh_status(case: Case) -> None:
    case.missing_information = compute_missing(case)
    if case.category == "out_of_scope":
        case.status = "out_of_scope"
    elif all(getattr(case, f) for f in CORE_FIELDS) and case.category not in (None, "unclear"):
        case.status = "ready"
    else:
        case.status = "intake"


# ---------------------------------------------------------------- grounding checks

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _digits_runs(s: str) -> set[str]:
    """Numbers the user wrote, with thousands separators removed: "Rs 25,000 on 12/03" -> {"25000", "12", "03"}."""
    return {re.sub(r"[,\s]", "", m) for m in re.findall(r"\d[\d,]*(?:\s\d{3})*", s)}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9ऀ-ॿ]", "", s.lower())


def ground_extraction(ext: CaseExtractionLLM, user_text: str) -> tuple[CaseExtractionLLM, list[str]]:
    """Drop extracted identifiers/amounts that do not literally appear in what the user wrote.

    The LLM is told not to invent values; this check enforces it for the fields where an invention would be
    most harmful (money, order numbers, seller identity). Returns (cleaned, dropped_field_notes).
    """
    dropped = []
    user_digits = _digits(user_text)
    norm_text = _norm(user_text)
    if ext.amount_paid:
        d = _digits(ext.amount_paid.split(".")[0])
        # "25,000" and "25000" both reduce to the same digit run; also accept "25k" / "25 hazaar" written by the user.
        short_form = d.endswith("000") and re.search(rf"\b{d[:-3]}\s*(k|hazaar|hazar|हजार|thousand)\b", user_text, re.I)
        if not d or (d not in _digits_runs(user_text) and not short_form):
            dropped.append(f"amount_paid={ext.amount_paid!r}")
            ext.amount_paid = None
    if ext.order_id:
        if _norm(ext.order_id) not in norm_text:
            dropped.append(f"order_id={ext.order_id!r}")
            ext.order_id = None
    for f in ("seller_name", "platform"):
        v = getattr(ext, f)
        if v:
            tokens = [t for t in re.split(r"\W+", v.lower()) if len(t) >= 3]
            if tokens and not any(_norm(t) in norm_text for t in tokens):
                dropped.append(f"{f}={v!r}")
                setattr(ext, f, None)
    for f in ("purchase_date", "delivery_date"):
        v = getattr(ext, f)
        if v and _digits(v) and _digits(v) not in user_digits and not any(
                _norm(t) in norm_text for t in re.split(r"\W+", v) if len(t) >= 3):
            dropped.append(f"{f}={v!r}")
            setattr(ext, f, None)
    return ext, dropped


# ---------------------------------------------------------------- extraction

def transcript_of(case: Case, extra_user: str | None = None) -> tuple[str, str]:
    lines, user_only = [], []
    for m in case.messages:
        lines.append(f"{m.role.upper()}: {m.content}")
        if m.role == "user":
            user_only.append(m.content)
    if extra_user:
        lines.append(f"USER: {extra_user}")
        user_only.append(extra_user)
    return "\n".join(lines[-40:]), "\n".join(user_only)


def extract_into_case(db: Session, case: Case, llm: LLMClient) -> dict:
    """Run extraction over the whole conversation and merge into the case. Returns a report dict."""
    transcript, user_text = transcript_of(case)
    locked = list(case.locked_fields or [])
    existing = case_facts(case)
    ext = llm.complete_json(prompts.extraction_messages(transcript, existing, locked), CaseExtractionLLM,
                            max_tokens=1500)
    ext, dropped = ground_extraction(ext, user_text)

    for f in CASE_TEXT_FIELDS:
        if f in locked:
            continue
        new = getattr(ext, f)
        if f == "category":
            # Do not let a later vague turn downgrade a known category back to "unclear".
            if new != "unclear" or not case.category:
                case.category = new
            continue
        if f == "title":
            if new:
                case.title = new[:255]
            continue
        if new is not None:
            setattr(case, f, new)
    if "evidence" not in locked and ext.evidence:
        merged = list(dict.fromkeys((case.evidence or []) + ext.evidence))
        case.evidence = merged

    if "events" not in locked and ext.timeline:
        case.events = [e for e in case.events if e.origin == "user"]
        base = len(case.events)
        for i, item in enumerate(ext.timeline):
            case.events.append(CaseEvent(position=base + i, date=item.date, description=item.event, origin="extracted"))

    refresh_status(case)
    db.flush()
    return {"dropped_ungrounded": dropped, "category": case.category, "status": case.status}


def apply_user_update(db: Session, case: Case, upd: CaseUpdate) -> Case:
    data = upd.model_dump(exclude_unset=True)
    locked = set(case.locked_fields or [])
    events = data.pop("events", None)
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        setattr(case, k, v)
        locked.add(k)
    if events is not None:
        case.events = [CaseEvent(position=i, date=(e.get("date") or None), description=e["description"], origin="user")
                       for i, e in enumerate(events)]
        locked.add("events")
    case.locked_fields = sorted(locked)
    refresh_status(case)
    db.flush()
    return case


def get_case_or_none(db: Session, case_id: int) -> Case | None:
    return db.get(Case, case_id)


def recent_history(case: Case, limit: int = 14) -> list[dict]:
    msgs: list[Message] = case.messages[-limit:]
    return [{"role": m.role, "content": m.content} for m in msgs]


def safe_extract(db: Session, case: Case, llm: LLMClient) -> str | None:
    """Extraction failures must not break the chat turn; return an error string instead."""
    try:
        report = extract_into_case(db, case, llm)
        if report["dropped_ungrounded"]:
            log.info("case %s: dropped ungrounded extracted values %s", case.id, report["dropped_ungrounded"])
        return None
    except LLMError as e:
        log.warning("extraction failed for case %s: %s", case.id, e)
        refresh_status(case)
        return e.user_message

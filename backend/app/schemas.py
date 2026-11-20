"""Pydantic schemas: API request/response models and the contracts LLM JSON output must satisfy."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "damaged_or_defective", "refund_not_received", "not_delivered", "wrong_item",
    "refund_or_replacement_refused", "other_ecommerce", "out_of_scope", "unclear",
]
CATEGORY_VALUES = set(Category.__args__)  # type: ignore[attr-defined]

CASE_TEXT_FIELDS = [
    "title", "category", "seller_name", "platform", "product_details", "order_id", "purchase_date",
    "delivery_date", "amount_paid", "description", "previous_communication", "desired_resolution",
]


def _blank_to_none(v):
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in {"null", "none", "n/a", "na", "unknown", "not provided", "not mentioned", "-"}:
            return None
    return v


# ---------------------------------------------------------------- LLM output contracts

def _lang(v) -> str:
    """Normalise the language the model reports it replied in (used for display labels only)."""
    v = str(v or "").strip().lower()
    aliases = {"english": "en", "hindi": "hi", "devanagari": "hi", "hindi (devanagari)": "hi",
               "romanized hindi": "hinglish", "roman hindi": "hinglish", "hinglish (roman)": "hinglish"}
    v = aliases.get(v, v)
    return v if v in {"en", "hi", "hinglish"} else "other"


class ChatTurnLLM(BaseModel):
    reply: str = Field(min_length=1)
    scope: Literal["in_scope", "out_of_scope", "unclear"] = "unclear"
    language: str = "other"

    _l = field_validator("language", mode="before")(lambda v: _lang(v))


class TimelineItemLLM(BaseModel):
    date: str | None = None
    event: str = Field(min_length=1)

    _n = field_validator("date", mode="before")(_blank_to_none)


class CaseExtractionLLM(BaseModel):
    title: str | None = None
    category: Category = "unclear"
    seller_name: str | None = None
    platform: str | None = None
    product_details: str | None = None
    order_id: str | None = None
    purchase_date: str | None = None
    delivery_date: str | None = None
    amount_paid: str | None = None
    description: str | None = None
    previous_communication: str | None = None
    evidence: list[str] = Field(default_factory=list)
    desired_resolution: str | None = None
    timeline: list[TimelineItemLLM] = Field(default_factory=list)

    _n = field_validator(
        "title", "seller_name", "platform", "product_details", "order_id", "purchase_date", "delivery_date",
        "amount_paid", "description", "previous_communication", "desired_resolution", mode="before",
    )(_blank_to_none)

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        v = (v or "unclear").strip().lower() if isinstance(v, str) else "unclear"
        return v if v in CATEGORY_VALUES else "unclear"

    @field_validator("amount_paid", "order_id", "purchase_date", "delivery_date", mode="before")
    @classmethod
    def _stringify(cls, v):
        return str(v) if isinstance(v, (int, float)) else v

    @field_validator("evidence", mode="before")
    @classmethod
    def _ev(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x).strip() for x in v if x and str(x).strip()]


class GuidancePoint(BaseModel):
    text: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class GuidanceOption(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    source_refs: list[str] = Field(default_factory=list)


class GuidanceLLM(BaseModel):
    what_happened: str = Field(min_length=1)
    protections: list[GuidancePoint] = Field(default_factory=list)
    options: list[GuidanceOption] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    evidence_to_preserve: list[str] = Field(default_factory=list)
    general_suggestions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    sources_sufficient: bool = False
    lawyer_recommended: bool = True
    lawyer_note: str = ""
    language: str = "other"

    _l = field_validator("language", mode="before")(lambda v: _lang(v))


class DocumentLLM(BaseModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=20)


# ---------------------------------------------------------------- API models

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    case_id: int | None = None


class EventIn(BaseModel):
    date: str | None = None
    description: str = Field(min_length=1)


class CaseUpdate(BaseModel):
    title: str | None = None
    category: Category | None = None
    seller_name: str | None = None
    platform: str | None = None
    product_details: str | None = None
    order_id: str | None = None
    purchase_date: str | None = None
    delivery_date: str | None = None
    amount_paid: str | None = None
    description: str | None = None
    previous_communication: str | None = None
    evidence: list[str] | None = None
    desired_resolution: str | None = None
    events: list[EventIn] | None = None


class DocumentRequest(BaseModel):
    doc_type: Literal["seller_complaint", "refund_request", "consumer_grievance", "statement_of_facts"]
    language: Literal["en", "hi"] = "en"


class DocumentUpdate(BaseModel):
    content: str = Field(min_length=1)
    title: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    language: str | None
    sources: list | None = None
    meta: dict | None = None
    created_at: datetime | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    date: str | None
    description: str
    origin: str


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    case_id: int
    doc_type: str
    title: str
    content: str
    language: str
    sources: list | None = None
    checks: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CaseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    status: str
    language: str
    category: str | None
    seller_name: str | None
    amount_paid: str | None
    created_at: datetime | None
    updated_at: datetime | None


class CaseOut(CaseSummary):
    platform: str | None
    product_details: str | None
    order_id: str | None
    purchase_date: str | None
    delivery_date: str | None
    description: str | None
    previous_communication: str | None
    evidence: list | None
    desired_resolution: str | None
    missing_information: list | None
    locked_fields: list | None
    guidance: dict | None
    events: list[EventOut] = []
    documents: list[DocumentOut] = []
    messages: list[MessageOut] = []


class ChatResponse(BaseModel):
    case: CaseOut
    user_message: MessageOut
    assistant_message: MessageOut
    extraction_error: str | None = None

"""One chat turn: hybrid retrieval -> LLM reply (same language as the user) -> citation checks -> persist -> extract case."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import prompts
from ..config import get_settings
from ..llm.client import LLMClient
from ..models import Case, Message
from ..rag.retriever import retrieve
from ..schemas import ChatTurnLLM
from . import case_service
from .citations import run_checks


def build_query(case: Case, message: str) -> str:
    """Retrieval query = the user's latest message + their previous 2 messages, in their ORIGINAL language.

    No translation: the multilingual E5 model handles Hindi/Hinglish semantically, and BM25 appends glossary
    terms itself. Earlier messages give context to short follow-ups ("haan, invoice hai").
    """
    recent_user = [m.content for m in case.messages if m.role == "user"][-2:]
    return "\n".join([message, *recent_user])


def merge_with_previous(case: Case, fresh: list[dict], max_carry: int = 4) -> list[dict]:
    """Keep passages cited earlier in this conversation available to the model.

    Without this, a provision cited in turn 1 but not re-retrieved in turn 2 gets remembered by the model and
    attached to the wrong tag (observed in live run 2: Section 39(1) cited as [S2] = Rule 7). Refs are re-numbered.
    """
    carried: list[dict] = []
    seen = {s["chunk_id"] for s in fresh}
    for m in reversed(case.messages):
        for s in (m.sources or []) if m.role == "assistant" else []:
            if s["chunk_id"] not in seen and len(carried) < max_carry:
                carried.append(s)
                seen.add(s["chunk_id"])
    merged = carried + fresh
    return [{**s, "ref": f"S{i}"} for i, s in enumerate(merged, start=1)]


def chat_turn(db: Session, llm: LLMClient, message: str, case_id: int | None) -> tuple[Case, Message, Message, str | None]:
    case = db.get(Case, case_id) if case_id else None
    if case is None:
        case = Case(title="New consultation", status="intake", language="other")
        db.add(case)
        db.flush()

    category = case.category if case.category not in (None, "unclear", "out_of_scope") else None
    fresh = retrieve(build_query(case, message), top_k=get_settings().rag_top_k, category=category)
    sources = merge_with_previous(case, fresh)

    history = case_service.recent_history(case) + [{"role": "user", "content": message}]
    msgs = prompts.chat_messages(history, sources, case_service.case_facts(case), case_service.missing_labels(case))
    out = llm.complete_json(msgs, ChatTurnLLM, temperature=0.3, max_tokens=900)  # raises LLMError -> rollback

    reply, checks = run_checks(out.reply.strip(), sources)
    cited = [s for s in sources if s["ref"] in checks["cited_refs"]]
    checks["scope"] = out.scope
    # The model reports the language it answered in (it is instructed to mirror the user's latest message).
    # Stored for display labels and to pick the fixed out-of-scope text; it does not steer retrieval or replies.
    language = out.language
    if language != "other":
        case.language = language

    user_msg = Message(case=case, role="user", content=message, language=language)
    asst_msg = Message(case=case, role="assistant", content=reply, language=language, sources=cited, meta=checks)
    db.add_all([user_msg, asst_msg])
    db.flush()

    extraction_error = case_service.safe_extract(db, case, llm)
    if out.scope == "out_of_scope" and case.category in (None, "unclear"):
        case.category = "out_of_scope"
        case_service.refresh_status(case)
    db.commit()
    db.refresh(case)
    return case, user_msg, asst_msg, extraction_error

"""HTTP endpoints. Thin layer: validation in, service call, serialisation out."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import DATA_DIR, get_settings
from ..db import get_db
from ..llm.client import get_llm
from ..models import Case, Document
from ..rag.retriever import retrieve
from ..rag.retriever import status as retrieval_status
from ..schemas import (CaseOut, CaseSummary, CaseUpdate, ChatRequest, ChatResponse, DocumentOut, DocumentRequest,
                       DocumentUpdate, MessageOut)
from ..services import case_service, chat_service, document_service, guidance_service

router = APIRouter(prefix="/api")


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


# ---------------------------------------------------------------- health / sources

@router.get("/health")
def health(db: Session = Depends(get_db)):
    s = get_settings()
    out = {"status": "ok", "model": s.model_name, "llm_configured": get_llm().configured}
    try:
        db.execute(text("SELECT 1"))
        out["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["database"] = f"error: {type(e).__name__}"
        out["status"] = "degraded"
    rs = retrieval_status()
    out["retrieval"] = rs
    out["legal_index_chunks"] = rs.get("chunks", 0)
    if rs["mode"] == "unavailable":
        out["status"] = "degraded"
    if not out["llm_configured"]:
        out["status"] = "degraded"
    return out


@router.get("/sources")
def sources():
    manifest = json.loads((DATA_DIR / "sources_manifest.json").read_text(encoding="utf-8"))
    report_path = DATA_DIR / "processed" / "ingest_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"documents": []}
    chunks = {d["id"]: d.get("chunks", 0) for d in report["documents"]}
    return [{"id": m["id"], "title": m["title"], "publisher": m["publisher"], "url": m["url"], "status": m["status"],
             "chunks": chunks.get(m["id"], 0), "checked_at": m.get("checked_at")} for m in manifest]


@router.get("/search")
def search(q: str = Query(min_length=2), k: int = Query(6, ge=1, le=20), category: str | None = None,
           mode: str = Query("hybrid", pattern="^(hybrid|bm25|dense)$")):
    """Retrieval only, no LLM. Default is hybrid (what the app uses); bm25/dense are for comparison."""
    return retrieve(q, top_k=k, category=category, mode=mode)


# ---------------------------------------------------------------- chat

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    if req.case_id is not None:
        _case(db, req.case_id)
    case, user_msg, asst_msg, extraction_error = chat_service.chat_turn(db, get_llm(), req.message.strip(), req.case_id)
    return ChatResponse(case=CaseOut.model_validate(case), user_message=MessageOut.model_validate(user_msg),
                        assistant_message=MessageOut.model_validate(asst_msg), extraction_error=extraction_error)


# ---------------------------------------------------------------- cases

@router.get("/cases", response_model=list[CaseSummary])
def list_cases(db: Session = Depends(get_db)):
    return db.scalars(select(Case).order_by(Case.updated_at.desc(), Case.id.desc())).all()


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: int, db: Session = Depends(get_db)):
    return _case(db, case_id)


@router.put("/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: int, upd: CaseUpdate, db: Session = Depends(get_db)):
    case = case_service.apply_user_update(db, _case(db, case_id), upd)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    db.delete(_case(db, case_id))
    db.commit()
    return Response(status_code=204)


@router.post("/cases/{case_id}/extract", response_model=CaseOut)
def extract(case_id: int, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    case_service.extract_into_case(db, case, get_llm())
    db.commit()
    db.refresh(case)
    return case


# ---------------------------------------------------------------- guidance

@router.post("/cases/{case_id}/guidance")
def make_guidance(case_id: int, db: Session = Depends(get_db)):
    return guidance_service.generate_guidance(db, _case(db, case_id), get_llm())


@router.get("/cases/{case_id}/guidance")
def read_guidance(case_id: int, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    if not case.guidance:
        raise HTTPException(404, "No guidance generated yet")
    return case.guidance


# ---------------------------------------------------------------- documents

@router.post("/cases/{case_id}/documents", response_model=DocumentOut)
def make_document(case_id: int, req: DocumentRequest, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    if case.category == "out_of_scope":
        raise HTTPException(422, "This case is outside NyayaAI's consumer-dispute scope; no documents can be drafted.")
    return document_service.generate_document(db, case, get_llm(), req.doc_type, req.language)


@router.put("/documents/{doc_id}", response_model=DocumentOut)
def edit_document(doc_id: int, upd: DocumentUpdate, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.content = upd.content
    if upd.title:
        doc.title = upd.title[:255]
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    return Response(status_code=204)


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"nyayaai_case{doc.case_id}_{doc.doc_type}_{doc.id}") + ".txt"
    return Response(content=doc.content.encode("utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})

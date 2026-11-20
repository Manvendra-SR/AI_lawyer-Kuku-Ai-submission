"""FastAPI application entry point.

Run:  uvicorn app.main:app --reload --port 8000   (from the backend/ directory)
"""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .config import get_settings
from .llm.client import LLMError, LLMInvalidResponse, LLMNotConfigured
from .rag.store import RetrievalIndexError
from .routers.api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pypdf").setLevel(logging.ERROR)

app = FastAPI(title="NyayaAI API", version="1.0.0",
              description="AI legal-information assistant for Indian consumer disputes")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(LLMError)
async def llm_error_handler(_: Request, exc: LLMError):
    code = 503 if isinstance(exc, LLMNotConfigured) else 502 if isinstance(exc, LLMInvalidResponse) else 503
    return JSONResponse(status_code=code, content={"detail": exc.user_message, "error_type": type(exc).__name__})


@app.exception_handler(RetrievalIndexError)
async def index_error_handler(_: Request, exc: RetrievalIndexError):
    logging.getLogger("nyaya.rag").error("retrieval index error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": f"Legal search index unavailable: {exc}",
                                                  "error_type": "RetrievalIndexError"})


@app.on_event("startup")
def warm_retriever():
    """Load both indexes and the embedding model at startup, so the first chat turn is fast.
    A broken index is logged here and reported by /api/health and by every retrieval call (503)."""
    from .rag.retriever import get_retriever

    try:
        get_retriever().search("refund for defective goods", top_k=1)
    except RetrievalIndexError as e:
        logging.getLogger("nyaya.rag").error("retrieval indexes not ready: %s", e)


@app.exception_handler(OperationalError)
async def db_down_handler(_: Request, exc: OperationalError):
    logging.getLogger("nyaya.db").error("database error: %s", exc)
    return JSONResponse(status_code=503, content={
        "detail": "Database unavailable. Is MySQL running and DATABASE_URL correct? Then run: python scripts/init_db.py",
        "error_type": "DatabaseUnavailable"})


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(_: Request, exc: SQLAlchemyError):
    logging.getLogger("nyaya.db").error("database error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Database error.", "error_type": type(exc).__name__})


app.include_router(router)


@app.get("/")
def root():
    return {"name": "NyayaAI API", "docs": "/docs", "health": "/api/health"}

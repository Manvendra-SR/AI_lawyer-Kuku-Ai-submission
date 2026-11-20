"""Offline test setup.

These tests never call the real Groq API: a scripted fake OpenAI-compatible endpoint (httpx.MockTransport)
stands in for it. They use TEST_DATABASE_URL if set (e.g. your MySQL), otherwise a throwaway SQLite file,
so they can run anywhere. The live evaluation against real Groq + MySQL is tests/run_eval.py.
"""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp()) / "nyaya_test.db"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp.as_posix()}")
os.environ["GROQ_API_KEY"] = ""  # never hit the real API from unit tests
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # embedding model must come from the local cache
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.db import Base, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _db():
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)

"""Central configuration loaded from environment variables / the project-level .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"

DEFAULT_MODEL = "openai/gpt-oss-120b"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = ""
    groq_model: str = ""
    # Any OpenAI-compatible chat-completions endpoint works here, which is what makes the model swappable.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 5  # retries on HTTP 429 (free-tier tokens-per-minute limits)

    database_url: str = "mysql+pymysql://root:@127.0.0.1:3306/nyayaai?charset=utf8mb4"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Retrieval (hybrid BM25 + dense with Reciprocal Rank Fusion)
    rag_top_k: int = 6                 # chunks passed to the LLM
    rag_candidates: int = 30           # candidates taken from EACH ranker before fusion
    rrf_k: int = 60                    # RRF constant: score = sum 1 / (rrf_k + rank)
    embedding_model: str = "intfloat/multilingual-e5-small"

    @property
    def model_name(self) -> str:
        return self.groq_model.strip() or DEFAULT_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()

# NyayaAI — AI legal assistant for Indian consumer disputes

NyayaAI helps an ordinary person in India who bought something (usually online) and got stuck: the
product arrived **damaged or defective**, the **refund never came**, the order was **never delivered**,
the **wrong item** arrived, or the **seller refuses** a refund or replacement.

The user writes in **English, Hindi (Devanagari) or Hinglish**. NyayaAI replies in the same language and
script, asks follow-up questions, builds a structured case, explains the consumer protections that apply
(citing **official Indian legal text it actually retrieved**), and drafts a complaint the user can edit and download.

> NyayaAI provides legal **information**, not legal advice. Every legal statement is tied to a retrieved
> official passage, and every document is a labelled draft.

Docs: [ARCHITECTURE.md](ARCHITECTURE.md) (how it works) · [SOLUTION.md](SOLUTION.md) / SOLUTION.pdf
(submission write-up) · [outputs/](outputs/) (test cases, live responses, retrieval evaluation).

---

## Features

| Area | What it does |
|---|---|
| **AI Legal Chat** | Multilingual conversation. The model is instructed to answer in the language and script of the user's latest message (there is no separate language-detection step). Asks focused follow-ups and never re-asks known facts. Shows clickable `[S1]` citations that open the official passage. |
| **Case Builder** | After every turn the conversation is extracted into a structured case (seller, platform, product, order ID, dates, amount, description, timeline, prior communication, evidence, desired resolution, missing info), stored in MySQL and editable in **My Cases**. Fields you edit are locked against AI overwrite. |
| **Hybrid legal RAG** | 286 section-level chunks from 12 official documents. Each query runs **BM25** (exact legal terms) and **multilingual E5 embeddings + FAISS** (meaning, cross-lingual) in parallel; results are fused with **Reciprocal Rank Fusion**. Used by Chat, Legal Guidance and Document Studio. |
| **Legal Guidance** | Structured output: what happened, protections (each must cite a retrieved source, or it is demoted), options, next steps, evidence to preserve, general (uncited) suggestions, missing info, uncertainties, when to see a lawyer. |
| **Document Studio** | Complaint to seller, refund request, consumer grievance draft (EN or हिंदी), plus a **chronological statement of facts built from the stored timeline (no LLM)**. Edit, save, copy, download `.txt`. Checks flag unverified legal references, numbers not in the case, and unfilled `[PLACEHOLDERS]`. |
| **Safety layer** | Out-of-scope matters (criminal, family, property…) are declined with a lawyer/police referral. Citation tags not among the retrieved passages are stripped. Any "Section N / Rule N / धारा N" not found in the retrieved text is flagged. Extracted amounts, order IDs and names the user never wrote are dropped. |

## Architecture

```
React + Vite + TS (frontend/)          FastAPI (backend/app/)
┌──────────────────────────┐  /api   ┌─────────────────────────────────────────────────┐
│ Chat · Cases · Guidance  │ ──────► │ routers/api.py      thin HTTP layer             │
│ · Document Studio        │ (Vite   │ services/           chat, case, guidance,       │
└──────────────────────────┘  proxy) │                     documents, citation checks  │
                                     │ prompts.py          all prompts                 │
                                     │ llm/client.py       OpenAI-compatible client    │──► Groq API
                                     │ rag/sparse.py       BM25 (+ glossary append)    │
                                     │ rag/dense.py        E5 embeddings + FAISS       │
                                     │ rag/hybrid.py       parallel search + RRF       │
                                     │ rag/retriever.py    public retrieve()           │
                                     │ models.py / db.py   SQLAlchemy                  │──► MySQL
                                     └─────────────────────────────────────────────────┘
```

**Swapping the model.** No vendor SDK: `LLMClient` speaks the OpenAI-compatible `/chat/completions` protocol.
Change `GROQ_MODEL` (or `LLM_BASE_URL`) in `.env`. All structured outputs are validated with Pydantic.
Retrieval (`/api/search`) uses no LLM.

## Installation

Prerequisites: Python 3.11+ (tested 3.12), Node 18+ (tested 24), MySQL 8 (tested 8.4.3), a free Groq API key
(https://console.groq.com/keys). About 1.5 GB of disk for torch + the embedding model.

```powershell
cd backend
python -m venv .venv                     # project-local environment (git-ignored)
.venv\Scripts\activate                   # macOS/Linux: source .venv/bin/activate
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch (smaller)
pip install -r requirements.txt          # exact pinned versions
cd ..\frontend
npm install
```

All backend commands below assume the venv is activated (or call `backend\.venv\Scripts\python` directly).

## Environment setup

Copy `.env.example` to `.env` in the project root:

```ini
GROQ_API_KEY=gsk_...           # required for chat / guidance / documents
GROQ_MODEL=                    # empty = openai/gpt-oss-120b (alternative: qwen/qwen3.8-27b)
DATABASE_URL=mysql+pymysql://root:@127.0.0.1:3306/nyayaai?charset=utf8mb4
```

Optional: `EMBEDDING_MODEL` (default `intfloat/multilingual-e5-small`), `RAG_TOP_K` (6), `RAG_CANDIDATES` (30 per
ranker), `RRF_K` (60), `LLM_BASE_URL`, `LLM_MAX_RETRIES`, `CORS_ORIGINS`. `.env` is git-ignored.

## Database setup

1. **Have MySQL running.** Use your own MySQL 8 (set `DATABASE_URL`), or the bundled no-install MySQL 8.4
   in `.mysql/` (git-ignored):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_mysql.ps1   # one-time: download + initialise
   powershell -ExecutionPolicy Bypass -File scripts\start_mysql.ps1   # every time; keep the window open
   ```
2. **Create the database and tables** (idempotent): `cd backend && python scripts/init_db.py`.
   Creates database `nyayaai` with tables `cases`, `messages`, `case_events`, `documents`.

## Legal corpus and indexes

The downloaded documents and built indexes are included. To refresh them:

```bash
cd backend
python scripts/download_sources.py   # fetch official docs -> data/raw (every attempt logged)
python scripts/build_index.py        # chunk -> BM25 index -> E5 embeddings -> FAISS index
```

Indexes live in `backend/data/processed/index/` (`bm25.json`, `dense.faiss`, `dense_meta.json`). Embeddings are
regenerated only when the corpus or `EMBEDDING_MODEL` changes (`--force` to override). The first build downloads
the embedding model (~470 MB) to the Hugging Face cache. If an index is missing, stale or corrupted, the API
returns a clear 503 telling you to run `build_index.py`, and `/api/health` reports `retrieval.mode: unavailable`.
It never falls back silently to BM25-only.

## Run

```bash
cd backend && python -m uvicorn app.main:app --port 8000     # terminal 1 (loads indexes + model at startup)
cd frontend && npm run dev                                   # terminal 2 -> http://localhost:5173
```

The sidebar shows live status for MySQL, Groq and hybrid search. API docs: http://localhost:8000/docs.

## Testing

```bash
cd backend
python -m pytest tests -v              # 40 offline tests (no Groq; embedding model from local cache)
python tests/run_retrieval_eval.py     # BM25 vs dense vs hybrid metrics -> outputs/retrieval_evaluation.md
python tests/run_eval.py               # LIVE: real Groq + MySQL, 5 scenarios + failure modes
python tests/export_responses.py eval_runs/eval_<timestamp>   # -> outputs/responses/test_0N.txt
```

## Demo / walkthrough script

1. Chat: paste the Hinglish example ("Maine Amazon se phone kharida tha…"), answer one follow-up, and open a
   citation chip to show the official passage and the PDF link.
2. My Cases: show the extracted fields and timeline; edit one field (it gets an "edited by you" lock).
3. Legal Guidance: generate; point out cited protections vs general suggestions, uncertainties, the lawyer note.
4. Document Studio: generate a complaint, show the DRAFT label, the placeholders and the checks; download `.txt`.
5. Ask the criminal-law question in a new chat to show the out-of-scope refusal.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | DB / LLM / retrieval-index status |
| POST | `/api/chat` | `{message, case_id?}` → reply + updated case |
| GET / PUT / DELETE | `/api/cases/{id}` | read / edit (locks edited fields) / delete case |
| GET | `/api/cases` | list cases |
| POST | `/api/cases/{id}/extract` | re-run case extraction |
| POST / GET | `/api/cases/{id}/guidance` | generate / read structured guidance |
| POST | `/api/cases/{id}/documents` | `{doc_type, language}` → draft |
| PUT / DELETE | `/api/documents/{id}` | edit / delete draft |
| GET | `/api/documents/{id}/download` | `.txt` download |
| GET | `/api/search?q=&mode=` | retrieval only, no LLM (`hybrid` default; `bm25`/`dense` for comparison) |
| GET | `/api/sources` | corpus manifest |

## Scope & limitations (short)

Covers consumer purchase disputes only. Not a lawyer: it cannot check a seller's own return policy, verify
facts or file anything. Retrieval and citation limits are documented in [SOLUTION.md](SOLUTION.md).

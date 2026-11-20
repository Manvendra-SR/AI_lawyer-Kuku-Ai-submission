# NyayaAI — Architecture Guide

A plain-language walkthrough of how the whole application works.

---

## A. Project overview

NyayaAI is a legal-information assistant for **consumer disputes in India**: a product arrived damaged,
a refund never came, an order was never delivered, the wrong item arrived, or a seller refuses a refund
or replacement.

The user describes the problem in **English, Hindi or Hinglish**. NyayaAI:

1. replies in the same language and asks follow-up questions,
2. turns the conversation into a **structured case** (seller, product, dates, amount, timeline, evidence…),
3. explains the user's position using **real, retrieved official legal text**, with clickable citations,
4. drafts documents (complaint, refund request, grievance, statement of facts) the user can edit and download.

It does not cover criminal, family, property or employment law. Those questions are politely declined
with a referral to a lawyer or the police.

---

## B. Technology stack

| Technology | What it is | Why it is used here |
|---|---|---|
| **React + Vite + TypeScript** | Frontend UI library, dev server/bundler, typed JavaScript | The four screens: Chat, My Cases, Legal Guidance, Document Studio |
| **FastAPI** | Python web framework | The REST API (`/api/...`) that the frontend calls |
| **Groq** | Cloud API that runs open LLMs very fast | Generates chat replies, extracts case data, writes guidance and drafts. Default model `openai/gpt-oss-120b` |
| **MySQL** | Relational database | Stores cases, messages, timeline events, documents |
| **SQLAlchemy** | Python ORM | Python classes ↔ MySQL tables, no hand-written SQL |
| **Pydantic** | Data-validation library | Validates API requests and, critically, the JSON the LLM returns |
| **BM25** | Classic keyword-ranking algorithm (sparse retrieval) | Finds passages sharing the query's exact words: "Rule 6", "limitation", "grievance officer". Plain Python |
| **multilingual-e5-small** (Sentence Transformers) | Small multilingual embedding model (384-dim, ~100 languages incl. Hindi), runs locally on CPU | Turns chunks and queries into vectors so a Hindi or Hinglish question can match English law by meaning (dense retrieval) |
| **FAISS** | Facebook's vector-search library | Stores the 286 chunk vectors and finds the nearest ones to a query vector (inner product on normalised vectors = cosine) |
| **RRF** (Reciprocal Rank Fusion) | A way to merge ranked lists | Combines the BM25 ranking and the FAISS ranking into one list, using ranks only |

---

## C. Folder structure (important parts only)

```
backend/
  app/
    main.py              FastAPI app: CORS, error handlers, mounts the router
    config.py            Settings from .env (API key, model, DB URL)
    db.py                SQLAlchemy engine/session; creates the database if missing
    models.py            Database tables: Case, Message, CaseEvent, Document
    schemas.py           Pydantic models: API requests/responses + LLM output contracts
    prompts.py           Every LLM prompt, in one file
    routers/api.py       All HTTP endpoints (thin: validate → call a service → return)
    llm/client.py        The only code that talks to Groq (OpenAI-compatible HTTP)
    rag/ingest.py        Official PDFs/HTML → section-level chunks with stable IDs
    rag/store.py         Paths, chunk loading, corpus fingerprint, RetrievalIndexError
    rag/sparse.py        BM25 index (build/save/load/search) + Hindi/Hinglish glossary append
    rag/dense.py         E5 embeddings + FAISS index (build/save/load/search)
    rag/hybrid.py        Runs both rankers in parallel and fuses them with RRF
    rag/retriever.py     Public retrieve() used by chat, guidance and documents; health status
    services/
      chat_service.py    One chat turn, end to end
      case_service.py    Case extraction, grounding checks, missing-info logic
      guidance_service.py Structured, cited legal guidance
      document_service.py Drafts + checks; deterministic statement of facts
      citations.py       Anti-hallucination checks on LLM text
  data/
    raw/                 The downloaded official documents (as fetched)
    processed/chunks.jsonl   286 searchable legal chunks
    processed/index/         bm25.json, dense.faiss, dense_meta.json (built indexes)
    sources_manifest.json    Every download attempt, including failures
  scripts/               download_sources.py, build_index.py (chunks + both indexes), init_db.py
  tests/                 test_offline.py, test_retrieval.py (pytest, offline), run_eval.py (live),
                         export_responses.py, run_retrieval_eval.py + retrieval_eval_set.json
frontend/src/
  App.tsx                Layout, sidebar navigation, active case, health status
  api.ts                 Typed functions for every backend endpoint
  pages/                 ChatPage, CasesPage, GuidancePage, DocumentsPage
  components/common.tsx  Citation chips, source cards, spinner, error banner
scripts/                 setup_mysql.ps1 / start_mysql.ps1 (bundled MySQL), make_pdf.py (SOLUTION.pdf)
.mysql/                  Local MySQL 8.4 server + data (git-ignored)
outputs/                 Test cases, real live responses, retrieval evaluation
```

---

## D. Application workflow (the real order)

What happens when the user sends **"Maine Amazon se phone kharida tha, lekin damaged aaya…"**:

```
React ChatPage ──POST /api/chat {message, case_id}──► FastAPI router ──► chat_service.chat_turn()
```

Inside `chat_turn()`, step by step:

1. **Load or create the case.** The first message creates a new `Case` row.
2. **Hybrid retrieval before calling the LLM** (`rag/retriever.py`). The query is the user's latest message
   plus their previous two messages, **in the original language, untranslated**. BM25 and FAISS search it in
   parallel (30 candidates each). RRF fuses the two rankings, and the top 6 chunks become `S1…S6`. Passages
   cited in earlier turns are carried forward so the model can keep citing them correctly.
3. **Call Groq for the reply** (`prompts.chat_messages` + `llm/client.py`). The prompt contains the safety
   rules, the **same-language instruction**, the facts already known (so it doesn't re-ask), the missing
   facts, the SOURCES and recent conversation history. The model must return JSON
   `{reply, scope, language}`, where `language` is what it replied in (stored only for UI labels).
4. **Check the reply** (`citations.py`): normalise citation tags, delete any `[S#]` that wasn't retrieved,
   and flag any "Section/Rule N" not present in the retrieved text.
5. **Save** the user message and assistant message (with its cited sources and check results) to MySQL.
6. **Extract the case** (second Groq call, `case_service.py`): the whole conversation → structured JSON
   (seller, amount, dates, timeline, evidence…). A Python **grounding check** then drops any amount, order ID,
   seller or date that doesn't literally appear in what the user typed. Fields the user edited by hand are
   never overwritten. Missing information and status are computed in Python.
7. **Commit** and return the reply plus the updated case. The UI shows the reply, citation chips and the
   case panel.

So the order is **retrieval → grounded LLM reply → checks → save → case extraction → save**. If the LLM call
fails, nothing is saved and the user sees a clear error.

**Later, on demand:**
- **Legal Guidance** (`POST /api/cases/{id}/guidance`): hybrid-retrieve 8 passages for the case (its facts +
  the user's latest message) → Groq returns
  structured JSON → Python enforces that every "protection" cites a valid source (otherwise it is moved to
  "general suggestions") → saved on the case.
- **Documents** (`POST /api/cases/{id}/documents`): complaint / refund request / grievance are written by Groq
  from the stored case only, with `[PLACEHOLDERS]` for unknowns, then checked for unverified legal
  references and numbers not in the case. The **statement of facts is built by Python directly from the
  timeline (no LLM)**. Users can edit, save, copy and download `.txt`.

---

## E. Database design

Four tables. One conversation equals one case.

| Table | Holds | Key columns |
|---|---|---|
| `cases` | The structured case | title, category, status, language, seller_name, platform, product_details, order_id, purchase/delivery dates, amount_paid, description, previous_communication, evidence (JSON), desired_resolution, missing_information (JSON), guidance (JSON), locked_fields (JSON) |
| `messages` | Every chat message | case_id → cases, role (user/assistant), content, language, sources (JSON), meta (JSON check results) |
| `case_events` | The timeline | case_id → cases, position, date (kept exactly as the user said it), description, origin (extracted/user) |
| `documents` | Generated drafts | case_id → cases, doc_type, title, content, language, sources, checks |

Relationships: `cases` 1 → many `messages`, `case_events`, `documents`, with `ON DELETE CASCADE` (deleting
a case removes everything about it). All text is `utf8mb4`, so Hindi is stored correctly. Dates are stored as
free text on purpose, so "last Monday" is never turned into an invented calendar date.

---

## F. RAG implementation (hybrid retrieval)

1. **Where the law comes from.** `scripts/download_sources.py` downloads 13 official sources: the Consumer
   Protection Act 2019; the E-Commerce Rules 2020 with corrigendum and the 2021/2026 amendments; the
   Consumer Commission and General Rules 2020; the Pecuniary Jurisdiction Rules 2021; the Mediation Rules
   2020; the Commission Procedure Regulations 2020; and the Dark Patterns Guidelines 2023, all from the
   Department of Consumer Affairs website. It also fetches two National Consumer Helpline pages and the
   e-Jagriti page. Every attempt is logged in `sources_manifest.json`, including India Code's 504 errors.
2. **Processing and chunking** (`rag/ingest.py`). Text is extracted with pypdf/BeautifulSoup. Gazette PDFs are
   bilingual and the Hindi half extracts as garbled text, so only English lines are kept. A new chunk starts
   at each section or rule number, and long sections are split at sub-sections "(1)", "(2)". Each chunk has a
   stable ID (e.g. `ecom_rules_2020#14`), document title, label ("Rule 6(1)-(3)"), page range and source URL.
   Result: 286 chunks.
3. **Building the indexes** (`scripts/build_index.py`):
   - **BM25** → `bm25.json` (term counts per chunk).
   - **Dense** → each chunk becomes `"passage: <title>, <label>. <text>"`, embedded by
     `intfloat/multilingual-e5-small`, L2-normalised and added to a FAISS `IndexFlatIP` (exact search; inner
     product of normalised vectors = cosine) → `dense.faiss` + `dense_meta.json`.
   - The metadata stores the model name, dimension, prefixes, chunk-ID order and a **corpus fingerprint**
     (hash of all chunk IDs and texts). Re-running the build skips re-embedding when nothing changed. Loading
     checks everything: missing, corrupted, stale or wrong-model indexes raise a clear "run build_index.py"
     error (503 from the API). Nothing silently falls back to BM25-only.
4. **Searching** (`rag/hybrid.py`). Both rankers get the same original query:
   - **BM25** scores word overlap. Before scoring, a glossary *appends* English legal terms for common
     Hindi/Hinglish words ("paisa wapas" → refund), and the original words stay in. A dispute-category hint may
     append terms too.
   - **Dense**: the query becomes `"query: <text>"`, is embedded and searched in FAISS. This is what lets
     "दोषपूर्ण सामान" or "jhoota vigyapan" match English sections by meaning.
   - **RRF**: `score(chunk) = Σ 1/(60 + rank)` over the two lists. Only ranks are used (BM25 scores and
     cosines aren't comparable). A chunk in both lists appears once, and ties are broken deterministically.
     The top 6 (chat) or 8 (guidance) are kept, each recording its BM25 and dense rank.
5. **Passing to Groq.** The chunks go into the prompt as a `SOURCES` block, tagged `[S1]`, `[S2]`… with title,
   label and text. The model may only make legal claims from these, citing tags.
6. **Displaying citations.** The backend keeps only tags that were really retrieved and attaches those source
   records to the message. The UI renders each tag as a clickable chip, and each source card shows title,
   section/rule, pages, a link to the official PDF page and the exact passage text.
7. **Evaluation.** `tests/run_retrieval_eval.py` compares BM25, dense and hybrid on 15 labelled queries
   (English/Hindi/Hinglish). Results are in `outputs/retrieval_evaluation.md`.

`GET /api/search?q=...&mode=hybrid|bm25|dense` runs retrieval alone, without an LLM.

---

## G. LLM integration

- **One client** (`llm/client.py`) makes a plain HTTPS POST to Groq's OpenAI-compatible
  `/chat/completions` endpoint. No vendor SDK.
- **Configuration** is in `.env`: `GROQ_API_KEY`, `GROQ_MODEL` (empty = `openai/gpt-oss-120b`), optional
  `LLM_BASE_URL`. Changing the model is a one-line change, and was tested with `qwen/qwen3.8-27b`.
- **JSON mode + Pydantic:** every call asks for JSON and validates it against a schema. If it's invalid,
  the model gets one repair attempt. If it's still invalid, the API returns a clear error and saves nothing.
- **Errors:** missing or invalid key → 503 with a clear message. Per-minute rate limits (429) → wait and
  retry. Daily quota exhausted → fail fast with "retry in N minutes".
- **Multilingual:** there is **no language-detection code**. Retrieval works on the original text (the E5
  model is multilingual), and every prompt contains one rule: *"Respond in the same language and writing style
  used by the user in their latest message…"*, repeated as a short reminder after the latest message in chat.
  (Known gap: after several Devanagari turns, a switch to Roman Hinglish may still get a Devanagari reply.)
  Guidance prompts also include the user's latest message
  verbatim. The model reports the language it used, which is stored only for UI labels and for the fixed
  out-of-scope text. Documents use the language the user picks (English/Hindi). The case record is stored in English.

---

## H. Important design decisions: code vs. LLM

| Deterministic Python code | LLM (Groq) |
|---|---|
| Legal retrieval: BM25, E5 embeddings + FAISS, RRF (no LLM) | Conversational reply in the user's language |
| Index building, versioning and validation | Extracting case fields from the conversation |
| Removing invalid citation tags; flagging unverified "Section/Rule N" | Writing structured guidance |
| Dropping extracted amounts/IDs/names the user never wrote | Drafting complaint / refund / grievance letters |
| Missing-information list, case status, out-of-scope guidance text | |
| Statement of facts (from the stored timeline) | |

The idea: the LLM handles language (including matching the user's language), and the code handles
retrieval, facts and verification. Anything that can be
checked mechanically is checked in code, never trusted to the model.

---

## I. Running the application

```powershell
# 1. Start MySQL (bundled; skip if you run your own MySQL and set DATABASE_URL in .env)
powershell -ExecutionPolicy Bypass -File scripts\start_mysql.ps1      # keep this window open

# 2. Backend environment (first time only), then create the database and tables
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python scripts/init_db.py
python scripts/build_index.py      # only if the indexes are missing or the corpus changed

# 3. Start the backend API (new terminal)
cd backend
.venv\Scripts\activate
python -m uvicorn app.main:app --port 8000

# 4. Start the frontend (new terminal)
cd frontend
npm install        # first time only
npm run dev        # open http://localhost:5173
```

Tests: `cd backend` → `python -m pytest tests -v` (40 offline tests) · `python tests/run_retrieval_eval.py`
(BM25 vs dense vs hybrid) · `python tests/run_eval.py` (live, uses Groq).

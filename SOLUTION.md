# NyayaAI — AI Legal Assistant for Indian Consumer Disputes
*Kuku AI Generalist Assignment · Problem 2: Build an AI Lawyer*

## 1. Problem chosen and why
A refund that never arrives, or a product that arrives broken, is the legal problem ordinary Indians run into
most often. It also has a clear, free remedy path: complaint to the seller → platform grievance officer →
National Consumer Helpline → District Consumer Commission. The governing law (Consumer Protection Act 2019,
E-Commerce Rules 2020) is short, public and citeable, so a small system can cover it **in depth** and ground
every legal statement in official text. People usually give up because they don't know their rights or how
to write the complaint.

## 2. Scope and assumptions
**Covered:** damaged/defective goods, refund not received, non-delivery, wrong item, seller refusing
refund/replacement, online-marketplace disputes. **Declined with a lawyer/police referral:** criminal, family,
property, employment, tenancy, tax. **Assumptions:** text input (no voice); users write English, Hindi
(Devanagari) or Hinglish; the structured case is stored in English (formal complaints in India are usually
English), while chat and guidance follow the user's language and letters can be drafted in English or Hindi.

## 3. What NyayaAI does
- **AI Legal Chat:** answers in the language and script of the user's latest message, asks focused
  follow-ups, never re-asks known facts.
- **Case Builder:** the conversation becomes a structured case (seller, product, order ID, dates, amount,
  timeline, evidence, desired outcome, missing info) in MySQL. Editable, and user edits are never overwritten.
- **Legal Guidance:** protections, options, next steps, evidence, uncertainties, when to see a lawyer. Every
  legal point links to the official passage it came from.
- **Document Studio:** complaint, refund request and consumer grievance (LLM-drafted), plus a statement of facts
  built directly from the stored timeline. All editable and downloadable (.txt), and labelled DRAFT.

## 4. Technology stack
React + Vite + TypeScript · FastAPI · Groq (`openai/gpt-oss-120b` default, set in `.env`) · MySQL 8.4 +
SQLAlchemy · Pydantic · **BM25** (plain Python) + **`intfloat/multilingual-e5-small`** embeddings (local,
sentence-transformers) + **FAISS** + **Reciprocal Rank Fusion**. No LangChain, no vector database server.

## 5. Architecture and workflow
One chat turn: **hybrid retrieval** on the user's original text → **Groq reply** (JSON; given the retrieved
SOURCES, known facts, missing facts and the same-language rule) → **citation checks** → **save** → **second
Groq call extracts the case** → Python grounding checks → **save**. Guidance and documents use the same
hybrid retriever on demand. The model is swappable through one OpenAI-compatible client and an env var.
A full earlier run used `qwen/qwen3.8-27b` with no code change.

| Deterministic code | LLM (Groq) |
|---|---|
| Hybrid retrieval (BM25, E5 + FAISS, RRF); index build, versioning, validation | Replies in the user's language |
| Citation-tag and "Section/Rule N" verification; dropping ungrounded case values | Case extraction (JSON) |
| Missing info, case status, out-of-scope text, statement of facts | Guidance (JSON), letter drafting |

## 6. Multilingual capability
No language-detection code and no translation. The query is retrieved **as written**: the multilingual E5
model matches Hindi and Hinglish questions to English statutes by meaning, and BM25 appends English glossary
terms ("paisa wapas" → refund) while keeping the original words. Every prompt carries one rule: *respond in the
same language and writing style as the user's latest message* (Hindi → Hindi, Roman Hinglish → Roman Hinglish).
In the final live run, all 11 chat replies matched: 3 Hinglish, 3 Hindi, 5 English. So did the guidance.

## 7. Legal RAG and hallucination prevention
**Corpus (downloaded, every attempt logged):** 12 official documents from the Department of Consumer Affairs and
National Consumer Helpline: the CP Act 2019; the E-Commerce Rules 2020 with corrigendum and 2021/2026 amendments;
the Commission, Pecuniary-Jurisdiction and Mediation Rules; the Procedure Regulations; the Dark Patterns
Guidelines. India Code returned HTTP 504, so the Act came from the DCA site. Only the English text of the
bilingual gazettes is indexed (the Hindi extracts garbled). **Chunking:** one chunk per section/rule, split at
sub-sections, giving 286 chunks with stable IDs, label, page and URL.
**Indexes:** BM25 + 384-dim normalised E5 vectors in FAISS `IndexFlatIP` (cosine), built once by
`build_index.py`. Metadata records the model, prefixes (`query:`/`passage:`) and a corpus fingerprint, so a
missing, stale or corrupted index produces an actionable 503, never a silent BM25-only fallback.
**Fusion:** each ranker returns 30 candidates, fused by RRF, `Σ 1/(60 + rank)`. Ranks only, deduplicated by chunk ID.
**Safeguards:** legal claims only from retrieved SOURCES, cited `[S#]`; code strips tags that weren't retrieved;
flags any "Section/Rule/धारा N" absent from the retrieved text (UI warning); demotes uncited "protections" to
general suggestions; drops amounts, IDs, names and dates the user never typed; letters use `[PLACEHOLDERS]` and
are checked for unknown numbers. Out-of-scope answers are fixed templates.

## 8. Testing and actual results
**Retrieval benchmark** (15 queries in English/Hindi/Hinglish; relevant chunk IDs labelled from the statutes
before running):

| Method | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| BM25 | 0.622 | 0.756 | 0.540 | 0.560 |
| Dense (E5 + FAISS) | 0.567 | 0.622 | 0.621 | 0.575 |
| **Hybrid (RRF)** | **0.656** | **0.822** | **0.702** | **0.657** |

Hybrid wins overall and on Hindi (nDCG 0.83 vs 0.77/0.54), but is worse than the better single method on 7/15
queries. On Hinglish all three are weak (nDCG ≤ 0.36), and no method finds the rule for "paisa wapas nahi aaya".
**Offline:** 40 pytest tests (no Groq) cover BM25, dense, RRF maths, deduplication, metadata, index
persistence, corrupted/stale/missing indexes, cross-lingual retrieval, citation checks and the full flow. They
also confirm Chat and Guidance call the hybrid retriever.
**Live (real Groq + MySQL):** five scenarios: damaged phone (Hinglish), refund after cancellation (English),
non-delivery (Hindi), "The company cheated me", and an assault/FIR question. Result: **142/144 checks**, plus
5/5 failure-mode checks. Both failures: a letter quoted Rule 4(10) and 4(5) verbatim but labelled them "Rule 10"
and "Rule 5", and the checker flagged both. The vague case invented no facts, and the criminal question was
declined with no IPC section named. Earlier runs found and fixed: `【S1】` tags silently dropping citations,
invented deadlines in letters, and a provision mis-tagged across turns.

## 9. Known limitations and failure cases
- **Citation labels:** sub-rules are sometimes shortened ("Rule 10" for 4(10)). Detected and flagged, not corrected.
- **Paraphrase drift** ("seller must refund" vs "Commission may order") is prompted against but not machine-checked.
- **Language switching mid-conversation:** a switch to English is followed, but after several Devanagari turns
  a Roman-Hinglish message can still get a mostly-Devanagari reply (seen on gpt-oss and qwen, even with the rule
  repeated after the last message).
- **Retrieval:** Hinglish recall is poor. Dense search always returns neighbours, so an out-of-scope question
  still retrieves weak passages, and the LLM scope gate is what stops a legal answer. Chunks longer than
  512 tokens are truncated for embedding.
- **No coverage** of sellers' return policies, RBI refund timelines or case law. Not legal advice.
- **Free tier** (8k tokens/min, 200k/day) allows roughly one chat turn per minute under load. The embedding model adds ~1.5 GB of dependencies.

## 10. Scalability and future improvements
~2.2k tokens per LLM call, about 20k per full case, which is about $0.005 per case on a paid tier (estimate).
Retrieval is local: about 50 ms per query on CPU, about 90 s to embed the corpus once. Next steps: auto-correct
citation labels from the retrieved chunk; Hinglish-aware query rewriting or a larger embedding model; a
cross-encoder re-ranker; an IVF/HNSW index if the corpus grows to many thousands of chunks; more dispute types
(rent deposits, salary) by adding sources; voice input.

*AI tools: built with Claude Code. One error it made: it chose `llama-3.3-70b-versatile` from memory as the
default model, but that model no longer existed on Groq (HTTP 404). A live check caught it.*

# Raw outputs

- `test_cases.md`: the five end-to-end test scenarios and the expected behaviour.
- `responses/test_01.txt … test_05.txt`: the **actual, unedited output** of one live run of the application.
- `retrieval_evaluation.md`: BM25 vs dense vs hybrid (RRF) retrieval metrics on 15 labelled queries (no LLM).

## How these were generated

```bash
cd backend
python tests/run_eval.py                                     # live: real Groq API + real MySQL + hybrid retrieval
python tests/export_responses.py eval_runs/eval_<timestamp>  # copies recorded responses into responses/
python tests/run_retrieval_eval.py                           # writes retrieval_evaluation.md
```

`run_eval.py` sends each conversation to the app through its HTTP API, then requests guidance and documents,
records every request and response, and runs automatic checks. `export_responses.py` copies those responses
verbatim into readable text files. Nothing is hand-written or edited.

Each response file contains: the extracted case as stored in MySQL; every user message and the assistant's reply
in the original language; the official sources cited (title, section/rule, page, URL, plus the BM25 rank and
dense rank behind each hybrid result); the legal guidance; the generated documents; and the automatic check results.

## Live run used (hybrid retrieval)

- Date: 20 Nov 2026 · LLM: `openai/gpt-oss-120b` via Groq · Retrieval: BM25 + `intfloat/multilingual-e5-small`/FAISS,
  RRF (k=60) · Database: local MySQL 8.4.3
- Result: **142/144 automatic checks passed**, plus 5/5 failure-mode checks (missing key, invalid key, malformed
  model output, unknown model).
- All 11 chat replies were in the user's language and script: 3 Hinglish (Roman), 3 Hindi (Devanagari), 5 English.
  (These five tests keep one language per conversation. A separate manual check, switching mid-conversation from
  Hindi to Roman Hinglish, found the reply can stay mostly in Devanagari. This is a known limitation; switching to
  English worked.)
- Both failures are in test 02's letters. The model quoted E-Commerce Rule **4(10)** and **4(5)** word for word,
  but labelled them "Rule 10" and "Rule 5". The checker flagged these as unverified references (the UI shows a
  warning). The quoted content is correct; the labels are incomplete.

## Retrieval evaluation (summary)

| Method | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| BM25 | 0.622 | 0.756 | 0.540 | 0.560 |
| Dense (E5 + FAISS) | 0.567 | 0.622 | 0.621 | 0.575 |
| **Hybrid (RRF)** | **0.656** | **0.822** | **0.702** | **0.657** |

Hybrid is best overall, but worse than the better single method on 7 of 15 queries, and no method finds the
relevant rule for one Hinglish query (q03). Details in `retrieval_evaluation.md`.

Offline tests: `cd backend && .venv\Scripts\python -m pytest tests -v` (40 tests, no Groq key needed).

Note: the live run above used an index built with pypdf 6.16. The pinned environment (`backend/.venv`, pypdf 7.1.1)
extracts slightly different text. Chunk IDs and labels are identical, and the metrics above come from the pinned
environment.

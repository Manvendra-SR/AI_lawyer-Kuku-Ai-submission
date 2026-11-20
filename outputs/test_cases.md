# NyayaAI — Test cases

Each test is a real multi-turn conversation sent to the running application (FastAPI + Groq + MySQL, hybrid
BM25 + E5/FAISS retrieval with RRF), followed by a request for legal guidance and documents.
Results: `responses/test_0N.txt`. The separate retrieval benchmark (15 queries) is in `retrieval_evaluation.md`.

| # | Scenario | Language | User input (turn 1) | Expected behaviour |
|---|---|---|---|---|
| 1 | Damaged product, refund refused | Hinglish | "Maine Amazon se phone kharida tha, lekin damaged aaya aur seller refund nahi de raha." (+2 follow-up turns with model, date, ₹18,999, order ID, customer-care reply, evidence, wish for refund) | Replies in Hinglish; asks for missing facts and doesn't re-ask; cites the E-Commerce Rules / CP Act from retrieved text; extracts amount, order ID and dates exactly; guidance with cited protections; complaint (EN), refund request (Hindi) and statement of facts generated as drafts |
| 2 | Refund not received after cancellation | English | "I cancelled my Flipkart order for a pair of running shoes on 2 November 2026, before it was shipped, but the refund of Rs 3,499 has still not come." (+2 turns: UPI, order ID, 18 days, evidence) | English replies; category `refund_not_received`; correct amount and order ID; refund request and consumer grievance drafts with no invented numbers or provisions |
| 3 | Product never delivered | Hindi (Devanagari) | "मैंने एक ऑनलाइन वेबसाइट से 6,500 रुपये का मिक्सर ग्राइंडर ऑर्डर किया था और पैसे पहले ही दे दिए, लेकिन सामान कभी डिलीवर नहीं हुआ।" (+2 turns) | Replies and guidance in Hindi; category `not_delivered`; amount ₹6,500 extracted; Hindi complaint + English grievance drafts |
| 4 | Insufficient information | English | "The company cheated me." | No legal conclusions; asks what happened; **no invented facts** in the case; guidance lists missing information |
| 5 | Unsupported question (criminal law) | English | "My neighbour assaulted me and the police are refusing to register an FIR. Which IPC section applies and how do I get him arrested?" | Declines as out of scope; names **no IPC section**; recommends a lawyer/police; case marked `out_of_scope`; document generation refused (HTTP 422) |

**Automatic checks run on every test** (listed at the end of each response file): reply language and script
match the user's message; every citation tag maps to a displayed source; every displayed source is a real indexed official passage
with an official URL; no unverified "Section/Rule N" mentions; extracted fields as expected; messages and
timeline persisted in MySQL; guidance cites only retrieved sources and makes no outcome guarantees; documents
labelled DRAFT, contain no unverified provisions or unknown numbers, and download as TXT.

**Failure-mode checks** (same run, not part of the five files): missing API key → clear 503; invalid key
(real Groq 401) → clear 503; malformed model output → 502 with nothing saved; unknown model id → graceful 503.
All 5 passed.

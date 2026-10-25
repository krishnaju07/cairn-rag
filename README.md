# Cairn

Upload documents, ask questions, get answers with citations you can open. Every claim links to the original page, and the sentence that supports it is highlighted. Python only: FastAPI, server-rendered pages, SQLite.

Embeddings, keyword search and reranking run on your machine, so the first run costs nothing.

## Run it

Needs Python 3.10 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m app
```

Open http://127.0.0.1:8100. Add `--reload` while developing, or `--port` to change the port. The first start downloads about 150 MB of local models once; the home page says when they are ready. Health check: `/healthz`.

To try it right away, build the sample documents and upload them to a collection:

```bash
.venv/bin/python eval/basic/build.py
```

### Choosing what writes the answers

Open **Providers** to pick a default answer model, paste keys and test connections. Each collection can still pick its own under **Settings → Answers**. Keys you paste are written to `.env` (owner-only permissions) and only their last four characters are ever shown again. You can also put them in `.env` yourself; see `.env.example`.

| Provider | Cost | Where text goes | Setup |
| --- | --- | --- | --- |
| Ollama | Free | Stays on this machine | `brew install ollama`, `ollama serve`, `ollama pull qwen2.5:3b` |
| NVIDIA NIM | Free tier, rate limited | Question and passages go to NVIDIA | Free key from build.nvidia.com |
| Anthropic | Paid per token | Question and passages go to Anthropic | API key |
| Extractive | Free | Stays on this machine | None. Quotes the closest sentences, no model |

NVIDIA lists many more models than it serves. Cairn checks the catalogue in the background and offers only the models that answered (with their speed), and **Refresh models** repeats the check.

Optional: **LlamaParse** (OCR for scanned PDFs and complex tables) is a switch on the Text Extraction step. It needs a LlamaCloud key, sends PDFs to LlamaCloud and uses your credits. The upload screen warns you before anything is sent.

## How it works

1. **Pipelines** decide how a file becomes searchable passages. Start from a template or from scratch, then add, reorder and remove steps. Steps only open where they are valid: while you drag one, only the legal places light up. Required steps (extraction, chunking, embedding, indexing) are locked.
2. **Test** runs the draft on one file with nothing saved, shows every step's output and time, and shows the cost of any LLM step before it runs.
3. **Publish** saves a numbered version. Any version can be restored to the draft.
4. **Collections** pick a published version, an answer model and how passages are found: by meaning, by exact words (BM25), or both with a local reranker.
5. **Upload** shows a cost estimate first, then each step's progress. Drop files anywhere on a collection.
6. **Chat** answers only from retrieved passages and numbers its citations. Answers stream in with lists and bold text formatted, each citation number is a chip you can hover to preview the cited sentence or click to open the original page with it highlighted, and sources sit under the answer as cards. You can stop an answer mid-way, copy it, ask again, switch between meaning, exact words or both, and start a new chat; the conversation is restored when you come back. A question the documents don't support gets a plain refusal, not a guess. Greetings, thanks and "what can you do?" get a short fixed reply with no citations (chat only; the `/v1` API never skips retrieval).
7. **Search** returns matching passages with their scores and no generated answer.
8. **API keys** are created per collection for external apps.

Pipeline steps: Text Extraction, Chunking (about 500 tokens, 50 overlap, sentence-aware), Embedding, Vector Indexing, Metadata Extraction, plus the optional LLM steps (abstract, sentiment, question generation), which are marked costly and estimated before they run.

### What it does to get hard documents right

- **PDF text:** repeated headers and footers are removed, words hyphenated across lines are rejoined, table rows stay together as `cell | cell | cell`, and multi-column pages keep their reading order.
- **Detail passages:** each 500-token chunk also gets small searchable passages, so a single fact buried in a long chunk can still be found. A hit always returns and cites the whole chunk.
- **Overview questions** ("what is this file?", "summarize this") are answered from each document's opening instead of searching for a match.
- **Refusals:** the answer model may decline, and independent checks refuse answers whose words are not in the passages, or whose specific identifiers (a year, `KX-7`) are missing from the cited passage.
- **Citations are repaired:** each sentence is attributed to the passage that actually contains its facts, so a mis-numbered `[2]` from a small model still opens the right page.

## API

Create a key in **Access** on a collection, then:

```bash
curl -X POST http://127.0.0.1:8100/v1/search \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"query": "hotel limit", "top_k": 3}'

curl -X POST http://127.0.0.1:8100/v1/query \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"question": "How many vacation days do new employees get?"}'
```

`/v1/search` returns chunks with semantic, BM25, fused and rerank scores. `/v1/query` returns the answer and numbered citations (file, page, passage), or the closest passages when unanswered. Keys are shown once, stored as hashes and can be revoked.

## Evaluate

```bash
.venv/bin/python eval/run.py --suite basic                       # 4 small documents, 20 questions
.venv/bin/python eval/run.py --suite hard                        # one 36-page report with traps
.venv/bin/python eval/run.py --suite hard/scanned --ocr          # image-only pages through LlamaParse
.venv/bin/python eval/run.py --suite hard --model nvidia:nvidia/nemotron-3-ultra-550b-a55b
```

Build a suite's documents first with its `build.py`. The **hard** report is two-column with repeated headers and footers, hyphenated words, tables, a footnote, a Spanish page, near-duplicate distractors, conflicting policy years and exact-ID lookups, with five questions the document cannot answer (for example `KX-7` when only `KX-9` exists). Reported per suite: retrieval hit rate, right-page rate and MRR for each search mode; whether answers are given, state the right fact and cite the right page; grounding (the share of an answer's words found in its cited passages); and how often unsupported questions are declined.

Measured here, hybrid search, top 5. Language models vary a little from run to run.

| Suite and model | Passage found | Answers | States the fact | Cites right page | Declines traps |
| --- | --- | --- | --- | --- | --- |
| basic, `ollama:qwen2.5:3b` | 100% | 100% | 100% | n/a | 100% |
| hard, before this work, same model | 57% | 64% | 43% | 50% | 60% |
| hard, `ollama:qwen2.5:3b` | 93% | 93% | 86–93% | 93% | 100% |
| hard, `nvidia/nemotron-3-ultra-550b-a55b` | 93% | 93% | 93% | 93% | 100% |
| hard/scanned, LlamaParse `fast` + qwen | 100% | 100% | 75% | 100% | n/a |

The one hard-suite question that still fails is an English question about a Spanish-language page: the default embedding model is English only. A multilingual option exists on the Embedding step, but it scored lower overall (86%), so it is only worth using for genuinely mixed-language collections.

## Layout

```
app/
  ingestion/   extraction, chunking, step catalog and rules, runner, versions, jobs, LlamaParse
  retrieval/   embeddings, reranker, SQLite index, semantic / keyword / hybrid search
  generation/  answer orchestration, citation attribution, providers and their backends
  api/         JSON endpoints (chat, search, key-protected /v1)
  web/         pages, templates, static assets
  security.py  host and origin checks, security headers
  viewer.py    page image and highlight rectangles for citations
eval/          suites (basic, hard), builders, Q&A sets, evaluation script
```

## Honest notes

- The Anthropic provider follows the official SDK but has not been run against a live key. Ollama, NVIDIA, extractive and LlamaParse were run for real.
- Extractive mode cannot tell a question the documents answer from one that only shares a name with them. Use a model for dependable refusals.
- On PDFs read with LlamaParse, the cited passage is shown next to the page image instead of outlined on it, because the extracted text no longer matches the page layout word for word.
- Documents processed before a layout change show the highlight beside the page until you press Reprocess.
- PDF handling uses PyMuPDF, which is AGPL-licensed. Check that works for how you plan to use this.
- It is a single-user local app with no sign-in. Requests are limited to local hosts and same-origin writes, and pages ship a strict content-security-policy. Set `ALLOWED_HOSTS` only if you deliberately serve it elsewhere, and put real authentication in front of it first.
- Vector search is an exact scan in SQLite, which is fast and precise up to a few hundred thousand chunks.
- No test files ship, at your request. The evaluation suites are the check.

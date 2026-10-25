# Cairn

Upload documents, ask questions, get answers with citations you can open. Every claim links to the original page, and the sentence that supports it is highlighted. Python only: FastAPI, server-rendered pages, SQLite.

Embeddings, keyword search and reranking run on your machine, so the first run costs nothing. Images and audio work too: text in an image is read with local OCR and recordings are transcribed locally, so a citation can outline the line on the image or open the recording at that moment.

## Run it

Needs Python 3.10 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m app
```

Open http://127.0.0.1:8100. Add `--reload` while developing, or `--port` to change the port. The first start downloads about 150 MB of local models once; the home page says when they are ready. Health check: `/healthz`.

A fresh install starts with a **Sample: Northwind Labs** collection (a handbook, benefits guide, remote-work policy and holiday calendar, plus an image and a recording when the media extras are installed), so there is something to ask about straight away. It is created once per database: delete it whenever you like, or set `SEED_SAMPLE=0` to skip it. On a host with a temporary disk, such as Render's free plan, the data is gone after every restart, so it is created again each time.

To build more sample documents and upload them to a collection:

```bash
.venv/bin/python eval/basic/build.py
```

### Images and audio

Reading images and transcribing audio needs two more local libraries:

```bash
.venv/bin/pip install -e '.[media]'
```

Then upload PNG, JPG, WEBP, BMP or TIFF images and MP3, WAV, M4A, FLAC, OGG, AAC or WEBM recordings like any other file. The first image downloads a 9 MB text-recognition model and the first recording downloads the speech model (about 145 MB for the default Base). Both run on your machine and nothing is sent anywhere. In Docker, install with `pip install ".[media]"`. Without the extras, uploading these files stops with a message that says what to install.

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
6. **Chat** answers only from retrieved passages and numbers its citations. Answers stream in with lists and bold text formatted, each citation number is a chip you can hover to preview the cited sentence or click to open the original page with it highlighted (for an image, the cited line is outlined on the image; for a recording, the player opens at the cited moment, the transcript lines are highlighted and any line can be clicked to jump there), and sources sit under the answer as cards. You can stop an answer mid-way, copy it, ask again, switch between meaning, exact words or both, and start a new chat; the conversation is restored when you come back. A question the documents don't support gets a plain refusal, not a guess. Greetings, thanks and "what can you do?" get a short fixed reply with no citations (chat only; the `/v1` API never skips retrieval).
7. **Search** returns matching passages with their scores and no generated answer.
8. **API keys** are created per collection for external apps.

Pipeline steps: Text Extraction (documents, images and audio), Chunking (about 500 tokens, 50 overlap, sentence-aware), Embedding, Vector Indexing, Metadata Extraction, plus the optional LLM steps (abstract, sentiment, question generation), which are marked costly and estimated before they run.

### What it does to get hard documents right

- **PDF text:** repeated headers and footers are removed, words hyphenated across lines are rejoined, table rows stay together as `cell | cell | cell`, and multi-column pages keep their reading order.
- **Detail passages:** each 500-token chunk also gets small searchable passages, so a single fact buried in a long chunk can still be found. A hit always returns and cites the whole chunk.
- **Overview questions** ("what is this file?", "summarize this") are answered from each document's opening instead of searching for a match.
- **Refusals:** the answer model may decline, and independent checks refuse answers whose words are not in the passages, or whose specific identifiers (a year, `KX-7`) are missing from the cited passage.
- **Images:** RapidOCR reads the text and every line keeps its box, so the passage a citation points to is outlined on the image. Lines are put in reading order, and cells that sit side by side in a row (a table or receipt) are joined with `|`. The OCR uses an English recognition model that is downloaded on first use: the one bundled with RapidOCR often drops the spaces between English words, which breaks keyword search. Measured here on rendered text, it read 100% of words correctly in three of four size tests against 1% to 61% for the bundled model.
- **Audio:** Whisper (through faster-whisper, on the CPU) transcribes the recording with silence skipped, and the transcript is cut into sections of about 90 seconds that keep their timestamps. A section is what a citation points to, so Chunking stays inside one. The Text Extraction step chooses the model size (Tiny, Base or Small) and a time limit per recording.
- Everything after extraction is the same for every file type, so hybrid search, detail passages and the refusal checks all apply to transcripts and image text.
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
.venv/bin/python eval/run.py --suite media --top-k 3                # a recording and two images
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
| media, `ollama:qwen2.5:3b`, top 3 | 100% | 100% | 100% | 100% | 67% |

The **media** suite is one 1:43 recording and two images with 15 answerable questions and 3 that cannot be answered. For audio, "page" means the 90-second section. It has only four chunks, so top 5 returns all of them; at top 1 hybrid search still puts the right chunk first for all 15 questions (semantic alone: 14). The one unsupported question it fails is the model confusing two similar sensor names (it gave the Meridian sensor's code for the Kestrel sensor); the same small model passes the other two. The speech was made with a macOS voice and the images were rendered from text, so real recordings and photos will read worse (see below). `eval/media/build.py` regenerates them on macOS; the built files are committed so the suite runs anywhere.

The one hard-suite question that still fails is an English question about a Spanish-language page: the default embedding model is English only. A multilingual option exists on the Embedding step, but it scored lower overall (86%), so it is only worth using for genuinely mixed-language collections.

## Layout

```
app/
  ingestion/   extraction (including OCR and speech to text), chunking, step catalog and rules, runner, versions, jobs, LlamaParse
  retrieval/   embeddings, reranker, SQLite index, semantic / keyword / hybrid search
  generation/  answer orchestration, citation attribution, providers and their backends
  api/         JSON endpoints (chat, search, key-protected /v1)
  web/         pages, templates, static assets
  security.py  host and origin checks, security headers
  viewer.py    page image and highlight rectangles for citations
eval/          suites (basic, hard, media), builders, Q&A sets, evaluation script
```

## Honest notes

- The Anthropic provider follows the official SDK but has not been run against a live key. Ollama, NVIDIA, extractive and LlamaParse were run for real.
- Extractive mode cannot tell a question the documents answer from one that only shares a name with them. Use a model for dependable refusals.
- On PDFs read with LlamaParse, the cited passage is shown next to the page image instead of outlined on it, because the extracted text no longer matches the page layout word for word.
- Documents processed before a layout change show the highlight beside the page until you press Reprocess.
- Image and audio support was run for real (RapidOCR and faster-whisper) but only on synthetic material: speech from a macOS voice and text rendered by PyMuPDF. Noisy recordings, accents, several speakers and phone photos of documents will read worse. The Base speech model already mishears some words ("our" became "are"), and a larger model helps at the cost of speed. A 1:43 recording took 5.5 seconds with Base on this machine.
- The sample collection picks its answer model when it is created: a local Ollama or NVIDIA model if one is ready, otherwise Extractive, which quotes sentences and cannot reliably refuse. Change it under Settings → Answers.
- The image and audio samples are only added when the media extras are installed. With them, the app peaked at about 1.27 GB in my test (0.79 GB with text only, on a Mac), so they do not fit on Render's 512 MB free plan. Use a larger instance and `pip install ".[media]"` to try them there.
- Photos and drawings with no text cannot be searched yet. There is no vision or image-embedding step, so such an image is rejected with a message that says so. Text recognition is tuned for English.
- Image highlights outline whole lines of text, not single words, and audio highlights cover whole transcript lines.
- On macOS, loading PyAV (audio) and OpenCV (images) in one process prints an Objective-C "implemented in both" warning. I saw no problem from it.
- PDF handling uses PyMuPDF, which is AGPL-licensed. Check that works for how you plan to use this.
- It is a single-user local app with no sign-in. Requests are limited to local hosts and same-origin writes, and pages ship a strict content-security-policy. Set `ALLOWED_HOSTS` only if you deliberately serve it elsewhere, and put real authentication in front of it first.
- Vector search is an exact scan in SQLite, which is fast and precise up to a few hundred thousand chunks.
- No test files ship, at your request. The evaluation suites are the check.

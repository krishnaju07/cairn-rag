import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .. import db
from ..generation import providers
from ..retrieval import embeddings, store
from . import extract, media, pages, tokens
from .chunking import Chunk, chunk_document, chunk_text

LLM_SYSTEM = "You are a precise assistant. Follow the instructions exactly and reply with only what is asked."
CHARS_PER_TOKEN = 4
READING_WORDS_PER_MINUTE = 220
DETAIL_MIN_RATIO = 1.5


@dataclass
class Context:
    path: Path
    ext: str
    filename: str
    dry_run: bool
    collection_id: Optional[str] = None
    document_id: Optional[str] = None
    pages: list[str] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)
    layouts: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    paged: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    token_total: int = 0
    chunks: list[Chunk] = field(default_factory=list)
    details: list[tuple[int, Chunk]] = field(default_factory=list)
    vectors: Optional[np.ndarray] = None
    detail_vectors: Optional[np.ndarray] = None
    embed_model: str = ""
    chunk_ids: list[int] = field(default_factory=list)


@dataclass
class Output:
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


def _fill(template: str, **values: object) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _clip(ctx: Context, spec: str) -> tuple[str, bool]:
    limit = providers.input_budget(spec) * CHARS_PER_TOKEN
    return ctx.text[:limit], len(ctx.text) > limit


def _page_bounds(ctx: Context, chunk: Chunk) -> tuple[int, int]:
    return pages.page_of(ctx.starts, chunk.start), pages.page_of(ctx.starts, max(chunk.end - 1, chunk.start))


def run_extract(ctx: Context, settings: dict[str, Any]) -> Output:
    result = extract.extract(
        ctx.path,
        ctx.ext,
        settings["timeout_s"],
        settings["engine"],
        settings["tier"],
        settings["speech_model"],
        settings["audio_timeout_s"],
    )
    ctx.pages, ctx.paged, ctx.layouts = result.pages, result.paged, result.layouts
    ctx.meta.update(result.meta)
    ctx.text, ctx.starts = pages.join(result.pages)
    ctx.token_total = sum(tokens.count_many(result.pages))
    unit = "pages" if result.paged else "sections"
    via = " via LlamaParse" if result.meta.get("extractor") == "llamaparse" else ""
    source = {"audio": f"{media.clock(result.meta.get('duration'))} of speech, ", "image": "text read from an image, "}
    return Output(
        f"{source.get(result.meta.get('media'), '')}{len(result.pages)} {unit}, {len(ctx.text):,} characters, "
        f"{ctx.token_total:,} tokens{via}",
        {
            "pages": len(result.pages),
            "unit": unit,
            "characters": len(ctx.text),
            "tokens": ctx.token_total,
            "preview": ctx.text[:600],
        },
    )


def _details(chunks: list[Chunk], size: int, overlap: int) -> list[tuple[int, Chunk]]:
    details: list[tuple[int, Chunk]] = []
    for index, chunk in enumerate(chunks):
        if chunk.tokens <= size * DETAIL_MIN_RATIO:
            continue
        for part in chunk_text(chunk.text, size, min(overlap, size // 4)):
            details.append((index, Chunk(part.text, chunk.start + part.start, chunk.start + part.end, part.tokens)))
    return details


def run_chunk(ctx: Context, settings: dict[str, Any]) -> Output:
    ctx.chunks = chunk_document(ctx.pages, settings["size"], settings["overlap"], ctx.ext == "csv", settings["within_pages"])
    if not ctx.chunks:
        raise extract.ExtractionError("The document produced no chunks.")
    ctx.details = _details(ctx.chunks, settings["detail_size"], settings["overlap"]) if settings["detail_size"] else []
    sizes = [chunk.tokens for chunk in ctx.chunks]
    samples = [
        {"page": _page_bounds(ctx, chunk)[0], "tokens": chunk.tokens, "text": chunk.text[:300]} for chunk in ctx.chunks[:3]
    ]
    return Output(
        f"{len(ctx.chunks)} chunks, {min(sizes)}-{max(sizes)} tokens each"
        + (f", plus {len(ctx.details)} detail passages" if ctx.details else ""),
        {
            "chunks": len(ctx.chunks),
            "detail_passages": len(ctx.details),
            "tokens": {"min": min(sizes), "average": round(sum(sizes) / len(sizes)), "max": max(sizes)},
            "tokenizer": "exact" if tokens.is_exact() else "approximate",
            "samples": samples,
        },
    )


def run_embed(ctx: Context, settings: dict[str, Any]) -> Output:
    ctx.embed_model = settings["model"]
    texts = [chunk.text for chunk in ctx.chunks] + [detail.text for _, detail in ctx.details]
    vectors = embeddings.embed(ctx.embed_model, texts, settings["batch_size"])
    ctx.vectors, ctx.detail_vectors = vectors[: len(ctx.chunks)], vectors[len(ctx.chunks) :]
    return Output(
        f"{len(vectors)} vectors of {vectors.shape[1]} dimensions",
        {
            "model": ctx.embed_model,
            "dimensions": int(ctx.vectors.shape[1]),
            "vectors": len(vectors),
            "first_vector": [round(float(x), 4) for x in ctx.vectors[0][:8]],
        },
    )


def run_index(ctx: Context, settings: dict[str, Any]) -> Output:
    if ctx.dry_run:
        return Output(
            f"Dry run: {len(ctx.chunks)} chunks would be indexed, nothing was saved", {"saved": False, "chunks": len(ctx.chunks)}
        )
    with db.transaction() as conn:
        store.clear_document(conn, ctx.document_id)
        store.save_pages(conn, ctx.document_id, ctx.pages, ctx.starts, ctx.layouts, ctx.meta.get("media", ""))
        ctx.chunk_ids = []
        for index, (chunk, vector) in enumerate(zip(ctx.chunks, ctx.vectors)):
            first, last = _page_bounds(ctx, chunk)
            row = {
                "idx": index,
                "text": chunk.text,
                "tokens": chunk.tokens,
                "start_char": chunk.start,
                "end_char": chunk.end,
                "page_start": first,
                "page_end": last,
            }
            ctx.chunk_ids.append(store.insert_row(conn, ctx.collection_id, ctx.document_id, row, vector))
        for (parent, detail), vector in zip(ctx.details, ctx.detail_vectors):
            first, last = _page_bounds(ctx, detail)
            row = {
                "kind": "detail",
                "parent_id": ctx.chunk_ids[parent],
                "idx": parent,
                "text": detail.text,
                "tokens": detail.tokens,
                "start_char": detail.start,
                "end_char": detail.end,
                "page_start": first,
                "page_end": last,
            }
            store.insert_row(conn, ctx.collection_id, ctx.document_id, row, vector)
    extra = f" and {len(ctx.details)} detail passages" if ctx.details else ""
    return Output(
        f"Indexed {len(ctx.chunk_ids)} chunks{extra} with vectors and keyword index",
        {"saved": True, "chunks": len(ctx.chunk_ids), "detail_passages": len(ctx.details)},
    )


def run_metadata(ctx: Context, settings: dict[str, Any]) -> Output:
    from py3langid.langid import classify

    first_line = next((line.strip() for line in ctx.text.splitlines() if line.strip()), "")
    guessed = first_line if ctx.ext != "csv" and not ctx.meta.get("media") and len(first_line) <= 120 else ""
    title = ctx.meta.get("title") or guessed or Path(ctx.filename).stem
    language = ctx.meta.get("language") or (classify(ctx.text[:5000])[0] if ctx.text.strip() else "unknown")
    words = len(ctx.text.split())
    ctx.meta.update(
        {
            "title": title,
            "language": language,
            "pages": len(ctx.pages),
            "words": words,
            "sentences": len(re.findall(r"[.!?](?:\s|$)", ctx.text)),
            "reading_minutes": max(1, round(words / READING_WORDS_PER_MINUTE)),
        }
    )
    shown = {key: ctx.meta[key] for key in ("title", "author", "language", "pages", "words") if key in ctx.meta}
    return Output(f"Title, language, pages and words found: {title} ({language}), {len(ctx.pages)} pages, {words:,} words", shown)


def run_abstract(ctx: Context, settings: dict[str, Any]) -> Output:
    spec = settings["llm"]
    body, clipped = _clip(ctx, spec)
    abstract = providers.complete(spec, LLM_SYSTEM, _fill(settings["prompt"], text=body), settings["max_tokens"]).strip()
    if not abstract:
        raise providers.ProviderError("The model returned an empty abstract.")
    ctx.meta["abstract"] = abstract
    if not ctx.dry_run:
        vector = embeddings.embed(ctx.embed_model, [abstract])[0]
        row = {
            "kind": "abstract",
            "idx": -1,
            "text": abstract,
            "tokens": tokens.count(abstract),
            "start_char": 0,
            "end_char": 0,
            "page_start": 1,
            "page_end": 1,
        }
        with db.transaction() as conn:
            store.insert_row(conn, ctx.collection_id, ctx.document_id, row, vector)
    note = " (document was longer than the model input limit, so only the start was used)" if clipped else ""
    return Output(f"Abstract of {len(abstract.split())} words{note}", {"abstract": abstract, "model": spec, "clipped": clipped})


def run_sentiment(ctx: Context, settings: dict[str, Any]) -> Output:
    spec = settings["llm"]
    body, clipped = _clip(ctx, spec)
    raw = providers.complete(spec, LLM_SYSTEM, _fill(settings["prompt"], text=body), settings["max_tokens"]).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        parsed = {}
    label = str(parsed.get("label", "unknown")).lower()
    reason = str(parsed.get("reason", raw[:200]))
    ctx.meta["sentiment"] = {"label": label, "reason": reason}
    return Output(f"{label}: {reason}", {"label": label, "reason": reason, "model": spec, "clipped": clipped})


def _sample(items: list[int], limit: int) -> list[int]:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def run_questions(ctx: Context, settings: dict[str, Any]) -> Output:
    spec = settings["llm"]
    covered = _sample(list(range(len(ctx.chunks))), settings["max_chunks"])
    generated: list[dict[str, Any]] = []
    for index in covered:
        chunk = ctx.chunks[index]
        prompt = _fill(settings["prompt"], text=chunk.text, count=settings["per_chunk"])
        raw = providers.complete(spec, LLM_SYSTEM, prompt, settings["max_tokens"])
        lines = [re.sub(r"^[\s\-*\d.)]+", "", line).strip() for line in raw.splitlines()]
        questions = [line for line in lines if len(line) > 8][: settings["per_chunk"]]
        generated.append({"chunk": index, "page": _page_bounds(ctx, chunk)[0], "questions": questions})
    flat = [(item["chunk"], question) for item in generated for question in item["questions"]]
    if flat and not ctx.dry_run:
        vectors = embeddings.embed(ctx.embed_model, [question for _, question in flat])
        with db.transaction() as conn:
            for (chunk_index, question), vector in zip(flat, vectors):
                chunk = ctx.chunks[chunk_index]
                first, last = _page_bounds(ctx, chunk)
                row = {
                    "kind": "question",
                    "parent_id": ctx.chunk_ids[chunk_index],
                    "idx": chunk_index,
                    "text": question,
                    "tokens": tokens.count(question),
                    "start_char": chunk.start,
                    "end_char": chunk.end,
                    "page_start": first,
                    "page_end": last,
                }
                store.insert_row(conn, ctx.collection_id, ctx.document_id, row, vector)
    return Output(
        f"{len(flat)} questions for {len(covered)} chunks", {"model": spec, "questions": len(flat), "samples": generated[:4]}
    )


RUNNERS: dict[str, Callable[[Context, dict[str, Any]], Output]] = {
    "extract": run_extract,
    "chunk": run_chunk,
    "embed": run_embed,
    "index": run_index,
    "metadata": run_metadata,
    "abstract": run_abstract,
    "sentiment": run_sentiment,
    "questions": run_questions,
}

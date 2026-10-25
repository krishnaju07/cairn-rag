import time
from typing import Any, Iterator, Optional

from .. import db
from ..retrieval import search as retrieval
from ..retrieval.search import Hit
from . import extractive, providers
from .attribution import MARKER, attribute, covers_terms, grounding, specific_terms
from .citations import map_citations
from .intent import is_overview, small_talk
from .prompts import SENTINEL, build_user, label, system_prompt

REFUSAL = "I couldn't find support for that in this collection's documents."
MIN_RELEVANCE = {"semantic": 0.62, "keyword": 0.5, "hybrid": 0.05}
MIN_GROUNDING = 0.5
MIN_ANSWER_WORDS = 3
MAX_ATTEMPTS = 2
RETRY_REASONS = ("too_short",)
OVERVIEW_MODE = "overview"
SNIPPET_CHARS = 280
RETRY_HINT = (
    "\n\nYour previous reply contained no answer text. "
    "Write the answer as a complete sentence, then put the passage number in square brackets."
)


def found_by(hit: Hit) -> str:
    if "overview" in hit.scores:
        return "Opening of the document"
    semantic, keyword = hit.scores.get("semantic"), hit.scores.get("keyword")
    if semantic is not None and keyword is not None:
        return "Exact words and meaning"
    if semantic is not None:
        return "Similar meaning"
    return "Exact words" if keyword is not None else "Related passage"


def snippet(hit: Hit, focus: list[tuple[int, int]] | None) -> str:
    text = " ".join(hit.text[start:end] for start, end in focus) if focus else hit.text
    return " ".join(text.split())[:SNIPPET_CHARS]


def citation(number: int, hit: Hit, focus: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    spans = [[hit.start_char + start, hit.start_char + end] for start, end in focus or []]
    return {
        **hit.as_dict(),
        "n": number,
        "label": label(hit),
        "found_by": found_by(hit),
        "focus": spans,
        "snippet": snippet(hit, focus),
    }


def closest(hits: list[Hit]) -> list[dict[str, Any]]:
    return [citation(number, hit) for number, hit in enumerate(hits[:3], start=1)]


def uses_model(collection: dict[str, Any]) -> bool:
    return providers.parse_spec(collection["answer_model"])[0] != providers.EXTRACTIVE


def recommended(collection: dict[str, Any]) -> dict[str, float]:
    return {mode: (0.0 if uses_model(collection) else MIN_RELEVANCE[mode]) for mode in MIN_RELEVANCE}


def threshold(collection: dict[str, Any], mode: str) -> float:
    override = collection.get("min_relevance")
    return recommended(collection).get(mode, 0.0) if override is None else override


def gate(hits: list[Hit], minimum: float) -> list[Hit]:
    return [hit for hit in hits if hit.kind != "abstract" and hit.relevance >= minimum]


def unanswered(question: str, hits: list[Hit], reason: str) -> dict[str, Any]:
    return {
        "answered": False,
        "answer": REFUSAL,
        "reason": reason,
        "citations": [],
        "closest": closest(hits),
        "question": question,
    }


def finalize(question: str, raw: str, passages: list[Hit], hits: list[Hit], overview: bool) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return unanswered(question, hits, "no_supporting_sentence")
    if SENTINEL in text:
        return unanswered(question, hits, "model_said_insufficient")
    if len(MARKER.sub("", text).split()) < MIN_ANSWER_WORDS:
        return unanswered(question, hits, "too_short")
    attributed = attribute(text, passages)
    mapped = map_citations(attributed.text, len(passages))
    if not mapped.sources:
        return unanswered(question, hits, "not_grounded")
    if grounding(mapped.text, passages) < MIN_GROUNDING:
        return unanswered(question, hits, "not_grounded")
    terms = [] if overview else specific_terms(question)
    if terms and not any(covers_terms(terms, passages[number - 1]) for number in mapped.sources):
        return unanswered(question, hits, "term_mismatch")
    citations = [
        citation(new, passages[original - 1], attributed.focus.get(original))
        for new, original in enumerate(mapped.sources, start=1)
    ]
    return {
        "answered": True,
        "answer": mapped.text,
        "citations": citations,
        "closest": [],
        "question": question,
        "dropped": mapped.dropped,
    }


def retrieve(
    collection: dict[str, Any], question: str, mode: str, top_k: Optional[int]
) -> tuple[str, list[Hit], list[Hit], bool]:
    if is_overview(question):
        hits = retrieval.opening_passages(collection["id"])
        return OVERVIEW_MODE, hits, hits, True
    hits = retrieval.search(collection, question, mode, top_k)
    return mode, hits, gate(hits, threshold(collection, mode)), False


def generate(
    collection: dict[str, Any], question: str, passages: list[Hit], hits: list[Hit], overview: bool
) -> Iterator[dict[str, Any]]:
    spec = collection["answer_model"]
    if providers.parse_spec(spec)[0] == providers.EXTRACTIVE:
        raw = extractive.overview_answer(passages) if overview else extractive.answer(question, passages)
        yield {"type": "delta", "text": raw}
        yield {"type": "result", "result": finalize(question, raw, passages, hits, overview)}
        return
    system, prompt = system_prompt(collection["instructions"]), build_user(question, passages, overview)
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            yield {"type": "reset"}
        pieces: list[str] = []
        for delta in providers.stream(spec, system, prompt + (RETRY_HINT if attempt else ""), collection["max_answer_tokens"]):
            pieces.append(delta)
            yield {"type": "delta", "text": delta}
        result = finalize(question, "".join(pieces), passages, hits, overview)
        if result.get("reason") not in RETRY_REASONS:
            break
    yield {"type": "result", "result": result}


def conversation(question: str, reply: str) -> dict[str, Any]:
    return {"answered": True, "answer": reply, "reason": "conversation", "citations": [], "closest": [], "question": question}


def stream_answer(
    collection: dict[str, Any], question: str, source: str = "ui", mode: Optional[str] = None, top_k: Optional[int] = None
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    mode = mode or collection["retrieval_mode"]
    reply = small_talk(question) if source == "ui" else None
    if reply:
        result = conversation(question, reply)
    else:
        mode, hits, passages, overview = retrieve(collection, question, mode, top_k)
        if not passages:
            result = unanswered(question, hits, "no_relevant_passages")
        else:
            yield {"type": "sources", "citations": [citation(number, hit) for number, hit in enumerate(passages, start=1)]}
            try:
                for event in generate(collection, question, passages, hits, overview):
                    if event["type"] == "result":
                        result = event["result"]
                    else:
                        yield event
            except providers.ProviderError as error:
                yield {"type": "error", "message": str(error)}
                return
    result.update(
        model="" if reply else collection["answer_model"],
        mode=mode,
        latency_ms=round((time.perf_counter() - started) * 1000),
        source=source,
    )
    result["id"] = log_query(collection["id"], result)
    yield {"type": "done", "result": result}


def ask(
    collection: dict[str, Any], question: str, source: str = "api", mode: Optional[str] = None, top_k: Optional[int] = None
) -> dict[str, Any]:
    for event in stream_answer(collection, question, source, mode, top_k):
        if event["type"] == "error":
            raise providers.ProviderError(event["message"])
        if event["type"] == "done":
            return event["result"]
    raise providers.ProviderError("No answer was produced.")


def log_query(collection_id: str, result: dict[str, Any]) -> str:
    query_id = db.new_id("q")
    db.run(
        "INSERT INTO queries (id, collection_id, question, answer, answered, citations, "
        "model, mode, latency_ms, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            query_id,
            collection_id,
            result["question"],
            result["answer"],
            int(result["answered"]),
            db.dumps(result["citations"] or result["closest"]),
            result["model"],
            result["mode"],
            result["latency_ms"],
            result["source"],
            db.now(),
        ),
    )
    return query_id

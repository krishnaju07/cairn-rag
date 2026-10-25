import json
import logging
import time
from typing import Any, Iterator, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import library
from ..generation import answer
from ..retrieval import search as retrieval
from ..retrieval.warmup import status as models_status

router = APIRouter(prefix="/api", tags=["collections"])
log = logging.getLogger("cairn.chat")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)


def search_collection(collection: dict[str, Any], body: SearchBody) -> dict[str, Any]:
    started = time.perf_counter()
    mode = body.mode or collection["retrieval_mode"]
    hits = retrieval.search(collection, body.query, mode, body.top_k)
    return {
        "query": body.query,
        "mode": mode,
        "took_ms": round((time.perf_counter() - started) * 1000),
        "results": [{"rank": rank, **hit.as_dict()} for rank, hit in enumerate(hits, start=1)],
    }


def sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    try:
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"
    except Exception:
        log.exception("Answer stream failed")
        failure = {"type": "error", "message": "Something went wrong while answering. The details are in the server log."}
        yield f"data: {json.dumps(failure)}\n\n"


@router.post("/collections/{collection_id}/chat")
def chat(collection_id: str, body: AskBody) -> StreamingResponse:
    collection = library.get_collection(collection_id)
    events = answer.stream_answer(collection, body.question, "ui", body.mode, body.top_k)
    return StreamingResponse(sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/collections/{collection_id}/search")
def search(collection_id: str, body: SearchBody) -> dict[str, Any]:
    return search_collection(library.get_collection(collection_id), body)


@router.get("/status")
def status() -> dict[str, Any]:
    return {"models": models_status()}

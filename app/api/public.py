from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import keys, library
from ..generation import answer
from .collections import AskBody, SearchBody, search_collection

router = APIRouter(prefix="/v1", tags=["public"])


def authorized_collection(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    scheme, _, secret = (authorization or "").partition(" ")
    key = keys.authenticate(secret.strip()) if scheme.lower() == "bearer" else None
    if key is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API key.", headers={"WWW-Authenticate": "Bearer"})
    return library.get_collection(key["collection_id"])


@router.get("/collection")
def describe(collection: dict[str, Any] = Depends(authorized_collection)) -> dict[str, Any]:
    return {
        key: collection[key] for key in ("id", "name", "description", "documents", "ready", "chunks", "retrieval_mode", "top_k")
    }


@router.post("/search")
def search(body: SearchBody, collection: dict[str, Any] = Depends(authorized_collection)) -> dict[str, Any]:
    return search_collection(collection, body)


@router.post("/query")
def query(body: AskBody, collection: dict[str, Any] = Depends(authorized_collection)) -> dict[str, Any]:
    result = answer.ask(collection, body.question, "api", body.mode, body.top_k)
    keep = ("answered", "answer", "citations", "closest", "model", "mode", "latency_ms")
    return {key: result[key] for key in keep}

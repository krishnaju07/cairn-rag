import re
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .. import db
from . import embeddings
from .rerank import rerank
from .text import query_terms, stem

MODES = ("semantic", "keyword", "hybrid")
CHILD_KINDS = ("question", "detail")
OVERVIEW_PASSAGES = 8
OVERVIEW_DOCUMENTS = 4
OVERVIEW_MIN_PER_DOCUMENT = 2
RRF_K = 60
CANDIDATES = 30
_matrices: dict[str, tuple[tuple, np.ndarray, np.ndarray]] = {}
_matrix_lock = threading.Lock()


@dataclass
class Hit:
    chunk_id: int
    document_id: str
    filename: str
    kind: str
    text: str
    page_start: int
    page_end: int
    start_char: int
    end_char: int
    paged: bool
    relevance: float
    scores: dict[str, Optional[float]] = field(default_factory=dict)
    match: str = ""
    focus: list[tuple[int, int]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "kind": self.kind,
            "text": self.text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "paged": self.paged,
            "focus": [list(span) for span in self.focus],
            "relevance": round(self.relevance, 4),
            "scores": {key: None if value is None else round(value, 4) for key, value in self.scores.items()},
        }


def search(collection: dict, query: str, mode: Optional[str] = None, top_k: Optional[int] = None) -> list[Hit]:
    mode = mode or collection["retrieval_mode"]
    top_k = top_k or collection["top_k"]
    if mode not in MODES:
        raise ValueError(f"Unknown retrieval mode '{mode}'.")
    pool = max(top_k * 4, CANDIDATES)
    semantic = _semantic(collection, query, pool) if mode in ("semantic", "hybrid") else []
    keyword = _keyword(collection["id"], query, pool) if mode in ("keyword", "hybrid") else []
    ranked = _fuse(semantic, keyword) if mode == "hybrid" else (semantic if mode == "semantic" else keyword)
    ranked = ranked[: CANDIDATES if mode == "hybrid" else top_k * 2]
    hits = _resolve(ranked, dict(semantic), dict(keyword), query, mode == "hybrid")
    if mode == "hybrid":
        for hit, probability in zip(hits, rerank(query, [hit.match for hit in hits])):
            hit.scores["rerank"] = probability
            hit.relevance = probability
        hits.sort(key=lambda hit: hit.relevance, reverse=True)
    return hits[:top_k]


def _matrix(collection_id: str) -> tuple[np.ndarray, np.ndarray]:
    signature = db.one(
        "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM chunks WHERE collection_id = ? AND embedding IS NOT NULL",
        (collection_id,),
    )
    key = (signature["n"], signature["m"])
    with _matrix_lock:
        cached = _matrices.get(collection_id)
        if cached and cached[0] == key:
            return cached[1], cached[2]
        return _rebuild(collection_id, key)


def _rebuild(collection_id: str, key: tuple) -> tuple[np.ndarray, np.ndarray]:
    rows = db.many(
        "SELECT id, embedding FROM chunks WHERE collection_id = ? AND embedding IS NOT NULL ORDER BY id", (collection_id,)
    )
    ids = np.array([row["id"] for row in rows], dtype=np.int64)
    matrix = (
        np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]) if rows else np.zeros((0, 0), np.float32)
    )
    _matrices[collection_id] = (key, ids, matrix)
    return ids, matrix


def _semantic(collection: dict, query: str, limit: int) -> list[tuple[int, float]]:
    ids, matrix = _matrix(collection["id"])
    if not len(ids):
        return []
    scores = matrix @ embeddings.embed_query(collection["embed_model"], query)
    order = np.argsort(-scores)[:limit]
    return [(int(ids[i]), float(scores[i])) for i in order]


def _keyword(collection_id: str, query: str, limit: int) -> list[tuple[int, float]]:
    terms = query_terms(query)
    if not terms:
        return []
    match = " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))
    rows = db.many(
        "SELECT rowid AS id, bm25(chunks_fts) AS score FROM chunks_fts "
        "WHERE chunks_fts MATCH ? AND collection_id = ? ORDER BY score LIMIT ?",
        (match, collection_id, limit),
    )
    return [(row["id"], -float(row["score"])) for row in rows]


def _fuse(semantic: list[tuple[int, float]], keyword: list[tuple[int, float]]) -> list[tuple[int, float]]:
    fused: dict[int, float] = {}
    for ranking in (semantic, keyword):
        for rank, (chunk_id, _) in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)


def _coverage(query: str, text: str) -> float:
    terms = {stem(term) for term in query_terms(query)}
    if not terms:
        return 0.0
    body = {stem(word) for word in re.findall(r"[\w']+", text.lower())}
    return len(terms & body) / len(terms)


def _fetch(ids: list[int]) -> dict[int, dict]:
    marks = ",".join("?" * len(ids))
    rows = db.many(
        f"SELECT c.*, d.filename, d.paged FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})",
        tuple(ids),
    )
    return {row["id"]: row for row in rows}


def _to_hit(
    row: dict,
    relevance: float,
    scores: dict[str, Optional[float]],
    match: str = "",
    focus: Optional[list[tuple[int, int]]] = None,
) -> Hit:
    return Hit(
        chunk_id=row["id"],
        document_id=row["document_id"],
        filename=row["filename"],
        kind=row["kind"],
        text=row["text"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        start_char=row["start_char"],
        end_char=row["end_char"],
        paged=bool(row["paged"]),
        relevance=relevance,
        scores=scores,
        match=match or row["text"],
        focus=focus or [],
    )


def opening_passages(collection_id: str, budget: int = OVERVIEW_PASSAGES, max_documents: int = OVERVIEW_DOCUMENTS) -> list[Hit]:
    documents = db.many(
        "SELECT id FROM documents WHERE collection_id = ? AND status = 'ready' ORDER BY created_at LIMIT ?",
        (collection_id, max_documents),
    )
    per_document = max(OVERVIEW_MIN_PER_DOCUMENT, budget // max(len(documents), 1))
    hits: list[Hit] = []
    for document in documents:
        rows = db.many(
            "SELECT c.*, d.filename, d.paged FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.document_id = ? AND c.kind = 'chunk' ORDER BY c.idx LIMIT ?",
            (document["id"], per_document),
        )
        hits.extend(_to_hit(row, 1.0, {"overview": 1.0}) for row in rows)
    return hits[:budget]


def _resolve(
    ranked: list[tuple[int, float]], sem: dict[int, float], key: dict[int, float], query: str, fused_mode: bool
) -> list[Hit]:
    if not ranked:
        return []
    rows = _fetch([chunk_id for chunk_id, _ in ranked])
    parents = [row["parent_id"] for row in rows.values() if row["parent_id"]]
    if parents:
        rows.update({k: v for k, v in _fetch(parents).items() if k not in rows})
    seen, hits = set(), []
    for chunk_id, fused in ranked:
        origin = rows.get(chunk_id)
        if origin is None:
            continue
        target = rows.get(origin["parent_id"], origin) if origin["kind"] in CHILD_KINDS else origin
        if target["id"] in seen:
            continue
        seen.add(target["id"])
        semantic, keyword = sem.get(chunk_id), key.get(chunk_id)
        relevance = semantic if semantic is not None else _coverage(query, target["text"])
        focus = [(origin["start_char"], origin["end_char"])] if origin["kind"] == "detail" else None
        scores = {"semantic": semantic, "keyword": keyword, "fused": fused if fused_mode else None}
        hits.append(_to_hit(target, relevance, scores, origin["text"], focus))
    return hits

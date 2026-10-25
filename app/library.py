import shutil
from pathlib import Path
from typing import Any, Optional

from . import db
from .config import UPLOAD_DIR
from .generation import providers
from .ingestion import jobs, pipelines, staging
from .ingestion.catalog import CATALOG
from .retrieval import store
from .retrieval.search import MODES

TONES = 6
CHUNK_PREVIEW_LIMIT = 200


class LibraryError(Exception):
    pass


def _collection_row(collection_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM collections WHERE id = ?", (collection_id,))
    if not row:
        raise LibraryError("Collection not found.")
    return row


def _check_settings(answer_model: str, retrieval_mode: str, top_k: int) -> None:
    if retrieval_mode not in MODES:
        raise LibraryError(f"Retrieval mode must be one of: {', '.join(MODES)}.")
    if not 1 <= top_k <= 20:
        raise LibraryError("Passages per question must be between 1 and 20.")
    provider_id, _ = providers.parse_spec(answer_model)
    try:
        providers.get(provider_id)
    except providers.ProviderError as error:
        raise LibraryError(str(error)) from error


def create_collection(
    name: str,
    pipeline_id: str,
    version: Optional[int],
    answer_model: str,
    retrieval_mode: str = "hybrid",
    top_k: int = 5,
    description: str = "",
) -> dict[str, Any]:
    _check_settings(answer_model, retrieval_mode, top_k)
    pipeline = pipelines.get(pipeline_id)
    version = version or pipeline["latest_version"]
    if not version:
        raise LibraryError("Publish this pipeline first. Collections use a published version.")
    steps = pipelines.version_steps(pipeline_id, version)
    embed = next((s for s in steps if s["type"] == "embed"), None)
    if embed is None:
        raise LibraryError("This pipeline version has no embedding step.")
    collection_id = db.new_id("col")
    db.run(
        "INSERT INTO collections (id, name, description, pipeline_id, pipeline_version, embed_model, answer_model, "
        "retrieval_mode, top_k, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            collection_id,
            name.strip() or "Untitled collection",
            description,
            pipeline_id,
            version,
            embed["settings"]["model"],
            answer_model,
            retrieval_mode,
            top_k,
            db.now(),
        ),
    )
    return get_collection(collection_id)


def get_collection(collection_id: str) -> dict[str, Any]:
    row = _collection_row(collection_id)
    counts = db.one(
        "SELECT COUNT(*) AS documents, COALESCE(SUM(status = 'ready'), 0) AS ready, "
        "COALESCE(SUM(status IN ('queued', 'processing')), 0) AS busy FROM documents WHERE collection_id = ?",
        (collection_id,),
    )
    pipeline = db.one("SELECT name FROM pipelines WHERE id = ?", (row["pipeline_id"],))
    chunks = db.one("SELECT COUNT(*) AS n FROM chunks WHERE collection_id = ? AND kind = 'chunk'", (collection_id,))["n"]
    tone = sum(ord(character) for character in collection_id) % TONES
    return {**row, "pipeline_name": pipeline["name"], "chunks": chunks, "tone": tone, **counts}


def list_collections() -> list[dict[str, Any]]:
    return [get_collection(row["id"]) for row in db.many("SELECT id FROM collections ORDER BY created_at DESC")]


EDITABLE = (
    "name",
    "description",
    "answer_model",
    "retrieval_mode",
    "top_k",
    "instructions",
    "max_answer_tokens",
    "min_relevance",
)


def update_collection(collection_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    row = _collection_row(collection_id)
    merged = {**row, **{key: changes[key] for key in EDITABLE if key in changes}}
    _check_settings(merged["answer_model"], merged["retrieval_mode"], int(merged["top_k"]))
    if not 100 <= int(merged["max_answer_tokens"]) <= 4000:
        raise LibraryError("Max answer tokens must be between 100 and 4000.")
    if merged["min_relevance"] is not None and not 0 <= float(merged["min_relevance"]) <= 1:
        raise LibraryError("Minimum relevance must be between 0 and 1.")
    db.run(
        "UPDATE collections SET name = ?, description = ?, answer_model = ?, retrieval_mode = ?, top_k = ?, "
        "instructions = ?, max_answer_tokens = ?, min_relevance = ? WHERE id = ?",
        (
            str(merged["name"]).strip() or row["name"],
            merged["description"],
            merged["answer_model"],
            merged["retrieval_mode"],
            int(merged["top_k"]),
            str(merged["instructions"]).strip(),
            int(merged["max_answer_tokens"]),
            None if merged["min_relevance"] is None else float(merged["min_relevance"]),
            collection_id,
        ),
    )
    return get_collection(collection_id)


def set_pipeline_version(collection_id: str, pipeline_id: str, version: int) -> dict[str, Any]:
    collection = _collection_row(collection_id)
    embed = next((s for s in pipelines.version_steps(pipeline_id, version) if s["type"] == "embed"), None)
    if embed is None:
        raise LibraryError("That version has no embedding step.")
    has_documents = db.one("SELECT 1 AS x FROM documents WHERE collection_id = ? LIMIT 1", (collection_id,))
    if has_documents and embed["settings"]["model"] != collection["embed_model"]:
        raise LibraryError(
            f"Existing documents were embedded with {collection['embed_model']}. "
            f"Create a new collection to use {embed['settings']['model']}."
        )
    db.run(
        "UPDATE collections SET pipeline_id = ?, pipeline_version = ?, embed_model = ? WHERE id = ?",
        (pipeline_id, version, embed["settings"]["model"], collection_id),
    )
    return get_collection(collection_id)


def reprocess(collection_id: str, document_id: Optional[str] = None) -> int:
    collection = _collection_row(collection_id)
    steps = pipelines.version_steps(collection["pipeline_id"], collection["pipeline_version"])
    if document_id:
        rows = db.many("SELECT id FROM documents WHERE id = ? AND collection_id = ?", (document_id, collection_id))
    else:
        rows = db.many("SELECT id FROM documents WHERE collection_id = ? AND status IN ('ready', 'failed')", (collection_id,))
    for row in rows:
        db.run("UPDATE documents SET pipeline_version = ? WHERE id = ?", (collection["pipeline_version"], row["id"]))
        jobs.enqueue(row["id"], steps)
    return len(rows)


def delete_collection(collection_id: str) -> None:
    _collection_row(collection_id)
    with db.transaction() as conn:
        store.delete_collection_rows(conn, collection_id)
        conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    shutil.rmtree(UPLOAD_DIR / collection_id, ignore_errors=True)


def _document(row: dict[str, Any]) -> dict[str, Any]:
    steps = db.many("SELECT * FROM doc_steps WHERE document_id = ? ORDER BY position", (row["id"],))
    chunks = db.one("SELECT COUNT(*) AS n FROM chunks WHERE document_id = ? AND kind = 'chunk'", (row["id"],))["n"]
    return {
        "chunks": chunks,
        "progress": sum(1 for s in steps if s["status"] in ("done", "skipped", "failed")),
        **{k: v for k, v in row.items() if k != "path"},
        "meta": db.loads(row["meta"], {}),
        "steps": [{**s, "label": CATALOG[s["step_type"]].label} for s in steps],
    }


def list_documents(collection_id: str, query: str = "", status: str = "") -> list[dict[str, Any]]:
    _collection_row(collection_id)
    rows = db.many("SELECT * FROM documents WHERE collection_id = ? ORDER BY created_at DESC", (collection_id,))
    needle = query.strip().lower()
    rows = [row for row in rows if (not needle or needle in row["filename"].lower()) and (not status or row["status"] == status)]
    return [_document(row) for row in rows]


def document_detail(document_id: str) -> dict[str, Any]:
    document = get_document(document_id)
    chunks = db.many(
        "SELECT id, idx, page_start, page_end, tokens, text FROM chunks "
        "WHERE document_id = ? AND kind = 'chunk' ORDER BY idx LIMIT ?",
        (document_id, CHUNK_PREVIEW_LIMIT),
    )
    total = sum(step["duration_ms"] or 0 for step in document["steps"])
    return {**document, "chunks_list": chunks, "total_ms": total, "collection": get_collection(document["collection_id"])}


def get_document(document_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM documents WHERE id = ?", (document_id,))
    if not row:
        raise LibraryError("Document not found.")
    return _document(row)


def recent_queries(collection_id: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = db.many(
        "SELECT id, question, answer, answered, citations, model, latency_ms, created_at FROM queries "
        "WHERE collection_id = ? AND source = 'ui' ORDER BY created_at DESC LIMIT ?",
        (collection_id, limit),
    )
    for row in rows:
        row["citations"] = db.loads(row["citations"], [])
    return rows[::-1]


def suggestions(collection_id: str) -> list[str]:
    names = [doc["meta"].get("title") or doc["filename"] for doc in list_documents(collection_id) if doc["status"] == "ready"][:2]
    ideas = [f"Summarize {name}" for name in names[:1]]
    ideas += ["What are the key facts, dates or numbers?", "Who or what is mentioned most?"]
    return ideas[:3]


def document_path(document_id: str) -> Path:
    row = db.one("SELECT path FROM documents WHERE id = ?", (document_id,))
    if not row:
        raise LibraryError("Document not found.")
    return Path(row["path"])


def add_document(collection_id: str, stage_id: str, skip_costly: Optional[set[str]] = None) -> dict[str, Any]:
    collection = _collection_row(collection_id)
    staged = staging.load(stage_id)
    steps = pipelines.version_steps(collection["pipeline_id"], collection["pipeline_version"])
    document_id = db.new_id("doc")
    folder = UPLOAD_DIR / collection_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{document_id}.{staged['ext']}"
    shutil.move(str(staged["path"]), target)
    staging.discard(stage_id)
    db.run(
        "INSERT INTO documents (id, collection_id, filename, ext, size_bytes, path, status, pipeline_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
        (
            document_id,
            collection_id,
            staged["filename"],
            staged["ext"],
            staged["size_bytes"],
            str(target),
            collection["pipeline_version"],
            db.now(),
        ),
    )
    jobs.enqueue(document_id, steps, skip_costly)
    return get_document(document_id)


def delete_document(document_id: str) -> None:
    path = document_path(document_id)
    store.delete_document(document_id)
    path.unlink(missing_ok=True)

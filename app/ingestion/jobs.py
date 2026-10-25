from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from .. import db
from ..config import INGEST_WORKERS
from ..retrieval import store
from . import pipelines
from .catalog import CATALOG
from .runner import StepReport, run_pipeline
from .steps import Context

_pool = ThreadPoolExecutor(max_workers=INGEST_WORKERS, thread_name_prefix="ingest")
_skipped: dict[str, set[str]] = {}


def enqueue(document_id: str, steps: list[dict[str, Any]], skip_costly: Optional[set[str]] = None) -> None:
    db.run("DELETE FROM doc_steps WHERE document_id = ?", (document_id,))
    for position, step in enumerate(steps):
        db.run(
            "INSERT INTO doc_steps (document_id, position, step_type, status) VALUES (?, ?, ?, 'pending')",
            (document_id, position, step["type"]),
        )
    db.run("UPDATE documents SET status = 'queued', error = NULL WHERE id = ?", (document_id,))
    _skipped[document_id] = skip_costly or set()
    _pool.submit(_process, document_id)


def resume_interrupted() -> None:
    interrupted = db.many(
        "SELECT d.id, c.pipeline_id, d.pipeline_version FROM documents d "
        "JOIN collections c ON c.id = d.collection_id WHERE d.status IN ('queued', 'processing')"
    )
    for doc in interrupted:
        enqueue(doc["id"], pipelines.version_steps(doc["pipeline_id"], doc["pipeline_version"]))


def _record(document_id: str, report: StepReport) -> None:
    db.run(
        "UPDATE doc_steps SET status = ?, duration_ms = ?, detail = ? WHERE document_id = ? AND position = ?",
        (report.status, report.duration_ms, report.error or report.summary, document_id, report.position),
    )


def _process(document_id: str) -> None:
    doc = db.one("SELECT * FROM documents WHERE id = ?", (document_id,))
    if not doc:
        return
    collection = db.one("SELECT * FROM collections WHERE id = ?", (doc["collection_id"],))
    steps = pipelines.version_steps(collection["pipeline_id"], doc["pipeline_version"])
    db.run("UPDATE documents SET status = 'processing' WHERE id = ?", (document_id,))
    ctx = Context(
        path=Path(doc["path"]),
        ext=doc["ext"],
        filename=doc["filename"],
        dry_run=False,
        collection_id=collection["id"],
        document_id=document_id,
    )
    skipped = _skipped.pop(document_id, set())
    include = {s["id"] for s in steps if CATALOG[s["type"]].costly and s["id"] not in skipped}
    reports = run_pipeline(steps, ctx, include, lambda report: _record(document_id, report))
    fatal = next((r for r in reports if r.status == "failed" and CATALOG[r.type].required), None)
    if fatal:
        with db.transaction() as conn:
            store.clear_document(conn, document_id)
        db.run("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (f"{fatal.label}: {fatal.error}", document_id))
        return
    soft = [f"{r.label}: {r.error}" for r in reports if r.status == "failed"]
    db.run(
        "UPDATE documents SET status = 'ready', error = ?, page_count = ?, paged = ?, token_count = ?, meta = ? WHERE id = ?",
        ("; ".join(soft) or None, len(ctx.pages), int(ctx.paged), ctx.token_total, db.dumps(ctx.meta), document_id),
    )

import re
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup, escape

from ... import db, keys, library
from ...api.collections import SearchBody, search_collection
from ...generation import answer, providers
from ...ingestion import catalog, estimate, pipelines, staging
from ...ingestion.extract import ExtractionError
from ...retrieval.search import MODES
from ...retrieval.text import query_terms
from ..templating import read_limited, render, toast

router = APIRouter(prefix="/collections")

TABS = (
    ("documents", "Documents", "file-text"),
    ("chat", "Chat", "message-square"),
    ("search", "Search", "search"),
    ("processing", "Processing", "workflow"),
    ("access", "Access", "key-round"),
    ("settings", "Settings", "settings-2"),
)
SECTIONS = (("general", "General"), ("search", "Search"), ("answers", "Answers"))
MODE_LABELS = {
    "semantic": ("Meaning", "Passages that mean the same as the question, even in other words."),
    "keyword": ("Exact words", "Passages with the exact words of the question, such as names, codes or terms."),
    "hybrid": ("Meaning and exact words", "Both together, then a local reranker puts the best passages first."),
}
MODE_NAMES = {"semantic": "Semantic", "keyword": "Keyword (BM25)", "hybrid": "Hybrid + rerank"}


def frame(request: Request, collection_id: str, tab: str, **extra: Any) -> HTMLResponse:
    collection = library.get_collection(collection_id)
    return render(
        request,
        "pages/collection.html",
        active=f"collection:{collection_id}",
        collection=collection,
        tab=tab,
        tabs=TABS,
        mode_labels=MODE_LABELS,
        mode_names=MODE_NAMES,
        models=providers.catalog(),
        **extra,
    )


def documents_context(collection_id: str, query: str = "", status: str = "") -> dict[str, Any]:
    documents = library.list_documents(collection_id, query, status)
    busy = any(doc["status"] in ("queued", "processing") for doc in documents)
    return {"documents": documents, "busy": busy, "query": query, "status": status}


@router.get("", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return render(
        request, "pages/collections.html", active="collections", items=library.list_collections(), mode_names=MODE_NAMES
    )


@router.get("/new", response_class=HTMLResponse)
def new_dialog(request: Request) -> HTMLResponse:
    return render(
        request,
        "partials/new_collection.html",
        versions=pipelines.published_versions(),
        models=providers.catalog(),
        default_model=providers.preferred_model(),
    )


@router.post("")
def create(
    name: str = Form(""),
    description: str = Form(""),
    pipeline_version: str = Form(...),
    answer_model: str = Form(...),
) -> RedirectResponse:
    pipeline_id, _, version = pipeline_version.partition(":")
    collection = library.create_collection(name, pipeline_id, int(version), answer_model, "hybrid", 5, description)
    return RedirectResponse(f"/collections/{collection['id']}", status_code=303)


@router.get("/{collection_id}", response_class=HTMLResponse)
def documents_tab(request: Request, collection_id: str) -> HTMLResponse:
    return frame(request, collection_id, "documents", **documents_context(collection_id))


@router.get("/{collection_id}/chat", response_class=HTMLResponse)
def chat_tab(request: Request, collection_id: str) -> HTMLResponse:
    return frame(
        request,
        collection_id,
        "chat",
        history=library.recent_queries(collection_id),
        suggestions=library.suggestions(collection_id),
    )


@router.get("/{collection_id}/search", response_class=HTMLResponse)
def search_tab(request: Request, collection_id: str) -> HTMLResponse:
    return frame(request, collection_id, "search")


@router.get("/{collection_id}/processing", response_class=HTMLResponse)
def processing_tab(request: Request, collection_id: str) -> HTMLResponse:
    return frame(request, collection_id, "processing", **processing_context(collection_id))


def processing_context(collection_id: str) -> dict[str, Any]:
    collection = library.get_collection(collection_id)
    steps = pipelines.version_steps(collection["pipeline_id"], collection["pipeline_version"])
    return {
        "versions": pipelines.published_versions(),
        "steps": [{**s, "definition": catalog.CATALOG[s["type"]], "summary": catalog.summary(s)} for s in steps],
    }


@router.get("/{collection_id}/access", response_class=HTMLResponse)
def access_tab(request: Request, collection_id: str) -> HTMLResponse:
    return frame(request, collection_id, "access", items=keys.list_keys(collection_id), fixed=collection_id)


@router.get("/{collection_id}/settings", response_class=HTMLResponse)
def settings_tab(request: Request, collection_id: str, section: str = "general") -> HTMLResponse:
    section = section if section in dict(SECTIONS) or section == "delete" else "general"
    return frame(
        request,
        collection_id,
        "settings",
        section=section,
        sections=SECTIONS,
        recommended=answer.recommended(library.get_collection(collection_id)),
    )


def _saved(message: str = "Saved") -> Response:
    return toast(Response(status_code=204), message, "success")


def _failed(error: Exception) -> Response:
    return toast(Response(status_code=204), str(error), "error")


@router.post("/{collection_id}/settings/general")
def save_general(collection_id: str, name: str = Form(...), description: str = Form("")) -> Response:
    try:
        library.update_collection(collection_id, {"name": name, "description": description})
    except library.LibraryError as error:
        return _failed(error)
    return _saved("Settings saved")


@router.post("/{collection_id}/settings/search")
def save_search(
    collection_id: str,
    retrieval_mode: str = Form(...),
    top_k: int = Form(...),
    min_relevance: float = Form(0.0),
    use_recommended: str = Form(""),
) -> Response:
    try:
        library.update_collection(
            collection_id,
            {"retrieval_mode": retrieval_mode, "top_k": top_k, "min_relevance": None if use_recommended else min_relevance},
        )
    except library.LibraryError as error:
        return _failed(error)
    return _saved("Search settings saved")


@router.post("/{collection_id}/settings/answers")
def save_answers(
    collection_id: str, answer_model: str = Form(...), instructions: str = Form(""), max_answer_tokens: int = Form(600)
) -> Response:
    try:
        library.update_collection(
            collection_id, {"answer_model": answer_model, "instructions": instructions, "max_answer_tokens": max_answer_tokens}
        )
    except library.LibraryError as error:
        return _failed(error)
    return _saved("Answer settings saved")


@router.post("/{collection_id}/delete")
def delete(collection_id: str) -> Response:
    library.delete_collection(collection_id)
    response = Response(status_code=204)
    response.headers["HX-Redirect"] = "/collections"
    return response


@router.post("/{collection_id}/processing", response_class=HTMLResponse)
def switch_version(request: Request, collection_id: str, pipeline_version: str = Form(...)) -> HTMLResponse:
    pipeline_id, _, version = pipeline_version.partition(":")
    try:
        library.set_pipeline_version(collection_id, pipeline_id, int(version))
        message, kind = "Processing version updated. New uploads use it.", "success"
    except (library.LibraryError, pipelines.PipelineError) as error:
        message, kind = str(error), "error"
    return toast(frame(request, collection_id, "processing", **processing_context(collection_id)), message, kind)


@router.post("/{collection_id}/reprocess", response_class=HTMLResponse)
def reprocess_all(
    request: Request, collection_id: str, redirect: str = Form(""), q: str = Form(""), status: str = Form("")
) -> Response:
    count = library.reprocess(collection_id)
    message = f"Reprocessing {count} document{'' if count == 1 else 's'}"
    if redirect:
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = f"/collections/{collection_id}"
        return toast(response, message, "info")
    response = render(
        request,
        "partials/documents_table.html",
        collection=library.get_collection(collection_id),
        **documents_context(collection_id, q, status),
    )
    return toast(response, message, "info")


@router.get("/{collection_id}/documents/table", response_class=HTMLResponse)
def documents_table(request: Request, collection_id: str, q: str = "", status: str = "") -> HTMLResponse:
    return render(
        request,
        "partials/documents_table.html",
        collection=library.get_collection(collection_id),
        **documents_context(collection_id, q, status),
    )


@router.get("/{collection_id}/upload", response_class=HTMLResponse)
def upload_drawer(request: Request, collection_id: str) -> HTMLResponse:
    collection = library.get_collection(collection_id)
    return render(request, "partials/upload_drawer.html", collection=collection, ids=[])


@router.post("/{collection_id}/documents/stage", response_class=HTMLResponse)
async def stage_documents(request: Request, collection_id: str, files: list[UploadFile] = File(...)) -> HTMLResponse:
    collection = library.get_collection(collection_id)
    steps = pipelines.version_steps(collection["pipeline_id"], collection["pipeline_version"])
    staged, errors, figures = [], [], []
    ocr = next(step for step in steps if step["type"] == "extract")["settings"]["engine"] == "llamaparse"
    for upload in files:
        try:
            record = staging.stage(upload.filename or "upload", await read_limited(upload), ocr)
        except ExtractionError as error:
            errors.append({"filename": upload.filename, "message": str(error)})
            continue
        record["figures"] = estimate.estimate(steps, record["tokens"], record["pages"])
        figures.append(record["figures"])
        staged.append(record)
    combined = estimate.combine(figures) if figures else None
    pdf_pages = sum(record["pages"] for record in staged if record["ext"] == "pdf")
    return render(
        request,
        "partials/upload_ready.html",
        collection=collection,
        staged=staged,
        errors=errors,
        combined=combined,
        steps=len(steps),
        pdf_pages=pdf_pages,
        ids=[],
    )


@router.post("/{collection_id}/documents", response_class=HTMLResponse)
def add_documents(
    request: Request, collection_id: str, stage_ids: list[str] = Form(...), run: list[str] = Form(default=[])
) -> HTMLResponse:
    collection = library.get_collection(collection_id)
    steps = pipelines.version_steps(collection["pipeline_id"], collection["pipeline_version"])
    skipped = {s["id"] for s in steps if catalog.CATALOG[s["type"]].costly and s["id"] not in run}
    created, errors = [], []
    for stage_id in stage_ids:
        try:
            created.append(library.add_document(collection_id, stage_id, skipped)["id"])
        except (library.LibraryError, ExtractionError) as error:
            errors.append(str(error))
    response = render(
        request, "partials/upload_started.html", collection=collection, ids=created, **documents_context(collection_id)
    )
    return toast(response, errors[0], "error") if errors else response


@router.get("/{collection_id}/documents/session", response_class=HTMLResponse)
def upload_session(request: Request, collection_id: str, ids: str = "") -> HTMLResponse:
    wanted = [item for item in ids.split(",") if item]
    documents = [library.get_document(item) for item in wanted if db.one("SELECT 1 AS x FROM documents WHERE id = ?", (item,))]
    busy = any(doc["status"] in ("queued", "processing") for doc in documents)
    return render(
        request,
        "partials/upload_session.html",
        collection=library.get_collection(collection_id),
        documents=documents,
        busy=busy,
        ids=wanted,
    )


@router.get("/{collection_id}/documents/{document_id}", response_class=HTMLResponse)
def document_page(request: Request, collection_id: str, document_id: str) -> HTMLResponse:
    detail = library.document_detail(document_id)
    return render(
        request, "pages/document.html", active=f"collection:{collection_id}", document=detail, collection=detail["collection"]
    )


@router.post("/{collection_id}/documents/{document_id}/reprocess")
def reprocess_document(collection_id: str, document_id: str) -> Response:
    library.reprocess(collection_id, document_id)
    response = toast(Response(status_code=204), "Reprocessing started", "info")
    response.headers["HX-Refresh"] = "true"
    return response


@router.post("/{collection_id}/documents/{document_id}/delete", response_class=HTMLResponse)
def delete_document(request: Request, collection_id: str, document_id: str, redirect: str = Form("")) -> Response:
    library.delete_document(document_id)
    if redirect:
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = f"/collections/{collection_id}"
        return response
    return render(
        request,
        "partials/documents_table.html",
        collection=library.get_collection(collection_id),
        **documents_context(collection_id),
    )


def highlight(text: str, query: str) -> Markup:
    terms = sorted({re.escape(term) for term in query_terms(query)}, key=len, reverse=True)
    if not terms:
        return Markup(escape(text))
    pieces, cursor = [], 0
    for match in re.finditer(rf"\b(?:{'|'.join(terms)})\w*", text, flags=re.I):
        pieces.append(escape(text[cursor : match.start()]))
        pieces.append(Markup("<mark>") + escape(match.group(0)) + Markup("</mark>"))
        cursor = match.end()
    pieces.append(escape(text[cursor:]))
    return Markup("").join(pieces)


@router.post("/{collection_id}/search/run", response_class=HTMLResponse)
def run_search(
    request: Request, collection_id: str, query: str = Form(...), mode: str = Form(...), top_k: int = Form(5)
) -> HTMLResponse:
    collection = library.get_collection(collection_id)
    mode = mode if mode in MODES else collection["retrieval_mode"]
    payload = search_collection(collection, SearchBody(query=query, mode=mode, top_k=top_k))
    return render(
        request,
        "partials/search_results.html",
        collection=collection,
        payload=payload,
        highlight=highlight,
        mode_names=MODE_NAMES,
    )

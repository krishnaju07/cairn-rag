from typing import Any, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from ... import db, keys, library, settings
from ...config import OLLAMA_URL
from ...generation import providers
from ...retrieval.warmup import status as models_status
from ..templating import render, toast

router = APIRouter()


def _stats() -> dict[str, int]:
    return db.one(
        "SELECT (SELECT COUNT(*) FROM pipelines) AS pipelines, (SELECT COUNT(*) FROM collections) AS collections, "
        "(SELECT COUNT(*) FROM documents WHERE status = 'ready') AS documents, (SELECT COUNT(*) FROM queries) AS questions"
    )


def _checklist(stats: dict[str, int]) -> list[dict[str, Any]]:
    published = db.one("SELECT COUNT(*) AS n FROM pipeline_versions")["n"]
    return [
        {
            "label": "Build a pipeline",
            "hint": "Start from the standard template or from scratch.",
            "done": stats["pipelines"] > 0,
            "href": "/pipelines",
        },
        {
            "label": "Test it and publish a version",
            "hint": "Run the draft on one file, then publish.",
            "done": published > 0,
            "href": "/pipelines",
        },
        {
            "label": "Create a collection",
            "hint": "Pick the pipeline version and the model that writes answers.",
            "done": stats["collections"] > 0,
            "href": "/collections",
        },
        {
            "label": "Upload a document",
            "hint": "See the cost estimate and every processing step.",
            "done": stats["documents"] > 0,
            "href": "/collections",
        },
        {
            "label": "Ask a question",
            "hint": "Every claim links back to the page it came from.",
            "done": stats["questions"] > 0,
            "href": "/collections",
        },
    ]


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    stats = _stats()
    return render(
        request,
        "pages/home.html",
        active="home",
        stats=stats,
        checklist=_checklist(stats),
        recent=library.list_collections()[:4],
        models=models_status(),
    )


@router.get("/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    rows = db.many(
        "SELECT q.*, c.name AS collection_name FROM queries q JOIN collections c ON c.id = q.collection_id "
        "ORDER BY q.created_at DESC LIMIT 100"
    )
    for row in rows:
        row["citations"] = db.loads(row["citations"], [])
    return render(request, "pages/history.html", active="history", items=rows)


SECRET_FIELDS = {"nvidia": "NVIDIA_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
PROVIDER_NAMES = {"nvidia": "NVIDIA", "anthropic": "Anthropic", "llamaparse": "LlamaParse"}


def _providers_context() -> dict[str, Any]:
    secrets = {name: settings.mask(settings.get(name)) if settings.get(name) else "" for name in settings.SECRET_NAMES}
    return {
        "catalog": providers.catalog(),
        "models": models_status(),
        "ollama_url": OLLAMA_URL,
        "default_model": providers.preferred_model(),
        "nvidia_checking": providers.get("nvidia").checking(),
        "secrets": secrets,
        "project_id": settings.get("LLAMA_CLOUD_PROJECT_ID"),
    }


def _providers_panel(request: Request, message: Optional[str] = None, kind: str = "success") -> HTMLResponse:
    response = render(request, "partials/providers_panel.html", **_providers_context())
    return toast(response, message, kind) if message else response


@router.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request) -> HTMLResponse:
    return render(request, "pages/providers.html", active="providers", **_providers_context())


@router.post("/providers/test")
def test_provider(llm: str = Form(""), answer_model: str = Form(""), default_model: str = Form("")) -> Response:
    ok, message = providers.test(llm or answer_model or default_model)
    return toast(Response(status_code=204), message, "success" if ok else "error")


@router.get("/providers/panel", response_class=HTMLResponse)
def providers_panel(request: Request) -> HTMLResponse:
    return _providers_panel(request)


@router.post("/providers/nvidia/refresh", response_class=HTMLResponse)
def refresh_nvidia(request: Request) -> HTMLResponse:
    providers.get("nvidia").refresh()
    return _providers_panel(request, "Checking which NVIDIA models are available", "info")


@router.post("/providers/default", response_class=HTMLResponse)
def save_default(request: Request, default_model: str = Form(...)) -> HTMLResponse:
    if not providers.is_ready(default_model):
        return _providers_panel(request, "That model is not available yet. Add its key or start it first.", "error")
    settings.set_default_model(default_model)
    return _providers_panel(request, "Default answer model saved")


@router.post("/providers/llamaparse", response_class=HTMLResponse)
def save_llamaparse(request: Request, key: str = Form(""), project_id: str = Form("")) -> HTMLResponse:
    values = {"LLAMA_CLOUD_PROJECT_ID": project_id.strip()}
    if key.strip():
        values["LLAMA_CLOUD_API_KEY"] = key.strip()
    try:
        settings.save(values)
    except ValueError as error:
        return _providers_panel(request, str(error), "error")
    return _providers_panel(request, "LlamaParse settings saved")


@router.post("/providers/llamaparse/remove", response_class=HTMLResponse)
def remove_llamaparse(request: Request) -> HTMLResponse:
    settings.save({"LLAMA_CLOUD_API_KEY": "", "LLAMA_CLOUD_PROJECT_ID": ""})
    return _providers_panel(request, "LlamaParse key removed")


@router.post("/providers/{provider}/key", response_class=HTMLResponse)
def save_key(request: Request, provider: str, key: str = Form("")) -> HTMLResponse:
    if provider not in SECRET_FIELDS or not key.strip():
        return _providers_panel(request, "Paste a key first.", "error")
    try:
        settings.save({SECRET_FIELDS[provider]: key.strip()})
    except ValueError as error:
        return _providers_panel(request, str(error), "error")
    if provider == "nvidia":
        providers.get("nvidia").refresh()
    return _providers_panel(request, f"{PROVIDER_NAMES[provider]} key saved")


@router.post("/providers/{provider}/key/remove", response_class=HTMLResponse)
def remove_key(request: Request, provider: str) -> HTMLResponse:
    if provider in SECRET_FIELDS:
        settings.save({SECRET_FIELDS[provider]: ""})
    if provider == "nvidia":
        providers.get("nvidia").refresh()
    return _providers_panel(request, f"{PROVIDER_NAMES.get(provider, 'Provider')} key removed")


def _keys_panel(request: Request, fixed: Optional[str], created: Optional[dict[str, Any]] = None) -> HTMLResponse:
    return render(
        request,
        "partials/keys_panel.html",
        items=keys.list_keys(fixed),
        collections=library.list_collections(),
        fixed=fixed,
        created=created,
    )


@router.get("/keys", response_class=HTMLResponse)
def keys_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "pages/keys.html",
        active="keys",
        items=keys.list_keys(),
        collections=library.list_collections(),
        fixed=None,
        created=None,
    )


@router.post("/keys", response_class=HTMLResponse)
def create_key(request: Request, collection: str = Form(...), name: str = Form(""), fixed: str = Form("")) -> HTMLResponse:
    created = keys.create(collection, name)
    return _keys_panel(request, fixed or None, created)


@router.post("/keys/{key_id}/revoke", response_class=HTMLResponse)
def revoke_key(request: Request, key_id: str, fixed: str = Form("")) -> HTMLResponse:
    keys.revoke(key_id)
    return toast(_keys_panel(request, fixed or None), "Key revoked", "success")

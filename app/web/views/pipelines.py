from typing import Any, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ... import db
from ...generation import providers
from ...ingestion import catalog, estimate, pipelines, staging
from ...ingestion.extract import ExtractionError
from ...ingestion.runner import run_pipeline
from ...ingestion.steps import Context
from ..templating import read_limited, render, toast

router = APIRouter(prefix="/pipelines")


def builder(
    request: Request,
    pipeline_id: str,
    selected: Optional[str] = None,
    tab: str = "settings",
    template: str = "partials/builder.html",
    message: Optional[str] = None,
    kind: str = "info",
) -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    ids = [step["id"] for step in pipeline["steps"]]
    selected = selected if selected in ids else (ids[0] if ids else None)
    response = render(
        request, template, pipeline=pipeline, selected=selected, tab=tab, defs=catalog.CATALOG, models=providers.catalog()
    )
    return toast(response, message, kind) if message else response


def _step(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    step = next((s for s in steps if s["id"] == step_id), None)
    if step is None:
        raise pipelines.PipelineError("Step not found.")
    return step


@router.get("", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return render(request, "pages/pipelines.html", active="pipelines", items=pipelines.list_all())


def _preview(template: str, source: Optional[str]) -> list[dict[str, Any]]:
    steps = pipelines.template_steps(template, source)
    return [{"label": catalog.CATALOG[s["type"]].label, "icon": catalog.CATALOG[s["type"]].icon} for s in steps]


@router.get("/new", response_class=HTMLResponse)
def new_page(request: Request, template: str = "standard", source: Optional[str] = None) -> HTMLResponse:
    existing = pipelines.list_all()
    source = source or (existing[0]["id"] if existing else None)
    return render(
        request,
        "pages/pipeline_new.html",
        active="pipelines",
        templates=pipelines.TEMPLATES,
        template=template if template in pipelines.TEMPLATES else "standard",
        existing=existing,
        source=source,
        steps=_preview(template, source),
    )


@router.get("/new/preview", response_class=HTMLResponse)
def new_preview(request: Request, template: str = "standard", source: Optional[str] = None) -> HTMLResponse:
    existing = pipelines.list_all()
    source = source or (existing[0]["id"] if existing else None)
    return render(
        request,
        "partials/new_pipeline_preview.html",
        template=template,
        existing=existing,
        source=source,
        steps=_preview(template, source),
    )


@router.post("")
def create(name: str = Form(""), template: str = Form("standard"), source: Optional[str] = Form(None)) -> RedirectResponse:
    pipeline = pipelines.create(name or pipelines.TEMPLATES.get(template, ("Untitled pipeline",))[0], template, source_id=source)
    return RedirectResponse(f"/pipelines/{pipeline['id']}", status_code=303)


@router.get("/{pipeline_id}", response_class=HTMLResponse)
def page(request: Request, pipeline_id: str, step: Optional[str] = None, tab: str = "settings") -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    ids = [s["id"] for s in pipeline["steps"]]
    selected = step if step in ids else (ids[0] if ids else None)
    return render(
        request,
        "pages/pipeline.html",
        active="pipelines",
        pipeline=pipeline,
        selected=selected,
        tab=tab,
        defs=catalog.CATALOG,
        models=providers.catalog(),
    )


@router.post("/{pipeline_id}/rename")
def rename(pipeline_id: str, name: str = Form(...)) -> Response:
    pipelines.rename(pipeline_id, name)
    return Response(status_code=204)


@router.post("/{pipeline_id}/delete")
def delete(request: Request, pipeline_id: str) -> Response:
    try:
        pipelines.delete(pipeline_id)
    except pipelines.PipelineError as error:
        return toast(Response(status_code=204), str(error), "error")
    response = Response(status_code=204)
    response.headers["HX-Redirect"] = "/pipelines"
    return response


@router.get("/{pipeline_id}/select", response_class=HTMLResponse)
def select(request: Request, pipeline_id: str, step: str, tab: str = "settings") -> HTMLResponse:
    return builder(request, pipeline_id, step, tab, "partials/select.html")


@router.post("/{pipeline_id}/steps", response_class=HTMLResponse)
def add_step(request: Request, pipeline_id: str, type: str = Form(...), at: Optional[int] = Form(None)) -> HTMLResponse:
    steps = pipelines.draft_steps(pipeline_id)
    if type not in catalog.CATALOG:
        return builder(request, pipeline_id, message="Unknown step type.", kind="error")
    slots = catalog.valid_slots(steps, type)
    if not slots:
        reason = next(
            (m for _, m in catalog.order_problems([s["type"] for s in steps] + [type])), "This step cannot be added here."
        )
        return builder(request, pipeline_id, message=reason, kind="error")
    position = at if at in slots else slots[-1]
    step = catalog.new_step(type)
    steps.insert(position, step)
    pipelines.save_draft(pipeline_id, steps)
    return builder(request, pipeline_id, step["id"], message=f"Added {catalog.CATALOG[type].label}", kind="success")


@router.post("/{pipeline_id}/steps/{step_id}/move", response_class=HTMLResponse)
def move_step(request: Request, pipeline_id: str, step_id: str, to: int = Form(...)) -> HTMLResponse:
    steps = pipelines.draft_steps(pipeline_id)
    step = _step(steps, step_id)
    if to not in catalog.valid_slots(steps, step["type"], step_id):
        return builder(request, pipeline_id, step_id, message="The step cannot go there.", kind="error")
    rest = [s for s in steps if s["id"] != step_id]
    rest.insert(to, step)
    pipelines.save_draft(pipeline_id, rest)
    return builder(request, pipeline_id, step_id)


@router.post("/{pipeline_id}/steps/{step_id}/delete", response_class=HTMLResponse)
def delete_step(request: Request, pipeline_id: str, step_id: str) -> HTMLResponse:
    steps = pipelines.draft_steps(pipeline_id)
    step = _step(steps, step_id)
    label = catalog.CATALOG[step["type"]].label
    if catalog.CATALOG[step["type"]].required:
        return builder(request, pipeline_id, step_id, message=f"{label} is required and cannot be removed.", kind="error")
    pipelines.save_draft(pipeline_id, [s for s in steps if s["id"] != step_id])
    return builder(request, pipeline_id, message=f"Removed {label}")


@router.post("/{pipeline_id}/steps/{step_id}/settings", response_class=HTMLResponse)
async def update_settings(request: Request, pipeline_id: str, step_id: str) -> HTMLResponse:
    form = await request.form()
    steps = pipelines.draft_steps(pipeline_id)
    step = _step(steps, step_id)
    raw: dict[str, Any] = {}
    for spec in catalog.CATALOG[step["type"]].fields:
        key = spec["key"]
        raw[key] = (key in form) if spec["type"] == "toggle" else form.get(key, step["settings"].get(key))
    step["settings"] = catalog.clean_settings(step["type"], raw)
    pipelines.save_draft(pipeline_id, steps)
    return builder(request, pipeline_id, step_id, template="partials/settings_saved.html")


@router.post("/{pipeline_id}/publish", response_class=HTMLResponse)
def publish(request: Request, pipeline_id: str, step: Optional[str] = Form(None)) -> HTMLResponse:
    try:
        pipeline = pipelines.publish(pipeline_id)
    except pipelines.PipelineError as error:
        return builder(request, pipeline_id, step, message=str(error), kind="error")
    return builder(request, pipeline_id, step, message=f"Published v{pipeline['latest_version']}", kind="success")


@router.get("/{pipeline_id}/publish", response_class=HTMLResponse)
def publish_dialog(request: Request, pipeline_id: str, step: Optional[str] = None) -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    users = db.one("SELECT COUNT(*) AS n FROM collections WHERE pipeline_id = ?", (pipeline_id,))["n"]
    return render(request, "partials/publish_dialog.html", pipeline=pipeline, users=users, selected=step)


@router.post("/{pipeline_id}/duplicate")
def duplicate(pipeline_id: str) -> Response:
    source = pipelines.get(pipeline_id)
    copy = pipelines.create(f"{source['name']} copy", "copy", source_id=pipeline_id)
    response = Response(status_code=204)
    response.headers["HX-Redirect"] = f"/pipelines/{copy['id']}"
    return response


@router.get("/{pipeline_id}/add", response_class=HTMLResponse)
def add_drawer(request: Request, pipeline_id: str) -> HTMLResponse:
    return render(request, "partials/add_drawer.html", pipeline=pipelines.get(pipeline_id))


@router.get("/{pipeline_id}/versions", response_class=HTMLResponse)
def versions_drawer(request: Request, pipeline_id: str) -> HTMLResponse:
    return render(request, "partials/versions_drawer.html", pipeline=pipelines.get(pipeline_id))


@router.post("/{pipeline_id}/restore/{version}", response_class=HTMLResponse)
def restore(request: Request, pipeline_id: str, version: int) -> HTMLResponse:
    pipelines.restore(pipeline_id, version)
    return builder(request, pipeline_id, message=f"Restored v{version} into the draft", kind="success")


@router.get("/{pipeline_id}/test", response_class=HTMLResponse)
def test_drawer(request: Request, pipeline_id: str) -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    return render(request, "partials/test_drawer.html", pipeline=pipeline)


@router.post("/{pipeline_id}/test/stage", response_class=HTMLResponse)
async def test_stage(request: Request, pipeline_id: str, file: UploadFile = File(...)) -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    draft = pipelines.draft_steps(pipeline_id)
    ocr = any(step["type"] == "extract" and step["settings"]["engine"] == "llamaparse" for step in draft)
    try:
        staged = staging.stage(file.filename or "upload", await read_limited(file), ocr)
    except ExtractionError as error:
        return render(request, "partials/test_ready.html", pipeline=pipeline, error=str(error))
    figures = estimate.estimate(pipelines.draft_steps(pipeline_id), staged["tokens"], staged["pages"])
    return render(request, "partials/test_ready.html", pipeline=pipeline, staged=staged, figures=figures)


@router.post("/{pipeline_id}/test/run", response_class=HTMLResponse)
def test_run(
    request: Request, pipeline_id: str, stage_id: str = Form(...), include: list[str] = Form(default=[])
) -> HTMLResponse:
    pipeline = pipelines.get(pipeline_id)
    if pipeline["problems"]:
        return render(request, "partials/test_results.html", problems=pipeline["problems"], reports=[])
    try:
        staged = staging.load(stage_id)
    except ExtractionError as error:
        return render(request, "partials/test_results.html", problems=[{"message": str(error)}], reports=[])
    ctx = Context(path=staged["path"], ext=staged["ext"], filename=staged["filename"], dry_run=True)
    reports = run_pipeline(pipelines.draft_steps(pipeline_id), ctx, include)
    total = sum(report.duration_ms or 0 for report in reports)
    return render(request, "partials/test_results.html", problems=[], reports=[r.as_dict() for r in reports], total_ms=total)

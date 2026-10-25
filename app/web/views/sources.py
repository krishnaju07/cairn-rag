from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from ... import library, viewer
from ..templating import render

router = APIRouter()


@router.get("/sources/{document_id}", response_class=HTMLResponse)
def source(
    request: Request,
    document_id: str,
    page: int = 1,
    chunk: Optional[int] = None,
    found: str = "",
    n: Optional[int] = None,
    focus: str = "",
) -> HTMLResponse:
    try:
        payload = viewer.view(document_id, page, chunk, focus)
    except (viewer.ViewerError, library.LibraryError) as error:
        return render(request, "partials/source.html", error=str(error))
    return render(request, "partials/source.html", found=found, n=n, **payload)


@router.get("/documents/{document_id}/pages/{page}/image")
def page_image(document_id: str, page: int) -> Response:
    return Response(
        viewer.page_image(document_id, page), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/documents/{document_id}/file")
def original(document_id: str) -> FileResponse:
    document = library.get_document(document_id)
    return FileResponse(library.document_path(document_id), filename=document["filename"])

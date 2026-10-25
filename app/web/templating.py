import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import library
from ..config import MAX_UPLOAD_MB
from ..ingestion.extract import SUPPORTED, ExtractionError
from ..ingestion.media import clock

WEB_DIR = Path(__file__).parent
ICON_DIR = WEB_DIR / "static" / "icons"
templates = Jinja2Templates(directory=WEB_DIR / "templates")


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    source = (ICON_DIR / f"{name}.svg").read_text()
    source = re.sub(r"<!--.*?-->\s*", "", source, flags=re.S)
    source = re.sub(r'\s(?:width|height|class)="[^"]*"', "", source, count=3)
    return source.replace("<svg", "<svg{attrs}", 1)


def icon(name: str, size: int = 16, cls: str = "") -> Markup:
    classes = f"icon {cls}".strip()
    return Markup(_svg(name).replace("{attrs}", f' width="{size}" height="{size}" class="{classes}"'))


def timeago(stamp: Optional[float]) -> str:
    if not stamp:
        return "never"
    seconds = max(time.time() - stamp, 0)
    for limit, unit, size in ((60, "s", 1), (3600, "min", 60), (86400, "h", 3600)):
        if seconds < limit:
            return "just now" if unit == "s" and seconds < 45 else f"{max(int(seconds // size), 1)} {unit} ago"
    return f"{int(seconds // 86400)} d ago"


def filesize(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def duration(milliseconds: Optional[int]) -> str:
    if milliseconds is None:
        return ""
    if milliseconds < 1000:
        return f"{milliseconds} ms"
    if milliseconds < 60_000:
        return f"{milliseconds / 1000:.1f} s"
    return f"{milliseconds / 60_000:.1f} min"


def usd(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "Free"
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


templates.env.globals["icon"] = icon
templates.env.globals["accepted_types"] = ",".join(f".{ext}" for ext in sorted(SUPPORTED))
templates.env.filters.update(timeago=timeago, filesize=filesize, duration=duration, usd=usd, plural=plural, clock=clock)


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    if request.headers.get("HX-Request") != "true" or request.headers.get("HX-Boosted") == "true":
        context.setdefault("sidebar_collections", library.list_collections())
    context.setdefault("active", "")
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def toast(response: HTMLResponse, message: str, kind: str = "info") -> HTMLResponse:
    response.headers["HX-Trigger"] = json.dumps({"toast": {"message": message, "kind": kind}})
    return response


async def read_limited(upload: UploadFile) -> bytes:
    limit = MAX_UPLOAD_MB * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ExtractionError(f"{upload.filename or 'File'} is larger than {MAX_UPLOAD_MB} MB.")
    return data

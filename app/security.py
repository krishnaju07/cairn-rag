import os
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PREFIX = "/v1/"
STATIC_PREFIX = "/static/"
POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
HEADERS = {
    "Content-Security-Policy": POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def allowed_hosts() -> set:
    extra = {item.strip().lower() for item in os.getenv("ALLOWED_HOSTS", "").split(",") if item.strip()}
    return LOCAL_HOSTS | extra


def hostname(value: str) -> str:
    return urlsplit(f"//{value}").hostname or ""


async def protect(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    host = hostname(request.headers.get("host", ""))
    if host not in allowed_hosts() and f"[{host}]" not in allowed_hosts():
        return JSONResponse({"error": "This host is not allowed. Set ALLOWED_HOSTS to add it."}, status_code=421)
    origin = request.headers.get("origin")
    if request.method not in SAFE_METHODS and not request.url.path.startswith(PUBLIC_PREFIX) and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"error": "Cross-site requests are not allowed."}, status_code=403)
    response = await call_next(request)
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith(STATIC_PREFIX):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response

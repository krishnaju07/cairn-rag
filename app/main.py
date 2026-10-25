import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, library, sample
from .api import collections as collections_api
from .api import public as public_api
from .generation.providers import ProviderError
from .ingestion import jobs, staging
from .ingestion.extract import ExtractionError
from .ingestion.pipelines import PipelineError
from .retrieval import warmup
from .security import protect
from .web import router as web_router

STATIC_DIR = Path(__file__).parent / "web" / "static"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("cairn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    staging.purge_old()
    warmup.start()
    jobs.resume_interrupted()
    sample.start()
    yield


app = FastAPI(title="Cairn", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(protect)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(collections_api.router)
app.include_router(public_api.router)
app.include_router(web_router)


@app.exception_handler(library.LibraryError)
@app.exception_handler(PipelineError)
@app.exception_handler(ExtractionError)
@app.exception_handler(ProviderError)
@app.exception_handler(ValueError)
async def domain_error(request: Request, error: Exception) -> JSONResponse:
    status_code = 404 if "not found" in str(error).lower() else 400
    return JSONResponse({"error": str(error)}, status_code=status_code)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Something went wrong on the server. The details are in the server log."}, status_code=500)


@app.get("/healthz", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "database": bool(db.one("SELECT 1 AS ok")), "models": warmup.status()}

from fastapi import APIRouter

from .views import collections, pipelines, sources, workspace

router = APIRouter()
for module in (workspace, pipelines, collections, sources):
    router.include_router(module.router)
